"""End-to-end: prompt 08's stored documents -> prompt 09's reusable bundle.

Runs the real service against a migrated SQLite schema, the real filesystem storage, and real
PDF/DOCX/ZIP bytes, with the OCR engine stubbed only because no Tesseract binary is installed
here (see ``tests/support/stubs.py``). Nothing about the database, the storage keys, the
extraction paths or the status transitions is mocked.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from support.documents import (
    DEADLINE_TABLE,
    ELIGIBILITY_TABLE,
    ENGLISH,
    EVALUATION_MATRIX,
    FRENCH,
    docx_bytes,
    image_pdf,
    mixed_pdf,
    table_pdf,
    text_pdf,
    zip_bytes,
)
from support.stubs import STUB_OCR_TEXT, StubOcrEngine
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories import DocumentRepository, TenderRepository
from tender_intelligence.interfaces.document import DocumentBundle, DocumentProcessor
from tender_intelligence.interfaces.source import TenderListing
from tender_intelligence.processing.service import (
    DocumentProcessingConfig,
    DocumentProcessingService,
    TenderDocumentProcessor,
    bundle_fingerprint,
    detect_format,
)
from tender_intelligence.processing.store import ExtractionStore, bundle_key
from tender_intelligence.storage.local import LocalFileSystemStorage

TENDER_URL = "https://data.wahooas.org/tenders/tenders/detail/T-1"


# --------------------------------------------------------------------------- helpers


def _storage(tmp_path: Path) -> LocalFileSystemStorage:
    return LocalFileSystemStorage(tmp_path / "objects")


def _seed_tender(session_factory: sessionmaker[Session], external_id: str = "T-1") -> int:
    with session_factory() as session:
        source = Source(
            name="wahoo",
            source_type="wahoo",
            base_url="https://data.wahooas.org",
            active=True,
        )
        session.add(source)
        session.flush()
        claim = TenderRepository(session).claim_new(
            source.id,
            TenderListing(external_id=external_id, title="Regional framework", url=TENDER_URL),
            "corr-seed",
        )
        session.commit()
        return claim.tender.id


def _add_document(
    session_factory: sessionmaker[Session],
    storage: LocalFileSystemStorage,
    tender_id: int,
    *,
    filename: str,
    data: bytes | None = None,
    mime_type: str | None = None,
    download_status: str = "downloaded",
    checksum: bool = True,
    store_bytes: bool = True,
) -> int:
    """Seed one document the way prompt 08 leaves it: bytes in storage, row referencing them."""
    digest = hashlib.sha256(data).hexdigest() if (data is not None and checksum) else None
    storage_path = None
    if data is not None:
        storage_path = f"tenders/{tender_id}/attachments/{digest or 'unhashed'}/{filename}"
        if store_bytes:
            storage.put(storage_path, data)

    with session_factory() as session:
        repository = DocumentRepository(session)
        document = repository.create(
            tender_id,
            f"{TENDER_URL}/attachments/{filename}",
            filename,
            mime_type=mime_type,
        )
        session.flush()
        document_id = document.id
        if digest is not None:
            repository.set_checksum(document_id, digest)
        if storage_path is not None:
            repository.set_storage_path(document_id, storage_path)
        if download_status != "pending":
            repository.set_download_status(document_id, download_status)
        session.commit()
        return document_id


def _service(
    session_factory: sessionmaker[Session],
    storage: LocalFileSystemStorage,
    *,
    ocr: StubOcrEngine | None = None,
    config: DocumentProcessingConfig | None = None,
) -> DocumentProcessingService:
    return DocumentProcessingService(
        session_factory, storage=storage, ocr=ocr, config=config
    )


def _row(session_factory: sessionmaker[Session], document_id: int):
    with session_factory() as session:
        document = DocumentRepository(session).get(document_id)
        assert document is not None
        session.expunge(document)
        return document


# --------------------------------------------------------------------------- happy path


def test_bundle_is_built_from_stored_documents(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    pdf_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )
    docx_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="annex.docx",
        data=docx_bytes([FRENCH, EVALUATION_MATRIX]),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.tender_id == tender_id
    assert bundle.document_count == 2
    assert bundle.incomplete_inputs is False
    assert [document.document_id for document in bundle.documents] == [pdf_id, docx_id]

    pdf = bundle.document_by_id(pdf_id)
    assert pdf is not None
    assert pdf.extraction_status == "extracted"
    assert pdf.extraction_method == "native_pdf"
    assert "World Health Organization" in pdf.text
    assert pdf.language == "en"
    assert pdf.language_source == "detected"

    docx = bundle.document_by_id(docx_id)
    assert docx is not None
    assert docx.extraction_method == "docx"
    assert docx.language == "fr"
    assert docx.tables[0].rows == EVALUATION_MATRIX

    assert bundle.languages == ["en", "fr"]


def test_outcomes_are_persisted_on_the_document_rows(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """docs/06 §6.3 — status, method and reference are recorded per document."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )

    _service(session_factory_gr, storage).process_tender(tender_id)

    row = _row(session_factory_gr, document_id)
    assert row.extraction_status == "extracted"
    assert row.extraction_error_code is None
    assert row.language == "en"
    assert row.extracted_text_ref is not None
    assert row.extracted_text_ref.startswith(f"tenders/{tender_id}/extracted/")


def test_extracted_text_ref_points_at_a_readable_artifact(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )

    _service(session_factory_gr, storage).process_tender(tender_id)

    row = _row(session_factory_gr, document_id)
    artifact = json.loads(storage.get(row.extracted_text_ref).decode("utf-8"))
    assert artifact["document_id"] == document_id
    assert artifact["extraction_status"] == "extracted"
    assert "World Health Organization" in artifact["text"]


def test_bundle_is_persisted_at_a_key_derived_from_the_tender_id(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§13 — later stages retrieve the bundle without a new database column."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )

    _service(session_factory_gr, storage).process_tender(tender_id)

    key = bundle_key(tender_id)
    assert storage.exists(key)
    payload = json.loads(storage.get(key).decode("utf-8"))
    assert payload["schema_version"] == 1
    assert payload["document_count"] == 1
    assert payload["incomplete_inputs"] is False
    assert payload["languages"] == ["en"]
    assert payload["documents"][0]["extraction_status"] == "extracted"


def test_tables_reach_the_bundle_with_their_location(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§9 — the evaluation matrix a verdict stage depends on survives processing."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="criteria.pdf",
        data=table_pdf(EVALUATION_MATRIX, caption="Evaluation criteria"),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    table = document.tables[0]
    assert table.rows == EVALUATION_MATRIX
    assert table.location == "page:1"
    assert table.page == 1


def test_a_tender_with_no_documents_produces_an_empty_complete_bundle(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.document_count == 0
    assert bundle.incomplete_inputs is False
    assert bundle.extracted_documents == []
    assert storage.exists(bundle_key(tender_id))


def test_the_three_tender_table_shapes_survive_into_the_bundle(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§20 — deadline, eligibility and evaluation tables, as real ruled PDF tables."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    shapes = {
        "deadline.pdf": DEADLINE_TABLE,
        "eligibility.pdf": ELIGIBILITY_TABLE,
        "evaluation.pdf": EVALUATION_MATRIX,
    }
    ids = {
        name: _add_document(
            session_factory_gr,
            storage,
            tender_id,
            filename=name,
            data=table_pdf(rows, caption=f"{name} table"),
        )
        for name, rows in shapes.items()
    }

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    for name, rows in shapes.items():
        document = bundle.document_by_id(ids[name])
        assert document is not None, name
        assert document.extraction_status == "extracted", name
        assert any(table.rows == rows for table in document.tables), name
        # Row and column counts survive, not just the text.
        table = next(table for table in document.tables if table.rows == rows)
        assert table.row_count == len(rows)
        assert table.column_count == len(rows[0])
        assert table.headers == rows[0]


def test_a_docx_table_keeps_its_rows_and_columns(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="eligibility.docx",
        data=docx_bytes([ENGLISH, ELIGIBILITY_TABLE]),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    table = document.tables[0]
    assert table.rows == ELIGIBILITY_TABLE
    assert (table.row_count, table.column_count) == (4, 3)
    assert table.location == "section:1", "a DOCX table names its section, not a fake page"


# --------------------------------------------------------------------------- scanned PDF / OCR


def test_scanned_pdf_is_ocrd_and_recorded_as_such(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX", 2)
    )
    ocr = StubOcrEngine()

    bundle = _service(session_factory_gr, storage, ocr=ocr).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "extracted"
    assert document.extraction_method == "ocr"
    assert document.ocr_page_count == 2
    assert [page.number for page in document.pages] == [1, 2]
    assert document.metadata.ocr_engine == "stub-ocr"
    assert document.metadata.ocr_engine_version == "9.9.9-test"
    assert STUB_OCR_TEXT.strip() in document.text
    assert ocr.calls == 2


def test_scanned_pdf_without_an_ocr_engine_fails_with_ocr_failed(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§16/§20 — no OCR engine is a recorded per-document failure, not silent emptiness."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf()
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "failed"
    assert document.error_code == "ocr_failed"
    assert document.text == ""
    assert bundle.incomplete_inputs is True

    row = _row(session_factory_gr, document_id)
    assert row.extraction_status == "failed"
    assert row.extraction_error_code == "ocr_failed"


def test_ocr_engine_fault_is_a_recorded_failure_not_a_crash(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf()
    )

    bundle = _service(
        session_factory_gr, storage, ocr=StubOcrEngine(fail=True)
    ).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "failed"
    assert document.error_code == "ocr_failed"


def test_mixed_document_ocrs_only_its_scanned_pages(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="rfp.pdf",
        data=mixed_pdf(native_pages=1, scanned_pages=1),
    )
    ocr = StubOcrEngine()

    bundle = _service(session_factory_gr, storage, ocr=ocr).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert [page.method for page in document.pages] == ["native_pdf", "ocr"]
    assert ocr.calls == 1
    assert "World Health Organization" in document.pages[0].text
    assert document.extraction_method == "ocr"


# --------------------------------------------------------------------------- failure semantics


@pytest.mark.parametrize(
    ("filename", "data", "expected_status", "expected_error"),
    [
        ("broken.pdf", b"not really a pdf", "failed", "parse_failed"),
        ("broken.docx", b"not really a docx", "failed", "parse_failed"),
    ],
)
def test_declared_format_that_cannot_be_parsed_is_a_failure(
    session_factory_gr: sessionmaker[Session],
    tmp_path: Path,
    filename: str,
    data: bytes,
    expected_status: str,
    expected_error: str,
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename=filename, data=data
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == expected_status
    assert document.error_code == expected_error
    assert bundle.incomplete_inputs is True


def test_download_failure_makes_the_inputs_incomplete(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """docs/06 §6.5 — a document that failed to download is a known gap in the inputs."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="missing.pdf",
        data=None,
        download_status="failed",
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "skipped"
    assert document.error_code == "document_download_failed"
    assert document.metadata.skip_reason == "acquisition_failed"
    assert bundle.incomplete_inputs is True
    assert document in bundle.skipped_documents


def test_missing_stored_bytes_are_an_explicit_failure_never_a_re_download(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§3 — the source URL is not re-fetched; the document is recorded as failed."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="gone.pdf",
        data=text_pdf([ENGLISH]),
        store_bytes=False,
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "failed"
    assert document.error_code == "document_download_failed"
    assert document.metadata.skip_reason == "source_bytes_unavailable"
    assert document.source_url == f"{TENDER_URL}/attachments/gone.pdf"
    assert bundle.incomplete_inputs is True


def test_a_row_without_a_storage_reference_is_a_failure(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    with session_factory_gr() as session:
        document = DocumentRepository(session).create(
            tender_id, f"{TENDER_URL}/attachments/x.pdf", "x.pdf", mime_type="application/pdf"
        )
        session.flush()
        document_id = document.id
        DocumentRepository(session).set_download_status(document_id, "downloaded")
        session.commit()

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "failed"
    assert document.metadata.skip_reason == "storage_reference_missing"
    assert bundle.incomplete_inputs is True


def test_archive_container_is_skipped_because_its_members_are_separate_rows(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§7 — a ZIP gets its own row in prompt 08; its members carry their own provenance."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    archive_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="annexes.zip",
        data=zip_bytes({"a.pdf": text_pdf([ENGLISH])}),
        mime_type="application/zip",
    )
    member_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="a.pdf",
        data=text_pdf([ENGLISH]),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    archive = bundle.document_by_id(archive_id)
    assert archive is not None
    assert archive.extraction_status == "skipped"
    assert archive.metadata.skip_reason == "archive_container"
    assert archive.text == ""

    member = bundle.document_by_id(member_id)
    assert member is not None
    assert member.extraction_status == "extracted"
    assert bundle.incomplete_inputs is False


def test_unsupported_format_is_skipped_without_marking_inputs_incomplete(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """docs/13-open-decisions.md O22 — the documented behaviour, not a silent choice.

    A spreadsheet annex is not something the specification requires extracting, so it is recorded
    as skipped (visible in ``skipped_documents``) rather than making the bundle look partial.
    """
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="PNG")

    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="annex.png",
        data=buffer.getvalue(),
        mime_type="image/png",
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "skipped"
    assert document.metadata.skip_reason == "unsupported_format"
    assert document.error_code is None
    assert bundle.incomplete_inputs is False
    assert document in bundle.skipped_documents, "a skipped document is still visible"


def test_the_five_document_mixed_batch_from_the_spec(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§20 — the exact fixture shape: A,B,D,E valid; C corrupt; inputs reported incomplete.

    This is the minimal form of the partial-failure requirement, kept separate from the wider
    mixed batch below so the specification's own fixture is verifiable literally.
    """
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    ids = [
        _add_document(
            session_factory_gr,
            storage,
            tender_id,
            filename=f"{name}.pdf",
            data=data,
        )
        for name, data in [
            ("a", text_pdf([ENGLISH])),
            ("b", text_pdf([FRENCH])),
            ("c", b"%PDF-1.7 this body is corrupt and cannot be parsed"),
            ("d", docx_bytes([ENGLISH])),
            ("e", table_pdf(DEADLINE_TABLE)),
        ]
    ]

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)
    statuses = [bundle.document_by_id(document_id).extraction_status for document_id in ids]

    assert statuses == ["extracted", "extracted", "failed", "extracted", "extracted"]
    assert bundle.document_count == 5
    assert bundle.incomplete_inputs is True, "a corrupt document makes the inputs incomplete"

    corrupt = bundle.document_by_id(ids[2])
    assert corrupt is not None
    assert corrupt.error_code in {"parse_failed", "ocr_failed"}
    assert bundle.failed_documents == [corrupt]


def test_one_bad_document_never_aborts_the_others(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§16 — the whole batch, with every failure mode present at once."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    good_first = _add_document(
        session_factory_gr, storage, tender_id, filename="a-good.pdf", data=text_pdf([ENGLISH])
    )
    corrupt = _add_document(
        session_factory_gr, storage, tender_id, filename="b-corrupt.pdf", data=b"garbage bytes"
    )
    failed_download = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="c-failed.pdf",
        data=None,
        download_status="failed",
    )
    archive = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="d-archive.zip",
        data=zip_bytes({"x.pdf": b"x"}),
    )
    gone = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="e-gone.pdf",
        data=text_pdf([FRENCH]),
        store_bytes=False,
    )
    good_last = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="f-good.docx",
        data=docx_bytes([DEADLINE_TABLE]),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.document_count == 6
    assert bundle.incomplete_inputs is True

    expected = {
        good_first: ("extracted", None),
        corrupt: ("failed", "parse_failed"),
        failed_download: ("skipped", "document_download_failed"),
        archive: ("skipped", None),
        gone: ("failed", "document_download_failed"),
        good_last: ("extracted", None),
    }
    for document_id, (status, error) in expected.items():
        document = bundle.document_by_id(document_id)
        assert document is not None, f"document {document_id} vanished from the bundle"
        assert document.extraction_status == status, document_id
        assert document.error_code == error, document_id

    # Every row was updated, including the ones that produced nothing, and the documents that
    # could be extracted were extracted despite the failures around them.
    for document_id, (status, error) in expected.items():
        row = _row(session_factory_gr, document_id)
        assert row.extraction_status == status
        assert row.extraction_error_code == error

    assert len(bundle.extracted_documents) == 2
    assert len(bundle.failed_documents) == 2
    assert len(bundle.skipped_documents) == 2
    last = bundle.document_by_id(good_last)
    assert last is not None and last.tables[0].rows == DEADLINE_TABLE


def test_every_failing_document_still_gets_a_persisted_artifact(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§16 — a failure is recorded, not hidden; the row's reference agrees with the artifact."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="bad.pdf", data=b"garbage"
    )

    _service(session_factory_gr, storage).process_tender(tender_id)

    row = _row(session_factory_gr, document_id)
    assert row.extracted_text_ref is not None
    artifact = json.loads(storage.get(row.extracted_text_ref).decode("utf-8"))
    assert artifact["extraction_status"] == "failed"
    assert artifact["error_code"] == "parse_failed"


# --------------------------------------------------------------------------- reuse


def test_rerun_reuses_extractions_without_reprocessing(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§14 — the second run must not re-OCR anything it already has."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )
    ocr = StubOcrEngine()
    service = _service(session_factory_gr, storage, ocr=ocr)

    first = service.process_tender(tender_id)
    assert ocr.calls == 1

    second = service.process_tender(tender_id)
    assert ocr.calls == 1, "the cached extraction must be reused, not recomputed"
    assert second.extracted_documents[0].text == first.extracted_documents[0].text
    assert bundle_fingerprint(second) == bundle_fingerprint(first)


def test_rerun_does_not_rewrite_the_artifact(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )
    service = _service(session_factory_gr, storage)

    service.process_tender(tender_id)
    ref = _row(session_factory_gr, document_id).extracted_text_ref
    before = storage.get(ref)

    service.process_tender(tender_id)

    assert _row(session_factory_gr, document_id).extracted_text_ref == ref
    assert storage.get(ref) == before, "reuse must not churn stored artifacts"


def test_a_processor_version_change_invalidates_reuse(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§15 — a processor fix must reach already-processed documents."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )
    ocr = StubOcrEngine()
    service = _service(session_factory_gr, storage, ocr=ocr)
    service.process_tender(tender_id)
    assert ocr.calls == 1

    monkeypatch.setattr(
        "tender_intelligence.processing.store.PROCESSOR_VERSION", "0.0.0-older"
    )
    service.process_tender(tender_id)
    assert ocr.calls == 2, "a processor version change must force reprocessing"


def test_a_config_version_change_invalidates_reuse(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )
    ocr = StubOcrEngine()

    _service(session_factory_gr, storage, ocr=ocr).process_tender(tender_id)
    assert ocr.calls == 1

    _service(
        session_factory_gr,
        storage,
        ocr=ocr,
        config=DocumentProcessingConfig(config_version="2"),
    ).process_tender(tender_id)
    assert ocr.calls == 2, "a changed extraction configuration must force reprocessing"


def test_reuse_can_be_disabled(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )
    ocr = StubOcrEngine()
    service = _service(
        session_factory_gr,
        storage,
        ocr=ocr,
        config=DocumentProcessingConfig(reuse_extractions=False),
    )

    service.process_tender(tender_id)
    service.process_tender(tender_id)
    assert ocr.calls == 2


def test_a_failed_extraction_is_retried_on_the_next_run(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """A transient failure must not be cached: only successful extractions are reusable."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )

    first = _service(session_factory_gr, storage).process_tender(tender_id)
    assert first.document_by_id(document_id).extraction_status == "failed"

    ocr = StubOcrEngine()
    second = _service(session_factory_gr, storage, ocr=ocr).process_tender(tender_id)

    document = second.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "extracted"
    assert ocr.calls == 1
    assert second.incomplete_inputs is False
    row = _row(session_factory_gr, document_id)
    assert row.extraction_status == "extracted"
    assert row.extraction_error_code is None


def test_processing_is_deterministic_across_runs(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§14 — repeat runs over unchanged inputs are materially identical."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="a.pdf", data=text_pdf([ENGLISH])
    )
    _add_document(
        session_factory_gr, storage, tender_id, filename="b.docx", data=docx_bytes([FRENCH])
    )
    service = _service(
        session_factory_gr, storage, config=DocumentProcessingConfig(reuse_extractions=False)
    )

    first = service.process_tender(tender_id)
    second = service.process_tender(tender_id)

    assert bundle_fingerprint(first) == bundle_fingerprint(second)
    assert [d.content_fingerprint for d in first.documents] == [
        d.content_fingerprint for d in second.documents
    ]


# --------------------------------------------------------------------------- metadata


def test_correlation_id_propagates_into_the_bundle_and_its_artifacts(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )

    bundle = _service(session_factory_gr, storage).process_tender(
        tender_id, correlation_id="run-42"
    )

    assert bundle.metadata.correlation_id == "run-42"
    document = bundle.document_by_id(document_id)
    assert document is not None
    assert document.metadata.correlation_id == "run-42"

    row = _row(session_factory_gr, document_id)
    artifact = json.loads(storage.get(row.extracted_text_ref).decode("utf-8"))
    assert artifact["metadata"]["correlation_id"] == "run-42"


def test_a_correlation_id_is_created_when_none_is_supplied(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.metadata.correlation_id


def test_processing_metadata_records_versions_for_reproducibility(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§15 — versions are recorded as evidence, so a later change can be explained."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    document = bundle.document_by_id(document_id)
    assert document is not None
    metadata = document.metadata
    assert metadata.processor == "TenderDocumentProcessor"
    assert metadata.processor_version
    assert metadata.config_version == "1"
    assert metadata.processed_at
    assert set(metadata.library_versions) >= {"pymupdf", "python-docx", "pillow", "pytesseract"}


def test_multi_language_tender_reports_its_languages(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="en.pdf", data=text_pdf([ENGLISH])
    )
    _add_document(
        session_factory_gr, storage, tender_id, filename="fr.pdf", data=text_pdf([FRENCH])
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.languages == ["en", "fr"]


# --------------------------------------------------------------------------- next-stage seam


def test_bundle_is_consumable_without_re_reading_the_source_documents(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§22 — what a later stage needs, retrievable from the persisted bundle alone.

    This is the seam prompt 09 exists for: a triage or verdict stage must be able to answer
    "what does this tender say?" from the bundle, with no re-download and no re-OCR.
    """
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="en.pdf", data=text_pdf([ENGLISH])
    )
    _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="criteria.pdf",
        data=table_pdf(EVALUATION_MATRIX, caption="Evaluation criteria"),
    )
    _add_document(
        session_factory_gr, storage, tender_id, filename="annex.docx", data=docx_bytes([FRENCH])
    )
    _service(session_factory_gr, storage).process_tender(tender_id)

    # A later stage reads the bundle from storage, exactly as it would in the pipeline.
    payload = json.loads(storage.get(bundle_key(tender_id)).decode("utf-8"))
    documents = payload["documents"]

    assert len(documents) == 3
    for document in documents:
        assert document["extraction_status"] == "extracted"
        assert document["text"].strip(), "every extracted document has usable text"
        assert document["content_fingerprint"]
        assert document["metadata"]["processor_version"]

    tables = [table for document in documents for table in _tables(document)]
    assert any(table["rows"] == EVALUATION_MATRIX for table in tables)
    assert all(table["location"] for table in tables)
    assert set(payload["languages"]) >= {"en", "fr"}


def _tables(document: dict) -> list[dict]:
    collected = [table for page in document["pages"] for table in page["tables"]]
    collected.extend(table for section in document["sections"] for table in section["tables"])
    return collected


def test_reprocessing_a_tender_does_not_duplicate_artifacts(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§14 — no uncontrolled artifact growth across runs."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )
    service = _service(
        session_factory_gr, storage, config=DocumentProcessingConfig(reuse_extractions=False)
    )

    service.process_tender(tender_id)
    first = storage.list_keys(f"tenders/{tender_id}/extracted")
    service.process_tender(tender_id)
    assert storage.list_keys(f"tenders/{tender_id}/extracted") == first


def test_the_persisted_bundle_reloads_without_reprocessing(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """§22 — the whole chain, ending in a reload that touches neither storage bytes nor OCR.

    stored Document -> storage read -> processor -> row metadata -> artifact -> bundle -> reload.
    A fresh ``ExtractionStore`` stands in for the later stage: if the reload needs anything the
    pipeline did not persist, this fails.
    """
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    document_id = _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", data=text_pdf([ENGLISH])
    )
    ocr = StubOcrEngine()

    produced = _service(session_factory_gr, storage, ocr=ocr).process_tender(tender_id)

    # Verified on the row: the extraction metadata reached the database (docs/06 §6.3).
    row = _row(session_factory_gr, document_id)
    assert row.extraction_status == "extracted"
    assert row.extracted_text_ref is not None

    # Verified through a fresh reader: no rerun, no acquisition, no extraction.
    reloaded = ExtractionStore(storage).read_bundle(tender_id)
    assert reloaded is not None
    assert bundle_fingerprint(reloaded) == bundle_fingerprint(produced)

    document = reloaded.document_by_id(document_id)
    assert document is not None
    assert document.extraction_status == "extracted"
    assert "World Health Organization" in document.text
    assert document.pages[0].text.strip()
    assert document.metadata.processor_version == produced.metadata.processor_version


# --------------------------------------------------------------------------- the seam itself


def test_tender_document_processor_satisfies_the_document_processor_seam(
    tmp_path: Path,
) -> None:
    """docs/02 §2.10 — the interface tests already pin this contract; the impl must match it."""
    storage = _storage(tmp_path)
    storage.put("tenders/1/attachments/abc/tor.pdf", text_pdf([ENGLISH]))

    processor = TenderDocumentProcessor(storage=storage)
    assert isinstance(processor, DocumentProcessor)

    bundle = processor.process(["tenders/1/attachments/abc/tor.pdf"])

    assert isinstance(bundle, DocumentBundle)
    assert bundle.incomplete_inputs is False
    assert len(bundle.documents) == 1
    extracted = bundle.documents[0]
    assert extracted.filename == "tor.pdf"
    assert extracted.extraction_method == "native_pdf"
    assert "World Health Organization" in extracted.text
    assert extracted.language == "en"
    assert extracted.pages and extracted.pages[0].strip()
    assert extracted.error_code is None


def test_document_processor_records_a_missing_key_instead_of_raising(tmp_path: Path) -> None:
    """docs/06 §6.4 — per-document failures are recorded on the document, not raised."""
    processor = TenderDocumentProcessor(storage=_storage(tmp_path))

    bundle = processor.process(["tenders/1/attachments/abc/absent.pdf"])

    assert bundle.incomplete_inputs is True
    assert bundle.documents[0].error_code == "document_download_failed"
    assert bundle.failed_documents


def test_document_processor_reports_tables_on_the_interface_shape(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage.put(
        "tenders/1/attachments/abc/criteria.pdf",
        table_pdf(EVALUATION_MATRIX, caption="Evaluation"),
    )

    bundle = TenderDocumentProcessor(storage=storage).process(
        ["tenders/1/attachments/abc/criteria.pdf"]
    )

    table = bundle.documents[0].tables[0]
    assert table["rows"] == EVALUATION_MATRIX
    assert table["location"] == "page:1"


# --------------------------------------------------------------------------- security


def test_logs_never_contain_document_content(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """§18 — never write raw document contents into logs, including OCR output."""
    secret = "CONFIDENTIAL PRICING SCHEDULE FOR THE MINISTRY OF HEALTH"
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="secret.pdf", data=text_pdf([secret])
    )
    _add_document(
        session_factory_gr, storage, tender_id, filename="scan.pdf", data=image_pdf("ANNEX")
    )
    _add_document(
        session_factory_gr, storage, tender_id, filename="bad.pdf", data=b"CORRUPT SECRET BYTES"
    )

    with caplog.at_level(logging.DEBUG):
        _service(session_factory_gr, storage, ocr=StubOcrEngine()).process_tender(tender_id)

    logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert secret not in logged
    assert "CORRUPT SECRET BYTES" not in logged
    assert STUB_OCR_TEXT.strip() not in logged


# --------------------------------------------------------------------------- format detection


@pytest.mark.parametrize(
    ("filename", "mime_type", "data", "expected"),
    [
        ("tor.pdf", "application/pdf", b"%PDF-1.7 something", "pdf"),
        ("tor.pdf", None, b"%PDF-1.7 something", "pdf"),
        ("annex.docx", None, b"PK\x03\x04rest", "docx"),
        ("annexes.zip", "application/zip", b"PK\x03\x04rest", "archive"),
        ("unknown.zip", None, b"PK\x03\x04rest", "archive"),
        (".doc", None, b"\xd0\xcf\x11\xe0legacy", "unknown"),
        ("annex.png", "image/png", b"\x89PNG\r\n\x1a\n", "unknown"),
        ("notes.txt", "text/plain", b"plain text", "unknown"),
    ],
)
def test_format_detection_prefers_bytes_over_declaration(
    filename: str, mime_type: str | None, data: bytes, expected: str
) -> None:
    assert detect_format(mime_type=mime_type, filename=filename, data=data) == expected


def test_a_pdf_named_docx_is_treated_as_a_pdf() -> None:
    """Content wins: a wrong declaration must not route a file to the wrong extractor."""
    assert detect_format(filename="tor.docx", data=b"%PDF-1.7") == "pdf"


def test_a_docx_served_under_a_pdf_name_is_read_as_a_docx() -> None:
    """The mirror of the case above, and the one a ZIP/OOXML container actually hits.

    Sources serve attachments under names that do not match their contents. A WordprocessingML
    package named ``tor.pdf`` is a ZIP, so sniffing only the magic bytes would fall through to the
    archive branch and the document would be *skipped* as a container whose members are already
    separate rows — silently dropping a document that holds real content. The archive's own
    contents settle it.
    """
    package = docx_bytes([ENGLISH])
    assert detect_format(filename="tor.pdf", mime_type="application/pdf", data=package) == "docx"
    assert detect_format(filename="", data=package) == "docx", "no name or MIME at all"


def test_a_zip_that_is_not_an_ooxml_package_stays_an_archive() -> None:
    """The OOXML sniff must not swallow every ZIP — plain archives are still containers."""
    archive = zip_bytes({"readme.txt": b"nothing to see"})
    assert detect_format(filename="annexes.zip", data=archive) == "archive"


def test_a_malformed_docx_still_routes_to_the_docx_reader() -> None:
    """A container that cannot be opened is not evidence of anything; the declaration decides.

    Routing it to the archive branch instead would skip it, and §20 requires a declared DOCX that
    cannot be parsed to be reported as a failure rather than quietly set aside.
    """
    assert detect_format(filename="tor.docx", data=b"PK\x03\x04not a real zip") == "docx"

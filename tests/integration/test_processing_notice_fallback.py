"""Stage 09's notice fallback, against a real database, real storage and real bytes.

The rule under test is *when* the published notice is allowed to stand in for the tender
document. It is the difference between a paywalled portal's tenders being assessed on the record
that is public and being reported as ``requirement_sources_unavailable``.

Everything about the database, the storage keys and the extraction paths is real; only the OCR
engine is stubbed, as elsewhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from support.documents import ENGLISH, text_pdf
from support.stubs import StubOcrEngine
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories import DocumentRepository, TenderRepository
from tender_intelligence.interfaces.source import TenderListing
from tender_intelligence.processing.notice import (
    NOTICE_FILENAME,
    NoticeSource,
    is_notice,
)
from tender_intelligence.processing.service import DocumentProcessingService
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.storage.local import LocalFileSystemStorage

TENDER_URL = "https://tenderdetail.test/Global-Tenders/TenderNotice/118902436"


@pytest.fixture
def notice() -> NoticeSource:
    return NoticeSource(
        title="Provision of medical laboratory consumables",
        source_url=TENDER_URL,
        procuring_body="Federal Ministry of Health",
        reference_numbers=("FMOH/LC/2026/07",),
        requirements_hints=(
            "Bidder must hold a current NAFDAC registration",
            "Evidence of three similar supply contracts",
        ),
        scope="Supply of laboratory consumables to twelve federal hospitals.",
        published_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
        deadline_at=datetime(2026, 10, 20, 15, 0, tzinfo=UTC),
        deadline_timezone="WAT",
        language="en",
        body_text="Prospective suppliers are invited to submit sealed bids.",
    )


def _storage(tmp_path: Path) -> LocalFileSystemStorage:
    return LocalFileSystemStorage(tmp_path / "objects")


def _seed_tender(session_factory: sessionmaker[Session], external_id: str = "TD-1") -> int:
    with session_factory() as session:
        source = Source(
            name="tenderdetail_global",
            source_type="tenderdetail_global",
            base_url="https://tenderdetail.test",
            active=True,
        )
        session.add(source)
        session.flush()
        claim = TenderRepository(session).claim_new(
            source.id,
            TenderListing(external_id=external_id, title="Medical consumables", url=TENDER_URL),
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
    download_status: str = "downloaded",
    store_bytes: bool = True,
) -> int:
    import hashlib

    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    storage_path = None
    if data is not None:
        storage_path = f"tenders/{tender_id}/attachments/{digest or 'unhashed'}/{filename}"
        if store_bytes:
            storage.put(storage_path, data)
    with session_factory() as session:
        repository = DocumentRepository(session)
        document = repository.create(
            tender_id, f"{TENDER_URL}/attachments/{filename}", filename
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
    session_factory: sessionmaker[Session], storage: LocalFileSystemStorage
) -> DocumentProcessingService:
    return DocumentProcessingService(session_factory, storage=storage, ocr=StubOcrEngine())


# ------------------------------------------------------------------ nothing was downloaded


def test_notice_stands_in_when_nothing_was_downloaded(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id, notice=notice)

    assert [document.filename for document in bundle.documents] == [NOTICE_FILENAME]
    document = bundle.documents[0]
    assert is_notice(document) is True
    assert document.tender_id == tender_id
    assert document.storage_path is None
    assert "Federal Ministry of Health" in document.text


def test_notice_only_bundle_records_incomplete_inputs(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    """An assessment reached from a summary is partial by construction (docs/06 §6.5)."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id, notice=notice)

    assert bundle.incomplete_inputs is True


def test_notice_only_bundle_is_persisted_and_reloadable(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    # Stage B reads the bundle back from the store, not from the object it was handed, so an
    # unpersisted fallback would be invisible to the stage it exists for.
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    service = _service(session_factory_gr, storage)

    produced = service.process_tender(tender_id, notice=notice)

    store = ExtractionStore(storage)
    reloaded = store.read_bundle(tender_id)
    assert reloaded is not None
    assert [document.filename for document in reloaded.documents] == [NOTICE_FILENAME]
    assert reloaded.documents[0].text == produced.documents[0].text


# --------------------------------------------------------- a real document must win


def test_notice_is_not_added_alongside_an_extracted_document(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    """Adding the summary next to the primary source invites a model to cite the weaker one."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    pdf_id = _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="tor.pdf",
        data=text_pdf([ENGLISH]),
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id, notice=notice)

    assert [document.document_id for document in bundle.documents] == [pdf_id]
    assert all(not is_notice(document) for document in bundle.documents)
    assert bundle.incomplete_inputs is False


def test_untouched_tender_behaves_exactly_as_before(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    """No notice supplied is no notice used — the fallback is opt-in per tender."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert bundle.documents == []
    assert bundle.incomplete_inputs is False


# ------------------------------------------------- files were offered but unreadable


def test_notice_is_used_when_the_offered_document_could_not_be_read(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    """The download failed, so there is no tender document — but there is a published notice."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr,
        storage,
        tender_id,
        filename="tor.pdf",
        download_status="failed",
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id, notice=notice)

    names = [document.filename for document in bundle.documents]
    assert NOTICE_FILENAME in names
    # The failed document is kept, not dropped: it is why the assessment is partial.
    assert "tor.pdf" in names


def test_a_failed_document_alone_still_yields_no_assessable_content(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)
    _add_document(
        session_factory_gr, storage, tender_id, filename="tor.pdf", download_status="failed"
    )

    bundle = _service(session_factory_gr, storage).process_tender(tender_id)

    assert all(not is_notice(document) for document in bundle.documents)
    assert bundle.extracted_documents == []


# --------------------------------------------------------------- nothing worth assessing


def test_a_title_only_notice_produces_no_document(
    session_factory_gr: sessionmaker[Session], tmp_path: Path
) -> None:
    """A thin document would satisfy "there is a document" while supporting no requirement."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(
        tender_id, notice=NoticeSource(title="Medical consumables")
    )

    assert bundle.documents == []
    assert bundle.incomplete_inputs is False


def test_notice_sections_are_citable_for_the_verdict_stage(
    session_factory_gr: sessionmaker[Session], tmp_path: Path, notice: NoticeSource
) -> None:
    """Stage B validates every quote against the text at the cited location."""
    storage = _storage(tmp_path)
    tender_id = _seed_tender(session_factory_gr)

    bundle = _service(session_factory_gr, storage).process_tender(tender_id, notice=notice)

    document = bundle.documents[0]
    assert document.sections
    for section in document.sections:
        assert section.location == f"section:{section.index}"
        assert section.text in document.text

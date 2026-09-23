"""Prompt 09 §13, §14 — where extracted content is persisted and when it may be reused."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedPage,
    ProcessingMetadata,
    TenderDocumentBundle,
)
from tender_intelligence.processing.store import (
    EXTRACTED_ROOT,
    ExtractionStore,
    artifact_key,
    bundle_key,
)
from tender_intelligence.processing.versions import PROCESSOR_VERSION
from tender_intelligence.storage.local import LocalFileSystemStorage


def _storage(tmp_path: Path) -> LocalFileSystemStorage:
    return LocalFileSystemStorage(tmp_path / "objects")


def _extraction(**overrides: object) -> DocumentExtraction:
    defaults: dict[str, object] = {
        "document_id": 7,
        "tender_id": 3,
        "filename": "tor.pdf",
        "source_url": "https://example.test/tor.pdf",
        "extraction_status": "extracted",
        "extraction_method": "native_pdf",
        "text": "Evaluation criteria.",
        "checksum": "b" * 64,
        "pages": [
            ExtractedPage(number=1, text="Evaluation criteria.", method="native_pdf")
        ],
        "metadata": ProcessingMetadata(
            config_version="1", processed_at="2026-01-01T00:00:00+00:00"
        ),
    }
    defaults.update(overrides)
    return DocumentExtraction(**defaults)  # type: ignore[arg-type]


def test_keys_are_deterministic_and_tender_scoped() -> None:
    assert (
        artifact_key(tender_id=3, segment="b" * 64, config_version="1")
        == f"tenders/3/{EXTRACTED_ROOT}/{'b' * 64}/v1.json"
    )
    assert bundle_key(3) == f"tenders/3/{EXTRACTED_ROOT}/bundle.json"


def test_artifact_key_omits_the_extraction_method() -> None:
    """§14 — a changed method must overwrite the artifact, not orphan the previous one."""
    store = ExtractionStore(_storage(Path(".")), config_version="1")
    pdf_run = store.key_for(tender_id=3, segment="b" * 64)
    ocr_run = store.key_for(tender_id=3, segment="b" * 64)
    assert pdf_run == ocr_run
    assert "native_pdf" not in pdf_run
    assert "ocr" not in pdf_run


def test_extraction_round_trips_through_storage(tmp_path: Path) -> None:
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    extraction = _extraction()

    key = store.write_extraction(extraction)
    assert store.read_extraction(key) == extraction


def test_a_missing_artifact_reads_as_absent(tmp_path: Path) -> None:
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    assert store.read_extraction("tenders/3/extracted/nope/v1.json") is None


def test_an_unreadable_artifact_reads_as_absent_and_is_logged(
    tmp_path: Path, caplog: logging.LogRecord
) -> None:
    """Better to reprocess a document than to fail the tender over a damaged artifact."""
    storage = _storage(tmp_path)
    store = ExtractionStore(storage, config_version="1")
    key = store.key_for(tender_id=3, segment="c" * 64)
    storage.put(key, b"{not json at all", "application/json")

    with caplog.at_level(logging.WARNING):
        assert store.read_extraction(key) is None
    assert any("unreadable" in record.message for record in caplog.records)


def test_artifact_is_written_as_deterministic_json(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    store = ExtractionStore(storage, config_version="1")
    key = store.write_extraction(_extraction())

    payload = json.loads(storage.get(key).decode("utf-8"))
    assert payload["extraction_status"] == "extracted"
    assert payload["content_fingerprint"]
    assert json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) == (
        storage.get(key).decode("utf-8")
    ), "the artifact is stored canonically, so identical content is byte-identical"


def test_bundle_round_trips_through_storage(tmp_path: Path) -> None:
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    bundle = TenderDocumentBundle(
        tender_id=3,
        documents=[_extraction()],
        incomplete_inputs=False,
        languages=["en"],
        created_at="2026-01-01T00:00:00+00:00",
    )
    store.write_bundle(bundle)
    restored = store.read_bundle(3)
    assert restored is not None
    assert restored.tender_id == 3
    assert restored.documents[0].text == "Evaluation criteria."


def test_rewriting_a_bundle_replaces_it(tmp_path: Path) -> None:
    """§12 — one reusable bundle per tender, not an accumulation of runs."""
    storage = _storage(tmp_path)
    store = ExtractionStore(storage, config_version="1")

    store.write_bundle(TenderDocumentBundle(tender_id=3, created_at="first"))
    store.write_bundle(TenderDocumentBundle(tender_id=3, created_at="second"))

    assert len([k for k in storage.list_keys("tenders/3/extracted") if "bundle" in k]) == 1
    restored = store.read_bundle(3)
    assert restored is not None and restored.created_at == "second"


def test_reuse_requires_a_successful_matching_extraction(tmp_path: Path) -> None:
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    assert store.is_reusable(_extraction()) is True


def test_failed_and_skipped_extractions_are_never_reused(tmp_path: Path) -> None:
    """A transient OCR failure must not permanently poison a document."""
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    for status in ("failed", "skipped", "pending"):
        assert store.is_reusable(_extraction(extraction_status=status)) is False


def test_reuse_is_invalidated_by_a_processor_version_change(tmp_path: Path) -> None:
    """§15 — otherwise a processor fix would never reach already-processed documents."""
    store = ExtractionStore(_storage(tmp_path), config_version="1")
    stale = _extraction(
        metadata=ProcessingMetadata(processor_version="0.0.1", config_version="1")
    )
    assert store.is_reusable(stale) is False
    assert PROCESSOR_VERSION != "0.0.1"


def test_reuse_is_invalidated_by_a_config_version_change(tmp_path: Path) -> None:
    store = ExtractionStore(_storage(tmp_path), config_version="2")
    old_config = _extraction(
        metadata=ProcessingMetadata(processor_version=PROCESSOR_VERSION, config_version="1")
    )
    assert store.is_reusable(old_config) is False


def test_config_version_is_reflected_in_the_key(tmp_path: Path) -> None:
    first = ExtractionStore(_storage(tmp_path), config_version="1")
    second = ExtractionStore(_storage(tmp_path), config_version="2")
    assert first.key_for(tender_id=3, segment="b" * 64) != second.key_for(
        tender_id=3, segment="b" * 64
    )

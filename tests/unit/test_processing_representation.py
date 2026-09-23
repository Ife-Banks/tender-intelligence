"""Prompt 09 §11/§12/§14 — the extraction representation and its persistence round-trip."""

from __future__ import annotations

from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedPage,
    ExtractedSection,
    ExtractedTable,
    ProcessingMetadata,
    TenderDocumentBundle,
)

MATRIX = [
    ["Criterion", "Weight"],
    ["Methodology", "40%"],
    ["Team", "30%"],
]


def _table() -> ExtractedTable:
    return ExtractedTable(
        location="page:1",
        index=0,
        headers=list(MATRIX[0]),
        rows=[list(row) for row in MATRIX],
        header_convention="first_row",
        page=1,
        bbox=[10.0, 20.0, 300.0, 120.0],
    )


def _extraction(**overrides: object) -> DocumentExtraction:
    defaults: dict[str, object] = {
        "document_id": 7,
        "tender_id": 3,
        "filename": "tor.pdf",
        "source_url": "https://example.test/tor.pdf",
        "extraction_status": "extracted",
        "extraction_method": "native_pdf",
        "text": "Evaluation criteria are set out below.",
        "checksum": "a" * 64,
        "pages": [
            ExtractedPage(
                number=1,
                text="Evaluation criteria are set out below.",
                method="native_pdf",
                tables=[_table()],
            )
        ],
        "metadata": ProcessingMetadata(
            config_version="1", processed_at="2026-01-01T00:00:00+00:00"
        ),
    }
    defaults.update(overrides)
    return DocumentExtraction(**defaults)  # type: ignore[arg-type]


def test_table_round_trip_preserves_the_complete_matrix() -> None:
    table = _table()
    restored = ExtractedTable.from_dict(table.to_dict())
    assert restored == table
    assert restored.rows == MATRIX, "every row must survive, including the header row"
    assert restored.row_count == 3
    assert restored.column_count == 2
    assert restored.bbox == [10.0, 20.0, 300.0, 120.0]


def test_table_header_convention_is_recorded_not_assumed_silently() -> None:
    assert _table().header_convention == "first_row"
    single = ExtractedTable(location="page:1", index=0, rows=[["Only", "Row"]])
    assert single.header_convention == "none"
    assert single.headers == [], "a one-row table has no header row to claim"


def test_table_to_text_prints_the_header_row_once() -> None:
    """The text rendering must not duplicate the header row it also carries as headers."""
    rendered = _table().to_text()
    assert rendered.splitlines()[0] == "Criterion | Weight"
    assert rendered.count("Criterion") == 1
    assert "Methodology | 40%" in rendered


def test_extraction_round_trip_preserves_pages_tables_and_language() -> None:
    extraction = _extraction(language="en", language_confidence=0.51, language_source="detected")
    restored = DocumentExtraction.from_dict(extraction.to_dict())

    assert restored.extraction_status == "extracted"
    assert restored.extraction_method == "native_pdf"
    assert restored.language == "en"
    assert restored.language_confidence == 0.51
    assert restored.page_count == 1
    assert restored.pages[0].tables[0].rows == MATRIX
    assert restored.tables == extraction.tables


def test_tables_are_persisted_once_not_duplicated_across_the_artifact() -> None:
    """A flat ``tables`` key alongside pages/sections would double every table in storage."""
    payload = _extraction().to_dict()
    assert "tables" not in payload, "table content belongs under its page or section only"
    assert payload["pages"][0]["tables"][0]["rows"] == MATRIX


def test_docx_sections_round_trip_in_order() -> None:
    extraction = _extraction(
        extraction_method="docx",
        pages=[],
        sections=[
            ExtractedSection(index=0, kind="paragraph", text="Introduction"),
            ExtractedSection(index=1, kind="table", text="Criterion | Weight", tables=[_table()]),
            ExtractedSection(index=2, kind="paragraph", text="The deadline is 30 March 2026."),
        ],
    )
    restored = DocumentExtraction.from_dict(extraction.to_dict())
    assert [section.kind for section in restored.sections] == ["paragraph", "table", "paragraph"]
    assert restored.sections[2].text == "The deadline is 30 March 2026."
    assert restored.tables[0].location == "page:1"


def test_content_fingerprint_is_deterministic_and_excludes_run_metadata() -> None:
    first = _extraction(metadata=ProcessingMetadata(config_version="1", processed_at="2026-01-01"))
    second = _extraction(
        metadata=ProcessingMetadata(
            config_version="1",
            processed_at="2026-06-06T12:00:00+00:00",
            correlation_id="different-run",
        )
    )
    assert first.content_fingerprint == second.content_fingerprint

    changed = _extraction(text="A different body of text.")
    assert changed.content_fingerprint != first.content_fingerprint


def test_artifact_key_segment_prefers_the_checksum() -> None:
    assert _extraction().artifact_key_segment == "a" * 64
    assert _extraction(checksum=None).artifact_key_segment == "doc-7"


def test_status_predicates_are_mutually_exclusive() -> None:
    for status in ("extracted", "failed", "skipped"):
        extraction = _extraction(extraction_status=status)
        flags = [extraction.is_extracted, extraction.is_failed, extraction.is_skipped]
        assert sum(flags) == 1, f"{status} must map to exactly one predicate"


def test_page_location_and_ocr_flags() -> None:
    page = ExtractedPage(number=4, text="scan", method="ocr")
    assert page.location == "page:4"
    assert page.was_ocr is True
    assert ExtractedPage(number=1, text="native", method="native_pdf").was_ocr is False


def test_bundle_round_trip_and_document_lookup() -> None:
    bundle = TenderDocumentBundle(
        tender_id=3,
        documents=[
            _extraction(document_id=7),
            _extraction(
                document_id=8,
                extraction_status="failed",
                extraction_method="none",
                error_code="parse_failed",
                pages=[],
                text="",
            ),
            _extraction(
                document_id=9,
                extraction_status="skipped",
                extraction_method="none",
                pages=[],
                text="",
            ),
        ],
        incomplete_inputs=True,
        languages=["en", "fr"],
        created_at="2026-01-01T00:00:00+00:00",
    )

    restored = TenderDocumentBundle.from_dict(bundle.to_dict())
    assert restored.tender_id == 3
    assert restored.document_count == 3
    assert [document.document_id for document in restored.extracted_documents] == [7]
    assert [document.document_id for document in restored.failed_documents] == [8]
    assert [document.document_id for document in restored.skipped_documents] == [9]
    assert restored.incomplete_inputs is True
    assert restored.languages == ["en", "fr"]
    assert restored.document_by_id(9) is not None
    assert restored.document_by_id(999) is None


def test_bundle_counts_are_derived_from_the_documents() -> None:
    bundle = TenderDocumentBundle(
        tender_id=3,
        documents=[
            _extraction(document_id=7),
            _extraction(
                document_id=8,
                extraction_status="failed",
                extraction_method="none",
                pages=[],
                text="",
            ),
        ],
    )
    payload = bundle.to_dict()
    assert payload["document_count"] == 2
    assert payload["extracted_count"] == 1
    assert payload["failed_count"] == 1
    assert payload["skipped_count"] == 0

"""Prompt 09 §4, §8, §9, §20 — PDF extraction: native text, scans, mixed documents, tables."""

from __future__ import annotations

import pytest

from support.documents import (
    DEADLINE_TABLE,
    ENGLISH,
    EVALUATION_MATRIX,
    FRENCH,
    blank_pdf,
    image_pdf,
    mixed_pdf,
    table_pdf,
    text_pdf,
)
from support.stubs import STUB_OCR_TEXT, StubOcrEngine
from tender_intelligence.processing.errors import OcrUnavailableError, ParseFailedError
from tender_intelligence.processing.pdf import extract_pdf


class TestNativeText:
    """§4.1 — a PDF with a usable text layer is read directly and never OCR'd."""

    def test_pages_are_extracted_with_their_boundaries_preserved(self) -> None:
        result = extract_pdf(text_pdf([ENGLISH, FRENCH]))
        assert result.method == "native_pdf"
        assert result.page_count == 2
        assert result.ocr_page_count == 0
        assert [page.number for page in result.pages] == [1, 2]
        assert "World Health Organization" in result.pages[0].text
        assert "Organisation mondiale" in result.pages[1].text

    def test_page_text_is_never_split_across_pages(self) -> None:
        """§8 — page boundaries are what let a later stage cite where a fact came from."""
        result = extract_pdf(text_pdf([ENGLISH, FRENCH]))
        assert "Organisation mondiale" not in result.pages[0].text
        assert "World Health Organization" not in result.pages[1].text

    def test_native_path_does_not_invoke_ocr_even_when_an_engine_is_available(self) -> None:
        ocr = StubOcrEngine()
        result = extract_pdf(text_pdf([ENGLISH]), ocr=ocr)
        assert ocr.calls == 0
        assert result.ocr_page_count == 0

    def test_document_text_contains_every_page(self) -> None:
        result = extract_pdf(text_pdf([ENGLISH, FRENCH]))
        assert "World Health Organization" in result.text
        assert "Organisation mondiale" in result.text


class TestScannedPdf:
    """§4.2 — a page with no text layer is rendered and OCR'd."""

    def test_scan_is_ocrd_and_mapped_to_its_page_number(self) -> None:
        ocr = StubOcrEngine()
        result = extract_pdf(image_pdf("SCANNED ANNEX", 1), ocr=ocr)

        assert ocr.calls == 1
        assert result.method == "ocr"
        assert result.ocr_page_count == 1
        assert result.pages[0].number == 1
        assert result.pages[0].method == "ocr"
        assert result.pages[0].was_ocr is True
        assert STUB_OCR_TEXT.strip() in result.text

    def test_engine_and_version_are_recorded_as_provenance(self) -> None:
        result = extract_pdf(image_pdf(), ocr=StubOcrEngine(version="9.9.9-test"))
        page = result.pages[0]
        assert page.ocr_engine == "stub-ocr"
        assert page.ocr_engine_version == "9.9.9-test"

    def test_multiple_scanned_pages_keep_their_numbers(self) -> None:
        ocr = StubOcrEngine()
        result = extract_pdf(image_pdf(page_count=3), ocr=ocr)
        assert ocr.calls == 3
        assert [page.number for page in result.pages] == [1, 2, 3]
        assert result.ocr_page_count == 3

    def test_a_scan_without_an_ocr_engine_fails_rather_than_fabricating_text(self) -> None:
        """§16/§20 — reporting an empty page as "extracted" would invent understanding."""
        with pytest.raises(OcrUnavailableError) as raised:
            extract_pdf(image_pdf())
        assert raised.value.error_code == "ocr_failed"
        assert raised.value.context["page"] == 1

    def test_an_unavailable_engine_is_not_treated_as_a_working_one(self) -> None:
        with pytest.raises(OcrUnavailableError):
            extract_pdf(image_pdf(), ocr=StubOcrEngine(available=False))

    def test_a_blank_page_is_recognised_as_needing_ocr(self) -> None:
        with pytest.raises(OcrUnavailableError):
            extract_pdf(blank_pdf(1))


class TestMixedDocument:
    """§4.1/§4.2 — the choice is made per page, not per document."""

    def test_native_and_scanned_pages_coexist_in_one_document(self) -> None:
        ocr = StubOcrEngine()
        result = extract_pdf(mixed_pdf(native_pages=1, scanned_pages=1), ocr=ocr)

        assert result.page_count == 2
        assert ocr.calls == 1, "only the scanned page may be OCR'd"
        assert result.pages[0].method == "native_pdf"
        assert result.pages[1].method == "ocr"
        assert result.ocr_page_count == 1
        assert result.method == "ocr", "the document is reported as OCR'd if any page was"
        assert "World Health Organization" in result.pages[0].text
        assert STUB_OCR_TEXT.strip() in result.pages[1].text

    def test_mixed_document_without_ocr_fails_on_the_scan(self) -> None:
        with pytest.raises(OcrUnavailableError):
            extract_pdf(mixed_pdf(native_pages=1, scanned_pages=1))

    def test_several_native_and_several_scanned_pages(self) -> None:
        ocr = StubOcrEngine()
        result = extract_pdf(mixed_pdf(native_pages=2, scanned_pages=2), ocr=ocr)
        assert [page.method for page in result.pages] == [
            "native_pdf",
            "native_pdf",
            "ocr",
            "ocr",
        ]
        assert ocr.calls == 2


class TestTables:
    """§9 — structured tables are preserved alongside the text, not instead of it."""

    def test_evaluation_matrix_is_extracted_as_a_matrix(self) -> None:
        result = extract_pdf(table_pdf(EVALUATION_MATRIX, caption="Evaluation criteria"))
        tables = result.pages[0].tables
        assert len(tables) == 1

        table = tables[0]
        assert table.location == "page:1"
        assert table.page == 1
        assert table.rows == EVALUATION_MATRIX
        assert table.row_count == 5
        assert table.column_count == 3
        assert table.header_convention == "first_row"
        assert table.headers == ["Criterion", "Weight", "Threshold"]
        assert table.bbox is not None

    def test_deadline_table_keeps_its_dates_intact(self) -> None:
        """The business-critical case: a deadline read from a mangled table is worse than none."""
        result = extract_pdf(table_pdf(DEADLINE_TABLE))
        rows = result.pages[0].tables[0].rows
        assert ["Proposal submission deadline", "30 March 2026", "Bidder"] in rows

    def test_table_text_also_remains_in_the_page_text(self) -> None:
        """Inherent to PDF text extraction; the structured form is offered *alongside* it (§9)."""
        result = extract_pdf(table_pdf(EVALUATION_MATRIX, caption="Evaluation criteria"))
        assert "Evaluation criteria" in result.pages[0].text
        assert result.pages[0].tables[0].rows == EVALUATION_MATRIX

    def test_a_page_without_tables_reports_none(self) -> None:
        result = extract_pdf(text_pdf([ENGLISH]))
        assert result.pages[0].tables == []


class TestParseFailures:
    """§20 — "declared format is wrong" is distinct from "format is not supported"."""

    def test_garbage_bytes_are_a_parse_failure(self) -> None:
        with pytest.raises(ParseFailedError) as raised:
            extract_pdf(b"this is not a pdf at all, despite the name")
        assert raised.value.error_code == "parse_failed"

    def test_truncated_pdf_is_a_parse_failure(self) -> None:
        with pytest.raises(ParseFailedError):
            extract_pdf(text_pdf([ENGLISH])[:120])

    def test_failure_messages_never_contain_document_content(self) -> None:
        """§18 — an error identifies the failure, not the document's contents."""
        secret = "CONFIDENTIAL TENDER PRICING SCHEDULE"
        with pytest.raises(ParseFailedError) as raised:
            extract_pdf(secret.encode("utf-8"))
        assert secret not in str(raised.value)

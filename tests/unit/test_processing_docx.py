"""Prompt 09 §6, §8 — DOCX extraction with true document order."""

from __future__ import annotations

import pytest

from support.documents import DEADLINE_TABLE, ENGLISH, EVALUATION_MATRIX, FRENCH, docx_bytes
from tender_intelligence.processing.docx import extract_docx
from tender_intelligence.processing.errors import ParseFailedError


def test_paragraphs_and_tables_are_extracted() -> None:
    result = extract_docx(docx_bytes([ENGLISH, EVALUATION_MATRIX]))
    assert result.method == "docx"
    assert "World Health Organization" in result.text
    assert len(result.tables) == 1
    assert result.tables[0].rows == EVALUATION_MATRIX


def test_document_order_is_preserved_across_paragraphs_and_tables() -> None:
    """§8 — separate ``.paragraphs``/``.tables`` collections would lose this interleaving."""
    result = extract_docx(
        docx_bytes(
            [
                "Section 1: Evaluation",
                EVALUATION_MATRIX,
                "The deadline for submission is 30 March 2026.",
                DEADLINE_TABLE,
                "Late submissions will not be considered.",
            ]
        )
    )
    assert [section.kind for section in result.sections] == [
        "paragraph",
        "table",
        "paragraph",
        "table",
        "paragraph",
    ]
    assert result.sections[0].text == "Section 1: Evaluation"
    assert result.sections[2].text == "The deadline for submission is 30 March 2026."
    assert result.sections[4].text == "Late submissions will not be considered."


def test_section_indices_are_stable_and_ordered() -> None:
    result = extract_docx(docx_bytes(["First", "Second", "Third"]))
    assert [section.index for section in result.sections] == [0, 1, 2]
    assert [section.text for section in result.sections] == ["First", "Second", "Third"]


def test_table_carries_its_section_location_for_provenance() -> None:
    """DOCX has no pages, so a table's location names its section rather than inventing a page."""
    result = extract_docx(docx_bytes(["Intro", EVALUATION_MATRIX]))
    table = result.tables[0]
    assert table.location == "section:1"
    assert table.page is None
    assert result.sections[1].location == "section:1"
    assert result.sections[1].tables == [table]


def test_table_structure_and_header_convention() -> None:
    result = extract_docx(docx_bytes([DEADLINE_TABLE]))
    table = result.tables[0]
    assert table.row_count == 4
    assert table.column_count == 3
    assert table.headers == ["Milestone", "Date", "Responsible"]
    assert table.header_convention == "first_row"
    assert ["Proposal submission deadline", "30 March 2026", "Bidder"] in table.rows


def test_reading_order_text_places_tables_where_they_appear() -> None:
    result = extract_docx(docx_bytes(["Before the matrix.", EVALUATION_MATRIX, "After it."]))
    before = result.text.index("Before the matrix.")
    matrix = result.text.index("Criterion")
    after = result.text.index("After it.")
    assert before < matrix < after


def test_french_document_is_extracted_verbatim() -> None:
    result = extract_docx(docx_bytes([FRENCH]))
    assert "Organisation mondiale" in result.text


def test_blank_paragraphs_are_not_emitted_as_sections() -> None:
    result = extract_docx(docx_bytes(["Real content", ""]))
    assert [section.text for section in result.sections] == ["Real content"]


def test_garbage_bytes_are_a_parse_failure() -> None:
    with pytest.raises(ParseFailedError) as raised:
        extract_docx(b"not a docx package, whatever the filename says")
    assert raised.value.error_code == "parse_failed"


def test_truncated_zip_is_a_parse_failure() -> None:
    with pytest.raises(ParseFailedError):
        extract_docx(docx_bytes([ENGLISH])[:200])


def test_zip_container_without_a_word_document_is_a_parse_failure() -> None:
    """A valid ZIP that is not an OOXML package is a format mismatch, not an empty document."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no word document here")
    with pytest.raises(ParseFailedError):
        extract_docx(buffer.getvalue())


def test_failure_messages_never_contain_document_content() -> None:
    """§18 — errors name the failure, not the contents."""
    with pytest.raises(ParseFailedError) as raised:
        extract_docx(b"CONFIDENTIAL PRICING TABLE")
    assert "CONFIDENTIAL" not in str(raised.value)


class TestHeadings:
    """Prompt 12 §4 — headings are captured where the format reports them."""

    def test_heading_level_is_read_from_the_word_style(self) -> None:
        result = extract_docx(
            docx_bytes(
                [
                    ("heading", 1, "I. Contexte"),
                    "Body paragraph under the first heading.",
                    ("heading", 2, "A. Sous-section"),
                ]
            )
        )
        headings = [section for section in result.sections if section.is_heading]
        assert [section.heading_level for section in headings] == [1, 2]

    def test_heading_style_name_is_preserved(self) -> None:
        result = extract_docx(docx_bytes([("heading", 1, "I. Contexte")]))
        assert result.sections[0].style == "Heading 1"

    def test_a_heading_is_still_a_paragraph(self) -> None:
        # Reclassifying it as its own kind would misrepresent the document to every consumer; the
        # heading is a paragraph that reports being styled as one.
        result = extract_docx(docx_bytes([("heading", 1, "I. Contexte")]))
        assert result.sections[0].kind == "paragraph"
        assert result.sections[0].is_heading is True

    def test_body_paragraph_is_not_a_heading(self) -> None:
        result = extract_docx(docx_bytes([("heading", 1, "I. Contexte"), "Plain body text."]))
        body = result.sections[1]
        assert body.is_heading is False
        assert body.heading_level is None

    def test_heading_text_reaches_the_document_text(self) -> None:
        result = extract_docx(docx_bytes([("heading", 1, "I. Contexte"), "Body."]))
        assert "I. Contexte" in result.text

    def test_headings_are_serialized_with_their_level(self) -> None:
        result = extract_docx(docx_bytes([("heading", 2, "A. Sous-section")]))
        payload = result.sections[0].to_dict()
        assert payload["heading_level"] == 2
        assert payload["is_heading"] is True


class TestCoreProperties:
    """Prompt 12 §4 — the package's declared metadata, kept separate from its body."""

    def test_core_properties_are_preserved(self) -> None:
        result = extract_docx(
            docx_bytes(
                [FRENCH],
                properties={
                    "title": "Avis de Demande de Propositions",
                    "author": "Cellule Achats",
                    "subject": "Ref. DA-2026-04",
                },
            )
        )
        assert result.metadata["title"] == "Avis de Demande de Propositions"
        assert result.metadata["author"] == "Cellule Achats"
        assert result.metadata["subject"] == "Ref. DA-2026-04"

    def test_properties_coexist_with_extracted_text(self) -> None:
        # Metadata is additional: it never replaces or stands in for the content.
        result = extract_docx(
            docx_bytes([ENGLISH], properties={"title": "Notice of Expression of Interest"})
        )
        assert "World Health Organization" in result.text
        assert result.metadata["title"] == "Notice of Expression of Interest"

    def test_a_document_without_properties_reports_none(self) -> None:
        result = extract_docx(docx_bytes([ENGLISH]))
        assert "title" not in result.metadata

    def test_template_boilerplate_comments_are_not_reported_as_metadata(self) -> None:
        # python-docx pre-fills ``comments`` with generator boilerplate. Recording it would put
        # "generated by python-docx" in a tender's metadata for a reason that has nothing to do
        # with the document.
        result = extract_docx(docx_bytes([ENGLISH], properties={"comments": "draft notes"}))
        assert "comments" not in result.metadata


class TestTableRoles:
    """Prompt 12 §7 — the business role of an extracted table."""

    def test_evaluation_matrix_is_classified_as_evaluation(self) -> None:
        result = extract_docx(docx_bytes(["Section 1: Evaluation", EVALUATION_MATRIX]))
        assert result.tables[0].role == "evaluation"

    def test_deadline_table_is_classified_as_deadline(self) -> None:
        result = extract_docx(docx_bytes([DEADLINE_TABLE]))
        assert result.tables[0].role == "deadline"

    def test_classification_does_not_disturb_the_matrix(self) -> None:
        result = extract_docx(docx_bytes([DEADLINE_TABLE]))
        assert result.tables[0].rows == DEADLINE_TABLE

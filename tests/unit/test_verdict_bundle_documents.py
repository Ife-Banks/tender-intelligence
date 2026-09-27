"""Stage B prompt flattening: what the model is actually shown, and what it is not.

``_bundle_documents`` is the single place where a stored bundle becomes model-visible content, so
its behaviour is the AI stage's real input contract. Three things are pinned here:

1. a DOCX table section already carries its own rendering, so its rows must appear exactly once --
   duplicating them is the kind of error that makes a model quote a total twice or invent a row;
2. a prose section with hanging tables has those tables still emitted (no silent loss);
3. the extraction stage's language/translation record is *not* injected into the model payload. That
   is a deliberate, visible boundary rather than an oversight, and this test file fails if it ever
   changes silently.
"""

from __future__ import annotations

from tender_intelligence.processing.languages import bundle_languages
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedPage,
    ExtractedSection,
    ExtractedTable,
    TenderDocumentBundle,
)
from tender_intelligence.processing.translation import UNRESOLVED_LANGUAGE_LIMITATION
from tender_intelligence.verdict.service import _bundle_documents

WEIGHTING_ROWS = [
    ["Lot 1", "40", "2 000 000"],
    ["Lot 2", "60", "3 500 000"],
]


def _table(location: str = "section-2/table-0") -> ExtractedTable:
    return ExtractedTable(
        location=location,
        index=0,
        headers=["Lot", "Weighting %", "Value"],
        rows=WEIGHTING_ROWS,
    )


def _table_section() -> ExtractedSection:
    """A table-kind section whose ``text`` is the table already rendered.

    This is exactly what the DOCX extractor produces: it keeps reading order intact by emitting the
    table inline as section text, and also keeps the structured table for downstream callers.
    """
    return ExtractedSection(
        index=1,
        kind="table",
        text="Lot | Weighting % | Value\nLot 1 | 40 | 2 000 000\nLot 2 | 60 | 3 500 000",
        tables=[_table()],
    )


def _prose_section() -> ExtractedSection:
    return ExtractedSection(
        index=0,
        kind="prose",
        text="Candidates must submit a technical proposal.",
        tables=[],
    )


def _extraction(*, language: str | None = "fr") -> DocumentExtraction:
    return DocumentExtraction(
        document_id=7,
        tender_id=3,
        filename="specification.docx",
        source_url="https://src.example/t3/specification.docx",
        extraction_status="extracted",
        extraction_method="docx",
        text="fallback text",
        language=language,
        sections=[_prose_section(), _table_section()],
    )


def _bundle(*, language: str | None = "fr") -> TenderDocumentBundle:
    extraction = _extraction(language=language)
    languages = bundle_languages([extraction.language])
    return TenderDocumentBundle(
        tender_id=extraction.tender_id,
        documents=[extraction],
        languages=languages,
        translation=TenderDocumentBundle(tender_id=extraction.tender_id).translation,
    )


def test_a_docx_table_is_not_emitted_twice() -> None:
    """The table appears once, in its section's rendering, and not again as a second chunk."""
    docs = _bundle_documents(_bundle())
    assert len(docs) == 1
    chunks = docs[0]["content"]
    # Two chunks: prose section and table section.
    assert len(chunks) == 2
    # The rendered text of the table section contains the figures.
    rendered = chunks[1]["text"]
    assert "2 000 000" in rendered
    # The figures appear exactly once, not twice.
    assert rendered.count("2 000 000") == 1
    assert rendered.count("Lot 2") == 1
    # No extra chunk with its own table rendering is present alongside the section text.
    assert not any("Lot 1" in chunk["text"] and chunk["text"] != rendered for chunk in chunks)


def test_prose_section_tables_are_still_emitted() -> None:
    """Skipping re-emission applies to table-kind sections only.

    A table hanging off a prose section has no inline rendering, so dropping it would silently lose
    the table from the prompt -- the opposite failure.
    """
    extraction = DocumentExtraction(
        document_id=7,
        tender_id=3,
        filename="specification.docx",
        source_url="https://src.example/t3/specification.docx",
        extraction_status="extracted",
        extraction_method="docx",
        sections=[
            ExtractedSection(
                index=0,
                kind="prose",
                text="Scoring is as follows.",
                tables=[_table(location="section-0/table-0")],
            )
        ],
    )
    docs = _bundle_documents(TenderDocumentBundle(tender_id=3, documents=[extraction]))
    chunks = docs[0]["content"]
    assert len(chunks) == 2
    # The table chunk is present even though there's no prose rendering for it.
    assert "2 000 000" in chunks[1]["text"]


def test_page_tables_are_emitted_alongside_page_text() -> None:
    """PDF pages keep text and tables as separate chunks, tables not folded into the page text."""
    extraction = DocumentExtraction(
        document_id=8,
        tender_id=3,
        filename="specification.pdf",
        source_url="https://src.example/t3/specification.pdf",
        extraction_status="extracted",
        extraction_method="pdf_text",
        pages=[
            ExtractedPage(
                number=1,
                text="Page one prose.",
                method="pdf_text",
                tables=[_table(location="page-1/table-0")],
            )
        ],
    )
    docs = _bundle_documents(TenderDocumentBundle(tender_id=3, documents=[extraction]))
    chunks = docs[0]["content"]
    assert len(chunks) == 2
    assert "Page one prose." in chunks[0]["text"]
    assert "2 000 000" in chunks[1]["text"]


def test_translation_limitation_is_recorded_on_the_bundle_but_not_in_the_prompt() -> None:
    """Pins the boundary: the limitation is persisted, and the model is not told about it.

    The processing stage records the unresolved-language limitation on every bundle so it is never
    lost. Stage B does not inject it into the model payload: doing so would need a prompt change
    that tells the model how to react, and a record the model cannot act on is worse than none. If a
    future change wires this into the prompt, this test is the reminder to update it deliberately.
    """
    bundle = _bundle(language="fr")

    # Recorded and explicit on the persisted handoff artifact.
    assert bundle.translation.limitation == UNRESOLVED_LANGUAGE_LIMITATION
    assert bundle.languages == ["fr"]

    # Not present anywhere in what the model is shown.
    payload = repr(_bundle_documents(bundle))
    assert "limitation" not in payload
    assert "translation" not in payload
    assert "not_configured" not in payload


def test_a_document_with_no_structure_falls_back_to_its_text() -> None:
    """An extraction with neither pages nor sections still reaches the model as one chunk."""
    extraction = DocumentExtraction(
        document_id=9,
        tender_id=3,
        filename="notes.txt",
        source_url="https://src.example/t3/notes.txt",
        extraction_status="extracted",
        extraction_method="text",
        text="Just some notes.",
    )
    docs = _bundle_documents(TenderDocumentBundle(tender_id=3, documents=[extraction]))
    assert len(docs) == 1
    # The model sees a single chunk with text (plus the location that the verdict stage always
    # includes).  The text field is the one the prompt consumes.
    assert docs[0]["content"][0]["text"] == "Just some notes."


def test_an_unextracted_document_reports_its_status_with_no_content() -> None:
    """A failed extraction stays visible as a document with a status instead of vanishing."""
    extraction = DocumentExtraction(
        document_id=10,
        tender_id=3,
        filename="broken.pdf",
        source_url="https://src.example/t3/broken.pdf",
        extraction_status="failed",
        extraction_method="pdf_text",
        error_code="extraction_failed",
    )
    docs = _bundle_documents(TenderDocumentBundle(tender_id=3, documents=[extraction]))
    assert len(docs) == 1
    assert docs[0]["content"] == []
    assert docs[0]["extraction_status"] == "failed"

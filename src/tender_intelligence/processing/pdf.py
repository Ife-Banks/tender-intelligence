""":mod:`tender_intelligence.processing.pdf` — PDF extraction (prompt 09 §4, §8, §9).

Two paths, chosen per page rather than per document:

* **Native** — a page with a usable text layer is read directly. A PDF that has usable native text
  is never OCR'd (prompt 09 §4.1).
* **OCR** — a page whose text layer is empty or negligible is rendered and OCR'd (prompt 09 §4.2).

Per-page sufficiency matters because real tenders mix the two: a native RFP with a scanned annex
has some pages that need OCR and some that do not. OCR-ing only the pages that need it preserves
the native text layer exactly where it exists and avoids pointless work.

Page boundaries are preserved in both paths (prompt 09 §8). Table text also appears in the page
text stream — that is inherent to PDF text extraction, not a defect — so the page text and the
structured tables are kept side by side rather than one replacing the other (prompt 09 §9).

The document's own ``/Info`` metadata is preserved alongside the content (prompt 12 §2): a TOR
titled "RFP — Health Systems Strengthening, ref. WH-AFR-2026-01" carries the reference and the
title in metadata that no text layer repeats. It is recorded as metadata and never substituted for
extracted text, because a metadata string is a publisher's claim, not the document's content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import pymupdf

from tender_intelligence.processing.errors import (
    OcrFailedError,
    OcrUnavailableError,
    ParseFailedError,
)
from tender_intelligence.processing.ocr import OcrEngine
from tender_intelligence.processing.representation import ExtractedPage, ExtractedTable
from tender_intelligence.processing.tables import classify_table

#: A page whose stripped text layer is shorter than this is treated as needing OCR.
#: PDFs that are genuinely image-only yield zero; a page carrying only a stamp or page number
#: yields a handful of characters and would otherwise be mistaken for a text page.
MIN_NATIVE_TEXT_CHARS_PER_PAGE: Final[int] = 25

#: Render resolution for OCR. 200 DPI is a common floor for reliable body-text OCR; it is part of
#: the extraction configuration version because changing it can change output.
DEFAULT_RENDER_DPI: Final[int] = 200

#: ``/Info`` keys preserved when the document declares them (prompt 12 §2). A fixed allow-list is
#: used rather than every key the file happens to carry: producer strings are unbounded and can
#: embed anything, and a document's metadata is not the place to accept unbounded input. ``format``
#: and ``encryption`` are included because they state what the container is, which matters when the
#: same bytes are served under a misleading name.
_PRESERVED_INFO_KEYS: Final[tuple[str, ...]] = (
    "title",
    "author",
    "subject",
    "keywords",
    "creator",
    "producer",
    "creationDate",
    "modDate",
    "format",
    "encryption",
)

#: Upper bound on any single metadata value, so a pathological ``/Info`` entry cannot bloat the
#: stored artifact. Truncation is recorded in the value so a consumer is never misled about length.
_MAX_INFO_VALUE_CHARS: Final[int] = 512

_NATIVE: Final[str] = "native_pdf"
_OCR: Final[str] = "ocr"


def _document_metadata(document: Any) -> dict[str, str]:
    """The PDF's declared ``/Info`` properties, bounded and filtered (prompt 12 §2).

    Reads the container's own metadata rather than inferring anything from the text layer, and
    never lets a metadata value stand in for content: an empty title means no title was declared,
    not that the document is untitled.
    """
    try:
        raw = document.metadata or {}
    except Exception:  # metadata is optional and occasionally malformed; never fatal
        return {}
    preserved: dict[str, str] = {}
    for key in _PRESERVED_INFO_KEYS:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.split())
        if not cleaned:
            continue
        preserved[key] = (
            cleaned if len(cleaned) <= _MAX_INFO_VALUE_CHARS
            else cleaned[:_MAX_INFO_VALUE_CHARS] + " [truncated]"
        )
    return preserved


@dataclass(frozen=True)
class PdfExtractionResult:
    """Extracted PDF content plus the document-level method that produced it."""

    pages: list[ExtractedPage] = field(default_factory=list)
    method: str = _NATIVE
    text: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def ocr_page_count(self) -> int:
        return sum(1 for page in self.pages if page.was_ocr)


def _tables_from_page(page: Any, page_number: int) -> list[ExtractedTable]:
    """Structured tables for one page, using pymupdf's ruled-line detection (prompt 09 §9).

    ``strategy="lines"`` (the default) is used deliberately: it keys off ruled lines and returns a
    clean rectangular matrix, whereas ``strategy="text"`` infers structure from text alignment and
    emits spurious blank rows that would corrupt the row set.
    """
    try:
        finder = page.find_tables()
    except Exception:  # table finding is best-effort; it never fails a document
        return []

    tables: list[ExtractedTable] = []
    for index, found in enumerate(finder.tables):
        try:
            raw = found.extract()
            bbox = [float(value) for value in found.bbox]
        except Exception:
            continue
        rows = [
            ["" if cell is None else str(cell).strip() for cell in row]
            for row in raw
        ]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue
        # rows keeps the complete matrix; headers is a labelled convenience view of row 0, and
        # role says what kind of table this is (prompt 12 §7) from the vocabulary in the header
        # and first data row.
        has_header = len(rows) > 1
        headers = list(rows[0]) if has_header else []
        role, role_source = classify_table(headers, rows)
        tables.append(
            ExtractedTable(
                location=f"page:{page_number}",
                index=index,
                headers=headers,
                rows=rows,
                header_convention="first_row" if has_header else "none",
                page=page_number,
                bbox=bbox,
                role=role,
                role_source=role_source,
            )
        )
    return tables


def extract_pdf(
    data: bytes,
    *,
    ocr: OcrEngine | None = None,
    ocr_lang: str | None = None,
    render_dpi: int = DEFAULT_RENDER_DPI,
    min_native_chars: int = MIN_NATIVE_TEXT_CHARS_PER_PAGE,
) -> PdfExtractionResult:
    """Extract *data* as a PDF, OCR-ing only the pages that need it.

    Raises :class:`ParseFailedError` when the bytes are not a readable PDF,
    :class:`OcrUnavailableError` when a page needs OCR but no engine is available, and
    :class:`OcrFailedError` when the engine runs but fails.
    """
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError, ValueError) as exc:
        raise ParseFailedError(
            f"pdf could not be parsed: {type(exc).__name__}",
            context={"format": "pdf", "exception": type(exc).__name__},
        ) from exc

    pages: list[ExtractedPage] = []
    document_metadata: dict[str, str] = {}
    try:
        if document.page_count == 0:
            # pymupdf repairs some corrupt files into a *readable but empty* document rather than
            # raising (a truncated download is the common case). Reporting that as "extracted"
            # would pass a document that contributed nothing into the AI stage as understood
            # content, so it is recorded as the parse failure it is (prompt 09 §16, §20).
            raise ParseFailedError(
                "pdf contains no pages",
                context={"format": "pdf", "page_count": 0},
            )

        document_metadata = _document_metadata(document)

        for offset in range(document.page_count):
            page: Any = document.load_page(offset)
            number = offset + 1
            native_text = page.get_text("text") or ""
            native_chars = len(native_text.strip())
            tables = _tables_from_page(page, number)

            if native_chars >= min_native_chars:
                pages.append(
                    ExtractedPage(
                        number=number,
                        text=native_text,
                        method=_NATIVE,
                        tables=tables,
                        native_text_chars=native_chars,
                    )
                )
                continue

            if ocr is None or not ocr.is_available():
                # No text layer and no OCR: reporting this page as extracted would fabricate
                # understanding, so the document fails explicitly (prompt 09 §16, §20).
                raise OcrUnavailableError(
                    "page requires OCR but no OCR engine is available",
                    context={
                        "format": "pdf",
                        "page": number,
                        "native_text_chars": native_chars,
                    },
                )

            pages.append(
                ExtractedPage(
                    number=number,
                    text=_ocr_page(page, number, ocr=ocr, lang=ocr_lang, render_dpi=render_dpi),
                    method=_OCR,
                    tables=tables,
                    native_text_chars=native_chars,
                    ocr_engine=ocr.name,
                    ocr_engine_version=ocr.version(),
                )
            )
    finally:
        document.close()

    return PdfExtractionResult(
        pages=pages,
        method=_OCR if any(page.was_ocr for page in pages) else _NATIVE,
        text="\n\n".join(page.text for page in pages).strip(),
        metadata=document_metadata,
    )


def _ocr_page(
    page: Any,
    number: int,
    *,
    ocr: OcrEngine,
    lang: str | None,
    render_dpi: int,
) -> str:
    """Render one page to PNG and OCR it, converting engine faults into a recorded failure."""
    try:
        pixmap = page.get_pixmap(dpi=render_dpi)
        image = pixmap.tobytes("png")
    except Exception as exc:
        raise OcrFailedError(
            f"page could not be rendered for OCR: {type(exc).__name__}",
            context={"format": "pdf", "page": number, "exception": type(exc).__name__},
        ) from exc

    try:
        return ocr.image_to_text(image, lang=lang)
    except OcrUnavailableError:
        raise
    except OcrFailedError as exc:
        raise OcrFailedError(
            exc.message,
            context={"format": "pdf", "page": number, **exc.context},
        ) from exc

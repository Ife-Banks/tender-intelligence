""":mod:`tender_intelligence.processing.representation` — the extraction representation.

This is the persisted answer to prompt 09's core question — *"what does each acquired document
contain?"* (prompt 09 §11, §12; docs/06 §6.3: "one structured, reusable 'document bundle' per
tender: plain text + metadata per document").

Shape and where each part is specified
--------------------------------------
``DocumentExtraction`` (per document) carries exactly the fields prompt 09 §11 lists: document
identity, extraction status, extraction method, language metadata, page/section boundaries,
extracted text, structured tables, provenance, processing metadata and correlation ID.

``TenderDocumentBundle`` (per tender) carries the §12 tree: ordered documents → their extraction
content → pages/sections → structured tables → language metadata → document-level statuses →
bundle-level completeness.

Boundary representation (§8)
----------------------------
Page boundaries are first-class. ``pages`` is populated for formats that have real page semantics
(PDF) and ``sections`` for formats that do not (DOCX). Exactly one of the two is populated, and a
consumer asks "where did this come from?" through ``ExtractedTable.location`` (``"page:3"`` /
``"section:7"``) — no fake PDF page numbers are invented for DOCX (prompt 09 §8).

Tables (§9)
-----------
``ExtractedTable.rows`` always holds the **complete** matrix as extracted, so no cell is ever
discarded. ``headers`` is a convenience view of the first row and ``header_convention`` records
whether that reading was assumed rather than detected — PDF and DOCX carry no machine-readable
header semantics, so claiming detection would be a fabrication.

Status vocabulary (§11, §16)
----------------------------
``extraction_status`` uses the data-model values from
``db.models.documents.EXTRACTION_STATUSES`` (``pending``/``extracted``/``failed``/``skipped``).
docs/03 §3.2 enumerates no extraction statuses and docs/04 §4.3 defines ``ocr_failed`` as a
machine-readable *error code*, so ``ocr_failed``/``parse_failed`` are recorded in
``error_code``/``Document.extraction_error_code`` rather than invented as statuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tender_intelligence.processing.versions import (
    BUNDLE_SCHEMA_VERSION,
    PROCESSOR_NAME,
    PROCESSOR_VERSION,
    content_fingerprint,
)

#: Extraction methods recorded per document (docs/06 §6.2 metadata: native/OCR/vision/DOCX).
#: ``none`` means no extraction was performed (skipped or failed before extraction).
EXTRACTION_METHODS: tuple[str, ...] = ("native_pdf", "ocr", "docx", "none")

#: Per-page extraction methods.
PAGE_METHODS: tuple[str, ...] = ("native_pdf", "ocr")


@dataclass(frozen=True)
class ExtractedTable:
    """One structured table (prompt 09 §9).

    ``rows`` is the complete rectangular matrix as extracted; ``headers`` mirrors the first row
    when the table has more than one row and ``header_convention`` states that this is an assumed
    reading (``"first_row"``) rather than a detected header (``"none"`` when there is no such row).
    """

    location: str
    index: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    header_convention: str = "none"
    page: int | None = None
    bbox: list[float] | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def to_text(self) -> str:
        """Whitespace textual rendering, offered *alongside* the structured form (prompt 09 §9).

        The first row is printed once — as the header line when it was taken as headers, otherwise
        as an ordinary row — so the rendering never duplicates it.
        """
        if self.header_convention == "first_row" and self.headers:
            lines = [" | ".join(self.headers)]
            lines.extend(" | ".join(row) for row in self.rows[1:])
            return "\n".join(lines)
        return "\n".join(" | ".join(row) for row in self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "index": self.index,
            "headers": list(self.headers),
            "rows": [list(row) for row in self.rows],
            "header_convention": self.header_convention,
            "page": self.page,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "row_count": self.row_count,
            "column_count": self.column_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedTable:
        bbox = data.get("bbox")
        return cls(
            location=str(data["location"]),
            index=int(data["index"]),
            headers=[str(cell) for cell in data.get("headers", [])],
            rows=[[str(cell) for cell in row] for row in data.get("rows", [])],
            header_convention=str(data.get("header_convention", "none")),
            page=data.get("page"),
            bbox=[float(value) for value in bbox] if bbox is not None else None,
        )


@dataclass(frozen=True)
class ExtractedPage:
    """One PDF page and its content (prompt 09 §4, §8). Numbers are 1-based."""

    number: int
    text: str
    method: str
    tables: list[ExtractedTable] = field(default_factory=list)
    native_text_chars: int = 0
    ocr_engine: str | None = None
    ocr_engine_version: str | None = None

    @property
    def location(self) -> str:
        return f"page:{self.number}"

    @property
    def was_ocr(self) -> bool:
        return self.method == "ocr"

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "text": self.text,
            "method": self.method,
            "location": self.location,
            "native_text_chars": self.native_text_chars,
            "ocr_engine": self.ocr_engine,
            "ocr_engine_version": self.ocr_engine_version,
            "tables": [table.to_dict() for table in self.tables],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedPage:
        return cls(
            number=int(data["number"]),
            text=str(data.get("text", "")),
            method=str(data.get("method", "native_pdf")),
            tables=[ExtractedTable.from_dict(item) for item in data.get("tables", [])],
            native_text_chars=int(data.get("native_text_chars", 0)),
            ocr_engine=data.get("ocr_engine"),
            ocr_engine_version=data.get("ocr_engine_version"),
        )


@dataclass(frozen=True)
class ExtractedSection:
    """One structural block, in document order, for formats without page semantics (prompt 09 §8).

    ``kind`` is ``"paragraph"`` or ``"table"``. DOCX blocks come from python-docx's
    ``iter_inner_content()``, which preserves the true interleaving of paragraphs and tables that
    the separate ``.paragraphs`` / ``.tables`` collections lose.
    """

    index: int
    kind: str
    text: str
    tables: list[ExtractedTable] = field(default_factory=list)

    @property
    def location(self) -> str:
        return f"section:{self.index}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "text": self.text,
            "location": self.location,
            "tables": [table.to_dict() for table in self.tables],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedSection:
        return cls(
            index=int(data["index"]),
            kind=str(data.get("kind", "paragraph")),
            text=str(data.get("text", "")),
            tables=[ExtractedTable.from_dict(item) for item in data.get("tables", [])],
        )


@dataclass(frozen=True)
class ProcessingMetadata:
    """How an extraction was produced (prompt 09 §15)."""

    processor: str = PROCESSOR_NAME
    processor_version: str = PROCESSOR_VERSION
    config_version: str = ""
    library_versions: dict[str, str | None] = field(default_factory=dict)
    ocr_engine: str | None = None
    ocr_engine_version: str | None = None
    vision_provider: str | None = None
    vision_model: str | None = None
    vision_profile: str | None = None
    processed_at: str = ""
    correlation_id: str | None = None
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "processor": self.processor,
            "processor_version": self.processor_version,
            "config_version": self.config_version,
            "library_versions": dict(self.library_versions),
            "ocr_engine": self.ocr_engine,
            "ocr_engine_version": self.ocr_engine_version,
            "vision_provider": self.vision_provider,
            "vision_model": self.vision_model,
            "vision_profile": self.vision_profile,
            "processed_at": self.processed_at,
            "correlation_id": self.correlation_id,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessingMetadata:
        return cls(
            processor=str(data.get("processor", PROCESSOR_NAME)),
            processor_version=str(data.get("processor_version", "")),
            config_version=str(data.get("config_version", "")),
            library_versions=dict(data.get("library_versions", {})),
            ocr_engine=data.get("ocr_engine"),
            ocr_engine_version=data.get("ocr_engine_version"),
            vision_provider=data.get("vision_provider"),
            vision_model=data.get("vision_model"),
            vision_profile=data.get("vision_profile"),
            processed_at=str(data.get("processed_at", "")),
            correlation_id=data.get("correlation_id"),
            skip_reason=data.get("skip_reason"),
        )


@dataclass(frozen=True)
class DocumentExtraction:
    """Everything understood about one document (prompt 09 §11)."""

    document_id: int
    tender_id: int
    filename: str
    source_url: str
    extraction_status: str
    extraction_method: str
    text: str = ""
    mime_type: str | None = None
    checksum: str | None = None
    storage_path: str | None = None
    language: str | None = None
    language_confidence: float | None = None
    language_source: str = "unknown"
    error_code: str | None = None
    pages: list[ExtractedPage] = field(default_factory=list)
    sections: list[ExtractedSection] = field(default_factory=list)
    archive_parent_url: str | None = None
    metadata: ProcessingMetadata = field(default_factory=ProcessingMetadata)

    @property
    def tables(self) -> list[ExtractedTable]:
        """Every table in the document, in page/section order (prompt 09 §9).

        A derived view, not a stored field: table content is persisted once, under its page or
        section, and each table carries ``location`` (``"page:3"`` / ``"section:7"``) so a
        consumer can still answer "where did this table come from?" (prompt 09 §8, §9).
        """
        collected: list[ExtractedTable] = []
        for page in self.pages:
            collected.extend(page.tables)
        for section in self.sections:
            collected.extend(section.tables)
        return collected

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def is_extracted(self) -> bool:
        return self.extraction_status == "extracted"

    @property
    def is_failed(self) -> bool:
        return self.extraction_status == "failed"

    @property
    def is_skipped(self) -> bool:
        return self.extraction_status == "skipped"

    @property
    def ocr_page_count(self) -> int:
        return sum(1 for page in self.pages if page.was_ocr)

    @property
    def content_fingerprint(self) -> str:
        """Deterministic hash of the extracted content only (prompt 09 §14).

        Excludes timestamps, versions and correlation IDs, so processing the same bytes with the
        same processor always yields the same fingerprint.
        """
        return content_fingerprint(
            {
                "filename": self.filename,
                "extraction_method": self.extraction_method,
                "language": self.language,
                "text": self.text,
                "pages": [
                    {
                        "number": page.number,
                        "text": page.text,
                        "method": page.method,
                        "tables": [table.to_dict() for table in page.tables],
                    }
                    for page in self.pages
                ],
                "sections": [
                    {
                        "index": section.index,
                        "kind": section.kind,
                        "text": section.text,
                        "tables": [table.to_dict() for table in section.tables],
                    }
                    for section in self.sections
                ],
            }
        )

    @property
    def artifact_key_segment(self) -> str:
        """Storage-key segment identifying this document's bytes (checksum, else its id)."""
        return self.checksum or f"doc-{self.document_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "tender_id": self.tender_id,
            "filename": self.filename,
            "source_url": self.source_url,
            "mime_type": self.mime_type,
            "checksum": self.checksum,
            "storage_path": self.storage_path,
            "language": self.language,
            "language_confidence": self.language_confidence,
            "language_source": self.language_source,
            "extraction_status": self.extraction_status,
            "extraction_method": self.extraction_method,
            "error_code": self.error_code,
            "page_count": self.page_count,
            "ocr_page_count": self.ocr_page_count,
            "archive_parent_url": self.archive_parent_url,
            "content_fingerprint": self.content_fingerprint,
            "text": self.text,
            "pages": [page.to_dict() for page in self.pages],
            "sections": [section.to_dict() for section in self.sections],
            "metadata": self.metadata.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DocumentExtraction:
        return cls(
            document_id=int(data["document_id"]),
            tender_id=int(data["tender_id"]),
            filename=str(data["filename"]),
            source_url=str(data.get("source_url", "")),
            extraction_status=str(data["extraction_status"]),
            extraction_method=str(data.get("extraction_method", "none")),
            text=str(data.get("text", "")),
            mime_type=data.get("mime_type"),
            checksum=data.get("checksum"),
            storage_path=data.get("storage_path"),
            language=data.get("language"),
            language_confidence=data.get("language_confidence"),
            language_source=str(data.get("language_source", "unknown")),
            error_code=data.get("error_code"),
            pages=[ExtractedPage.from_dict(item) for item in data.get("pages", [])],
            sections=[ExtractedSection.from_dict(item) for item in data.get("sections", [])],
            archive_parent_url=data.get("archive_parent_url"),
            metadata=ProcessingMetadata.from_dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class TenderDocumentBundle:
    """One reusable bundle per tender (prompt 09 §12; docs/06 §6.3)."""

    tender_id: int
    documents: list[DocumentExtraction] = field(default_factory=list)
    incomplete_inputs: bool = False
    languages: list[str] = field(default_factory=list)
    metadata: ProcessingMetadata = field(default_factory=ProcessingMetadata)
    schema_version: int = BUNDLE_SCHEMA_VERSION
    created_at: str = ""

    @property
    def extracted_documents(self) -> list[DocumentExtraction]:
        return [document for document in self.documents if document.is_extracted]

    @property
    def failed_documents(self) -> list[DocumentExtraction]:
        return [document for document in self.documents if document.is_failed]

    @property
    def skipped_documents(self) -> list[DocumentExtraction]:
        """Documents with no extractable content — recorded so they are never hidden (§16)."""
        return [document for document in self.documents if document.is_skipped]

    @property
    def document_count(self) -> int:
        return len(self.documents)

    def document_by_id(self, document_id: int) -> DocumentExtraction | None:
        for document in self.documents:
            if document.document_id == document_id:
                return document
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tender_id": self.tender_id,
            "created_at": self.created_at,
            "incomplete_inputs": self.incomplete_inputs,
            "languages": list(self.languages),
            "document_count": self.document_count,
            "extracted_count": len(self.extracted_documents),
            "failed_count": len(self.failed_documents),
            "skipped_count": len(self.skipped_documents),
            "metadata": self.metadata.to_dict(),
            "documents": [document.to_dict() for document in self.documents],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TenderDocumentBundle:
        return cls(
            tender_id=int(data["tender_id"]),
            documents=[DocumentExtraction.from_dict(item) for item in data.get("documents", [])],
            incomplete_inputs=bool(data.get("incomplete_inputs", False)),
            languages=[str(item) for item in data.get("languages", [])],
            metadata=ProcessingMetadata.from_dict(data.get("metadata", {})),
            schema_version=int(data.get("schema_version", BUNDLE_SCHEMA_VERSION)),
            created_at=str(data.get("created_at", "")),
        )

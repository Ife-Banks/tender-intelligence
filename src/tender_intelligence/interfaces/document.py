""":mod:`tender_intelligence.interfaces.document` — DocumentProcessor seam (docs/06, docs/02 §2.10).

Converts fetched files into one reusable structured "document bundle": plain text + metadata
per document, ready for the AI stage. Supports native PDF, scanned PDF (OCR), DOCX, ZIP
(unzip + recurse), tables and multi-language documents. Per-document failures must never
block the rest of the bundle (docs/06 §6.4).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tender_intelligence.core.errors import OCR_FAILED


@dataclass(frozen=True)
class ExtractedDocument:
    """One extracted document inside a bundle (docs/06 §6.2, DM Document)."""

    filename: str
    source_url: str
    mime_type: str | None
    language: str | None
    checksum: str
    extraction_method: str  # e.g. "native_pdf", "ocr", "docx", "zip"
    text: str
    pages: list[str] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    error_code: str | None = None


@dataclass(frozen=True)
class DocumentBundle:
    """Reusable per-tender extraction output handed to the AI stage (docs/06 §6.3)."""

    documents: list[ExtractedDocument] = field(default_factory=list)
    incomplete_inputs: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def failed_documents(self) -> list[ExtractedDocument]:
        """Documents whose extraction failed; they carry an ``error_code``."""
        return [d for d in self.documents if d.error_code is not None]


class DocumentProcessingError(Exception):
    """Fatal bundle-level failure (as opposed to a per-document error)."""

    def __init__(self, message: str, error_code: str = OCR_FAILED) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code


class DocumentProcessor(ABC):
    """Abstract document-understanding seam.

    Implementations must tolerate individual document failures (record them on the
    ``ExtractedDocument`` and set ``incomplete_inputs``) rather than raising for one bad
    file (docs/06 §6.4). A raise is reserved for bundle-level failures that prevent any
    output being produced.
    """

    @abstractmethod
    def process(self, document_paths: list[str]) -> DocumentBundle:
        """Extract text/table/language data from *document_paths* into a bundle."""

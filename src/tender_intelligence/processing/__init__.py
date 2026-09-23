""":mod:`tender_intelligence.processing` — document understanding (prompt 09).

Turns acquired document bytes into a deterministic, reusable, persisted representation of
document content, and assembles one reusable bundle per tender. Verdicting, triage and
notification belong to later stages (prompt 09 §1, §17).
"""

from __future__ import annotations

from tender_intelligence.processing.docx import DocxExtractionResult, extract_docx
from tender_intelligence.processing.errors import (
    OcrFailedError,
    OcrUnavailableError,
    ParseFailedError,
    ProcessingError,
)
from tender_intelligence.processing.languages import (
    SUPPORTED_LANGUAGES,
    bundle_languages,
    detect_language,
    normalize_language,
)
from tender_intelligence.processing.ocr import OcrEngine, TesseractOcrEngine
from tender_intelligence.processing.pdf import PdfExtractionResult, extract_pdf
from tender_intelligence.processing.representation import (
    DocumentExtraction,
    ExtractedPage,
    ExtractedSection,
    ExtractedTable,
    ProcessingMetadata,
    TenderDocumentBundle,
)
from tender_intelligence.processing.service import (
    DocumentProcessingConfig,
    DocumentProcessingService,
    DocumentSnapshot,
    TenderDocumentProcessor,
    bundle_fingerprint,
    detect_format,
)
from tender_intelligence.processing.store import ExtractionStore, artifact_key, bundle_key
from tender_intelligence.processing.versions import (
    BUNDLE_SCHEMA_VERSION,
    EXTRACTION_CONFIG_VERSION,
    PROCESSOR_NAME,
    PROCESSOR_VERSION,
    content_fingerprint,
    library_versions,
)

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "EXTRACTION_CONFIG_VERSION",
    "PROCESSOR_NAME",
    "PROCESSOR_VERSION",
    "SUPPORTED_LANGUAGES",
    "DocxExtractionResult",
    "DocumentExtraction",
    "DocumentProcessingConfig",
    "DocumentProcessingService",
    "DocumentSnapshot",
    "ExtractedPage",
    "ExtractedSection",
    "ExtractedTable",
    "ExtractionStore",
    "OcrEngine",
    "OcrFailedError",
    "OcrUnavailableError",
    "ParseFailedError",
    "PdfExtractionResult",
    "ProcessingError",
    "ProcessingMetadata",
    "TenderDocumentBundle",
    "TenderDocumentProcessor",
    "TesseractOcrEngine",
    "artifact_key",
    "bundle_fingerprint",
    "bundle_key",
    "bundle_languages",
    "content_fingerprint",
    "detect_format",
    "detect_language",
    "extract_docx",
    "extract_pdf",
    "library_versions",
    "normalize_language",
]

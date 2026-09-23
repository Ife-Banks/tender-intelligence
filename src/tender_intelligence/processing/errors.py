""":mod:`tender_intelligence.processing.errors` — processing-internal failure taxonomy.

Per-document failures are *results*, not exceptions (prompt 09 §16): one bad document must never
abort a tender. These types cover the two categories that genuinely interrupt the extraction of a
single document — the stored bytes are not the format they claim to be, or OCR was required and
could not be performed — plus documented skip reasons.

Error codes come from :mod:`tender_intelligence.core.errors` so that persisted failures stay
machine-readable and comparable with the docs/04 §4.3 catalogue.
"""

from __future__ import annotations

from typing import Any, Final

from tender_intelligence.core.errors import OCR_FAILED, PARSE_FAILED

# --------------------------------------------------------------------------- skip reasons
# Recorded in extraction processing metadata. A skipped document is *not* a failed one: it never
# had extractable content, so it does not by itself make a bundle's inputs incomplete (docs/06
# §6.5 triggers on documents that failed to download or extract). See docs/13-open-decisions.md O22.

#: The file is an archive container whose members are already separate Document rows (prompt 09 §7).
SKIP_ARCHIVE_CONTAINER: Final[str] = "archive_container"

#: The format is identifiable but the specification does not require extracting it (prompt 09 §19).
SKIP_UNSUPPORTED_FORMAT: Final[str] = "unsupported_format"

#: Acquisition already recorded a download failure; there are no stored bytes to process.
SKIP_ACQUISITION_FAILED: Final[str] = "acquisition_failed"

#: The row claims a successful download but carries no storage reference (prompt 09 §3).
SKIP_STORAGE_REFERENCE_MISSING: Final[str] = "storage_reference_missing"

#: The storage reference exists but the object is gone (prompt 09 §3: an explicit processing
#: failure, never a silent re-download from the source URL).
SKIP_SOURCE_BYTES_UNAVAILABLE: Final[str] = "source_bytes_unavailable"


class ProcessingError(Exception):
    """A failure that prevents extracting one document's content.

    The message must never contain document content (prompt 09 §18) — it names the failure and
    carries structured, non-sensitive context instead.
    """

    def __init__(
        self,
        message: str,
        error_code: str = PARSE_FAILED,
        *,
        category: str = "processing",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.category = category
        self.context = context or {}


class ParseFailedError(ProcessingError):
    """The stored bytes are not decodable as their declared format (prompt 09 §16)."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, PARSE_FAILED, category="parse_failed", context=context)


class OcrUnavailableError(ProcessingError):
    """OCR was required for a page but no usable OCR engine is available (prompt 09 §4.2, §5).

    Raised rather than silently returning partial native text: a scanned page with no text layer
    has no content to fall back to, and reporting it as extracted would fabricate understanding.
    """

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, OCR_FAILED, category="ocr_unavailable", context=context)


class OcrFailedError(ProcessingError):
    """The OCR engine ran but failed for a page (prompt 09 §16)."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, OCR_FAILED, category="ocr_failed", context=context)

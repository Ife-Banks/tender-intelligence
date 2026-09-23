""":mod:`tender_intelligence.acquisition.errors` — structured acquisition failures (prompt 08 §12).

Uses the authoritative error-code taxonomy (``docs/04`` §4.3): acquisition failures carry
``document_download_failed`` and structured context (document identity, safe URL reference,
HTTP/status category where available, failure category, retryability, correlation ID) so the
pipeline and alerts can act without parsing prose. Secrets are never included.
"""

from __future__ import annotations

from typing import Final

from tender_intelligence.core.errors import DOCUMENT_DOWNLOAD_FAILED

# Failure categories (diagnostic, not error codes). One machine-readable axis.
HTTP_ERROR: Final[str] = "http_error"
TRANSPORT: Final[str] = "transport"
UNSUPPORTED_SCHEME: Final[str] = "unsupported_scheme"
INVALID_URL: Final[str] = "invalid_url"
RESPONSE_TOO_LARGE: Final[str] = "response_too_large"
INVALID_RESPONSE: Final[str] = "invalid_response"
INVALID_ARCHIVE: Final[str] = "invalid_archive"
ARCHIVE_LIMIT: Final[str] = "archive_limit"
UNSAFE_ARCHIVE_MEMBER: Final[str] = "unsafe_archive_member"
STORAGE_FAILED: Final[str] = "storage_failed"
PERSISTENCE_FAILED: Final[str] = "persistence_failed"


class DocumentAcquisitionError(Exception):
    """A single-attachment acquisition failure carrying structured diagnostics.

    Not fatal to the tender: the :class:`DocumentAcquisitionService` records it as a
    per-document ``failed`` state and continues with the remaining attachments
    (prompt 08 §11, docs/06 §6.4).
    """

    def __init__(
        self,
        message: str,
        *,
        error_code: str = DOCUMENT_DOWNLOAD_FAILED,
        category: str = TRANSPORT,
        retryable: bool = False,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.category = category
        self.retryable = retryable
        self.context = context or {}
        self.context.setdefault("error_code", error_code)
        self.context.setdefault("category", category)
        self.context.setdefault("retryable", retryable)

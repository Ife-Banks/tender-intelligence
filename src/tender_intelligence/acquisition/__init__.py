""":mod:`tender_intelligence.acquisition` — document acquisition stage (prompt 08).

Downloads discovered :class:`TenderAttachment` bytes under the crawl policy, validates and
checksums them, stores them through the ``ObjectStorage`` seam, expands ZIP archives
safely, and records per-document state via :class:`DocumentRepository` with the exact
model statuses (``pending``/``downloaded``/``failed``).

Public entry point: :class:`tender_intelligence.acquisition.service.DocumentAcquisitionService`.
"""

from __future__ import annotations

from tender_intelligence.acquisition.checksums import CHECKSUM_ALGORITHM, checksum_of
from tender_intelligence.acquisition.errors import (
    ARCHIVE_LIMIT,
    HTTP_ERROR,
    INVALID_ARCHIVE,
    INVALID_RESPONSE,
    INVALID_URL,
    PERSISTENCE_FAILED,
    RESPONSE_TOO_LARGE,
    STORAGE_FAILED,
    TRANSPORT,
    UNSAFE_ARCHIVE_MEMBER,
    UNSUPPORTED_SCHEME,
    DocumentAcquisitionError,
)
from tender_intelligence.acquisition.fetcher import (
    DocumentFetcher,
    DocumentResponse,
    redact_url,
)
from tender_intelligence.acquisition.mimes import bare_media_type, guess_mime
from tender_intelligence.acquisition.names import sanitize_storage_name, storage_key
from tender_intelligence.acquisition.service import (
    AcquiredDocument,
    AcquisitionFailure,
    AcquisitionResult,
    DocumentAcquisitionService,
)
from tender_intelligence.acquisition.zip import (
    ZipArchiveReport,
    ZipEntry,
    ZipLimits,
    ZipMemberFailure,
    looks_like_zip,
    recurse_depth_exceeded,
    safe_extract_archive,
)

__all__ = [
    "AcquiredDocument",
    "AcquisitionFailure",
    "AcquisitionResult",
    "ARCHIVE_LIMIT",
    "CHECKSUM_ALGORITHM",
    "DocumentAcquisitionError",
    "DocumentAcquisitionService",
    "DocumentFetcher",
    "DocumentResponse",
    "HTTP_ERROR",
    "INVALID_ARCHIVE",
    "INVALID_RESPONSE",
    "INVALID_URL",
    "PERSISTENCE_FAILED",
    "RESPONSE_TOO_LARGE",
    "STORAGE_FAILED",
    "TRANSPORT",
    "UNSAFE_ARCHIVE_MEMBER",
    "UNSUPPORTED_SCHEME",
    "ZipArchiveReport",
    "ZipEntry",
    "ZipLimits",
    "ZipMemberFailure",
    "bare_media_type",
    "checksum_of",
    "guess_mime",
    "looks_like_zip",
    "redact_url",
    "recurse_depth_exceeded",
    "safe_extract_archive",
    "sanitize_storage_name",
    "storage_key",
]

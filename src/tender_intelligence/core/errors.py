"""Machine-readable error codes used across the pipeline.

Taxonomy source: ``docs/04-pipeline-spec.md`` (error-code catalogue defined by the
specification). Every structured log line and failure result must carry one of these.
"""

from __future__ import annotations

from typing import Final

# Source acquisition
SOURCE_UNREACHABLE: Final[str] = "source_unreachable"
PARSER_MISMATCH: Final[str] = "parser_mismatch"

# Document handling
DOCUMENT_DOWNLOAD_FAILED: Final[str] = "document_download_failed"
OCR_FAILED: Final[str] = "ocr_failed"

# AI assessment
AI_CALL_TIMEOUT: Final[str] = "ai_call_timeout"
AI_INVALID_OUTPUT: Final[str] = "ai_invalid_output"
BUDGET_EXCEEDED: Final[str] = "budget_exceeded"

# Notification
EMAIL_SEND_FAILED: Final[str] = "email_send_failed"
PROVIDER_FAILOVER: Final[str] = "provider_failover"

ERROR_CODES: Final[tuple[str, ...]] = (
    SOURCE_UNREACHABLE,
    PARSER_MISMATCH,
    DOCUMENT_DOWNLOAD_FAILED,
    OCR_FAILED,
    AI_CALL_TIMEOUT,
    AI_INVALID_OUTPUT,
    BUDGET_EXCEEDED,
    EMAIL_SEND_FAILED,
    PROVIDER_FAILOVER,
)

ERROR_CODE_VALUES: Final[frozenset[str]] = frozenset(ERROR_CODES)


def is_valid_error_code(code: str) -> bool:
    """Return True when *code* is one of the recognised error codes."""
    return code in ERROR_CODE_VALUES
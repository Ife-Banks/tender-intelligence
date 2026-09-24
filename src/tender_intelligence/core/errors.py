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
# Required by prompt 09 §16 (a document whose bytes could not be parsed/decoded as its declared
# format). docs/04 §4.3's catalogue is explicitly non-exhaustive, so this is an addition to it,
# never a replacement for OCR_FAILED.
PARSE_FAILED: Final[str] = "parse_failed"

# AI assessment
AI_CALL_TIMEOUT: Final[str] = "ai_call_timeout"
AI_INVALID_OUTPUT: Final[str] = "ai_invalid_output"
BUDGET_EXCEEDED: Final[str] = "budget_exceeded"

# Notification (docs/04 §4.3's catalogue is explicitly non-exhaustive; prompt 11 §7 requires
# the delivery layer to distinguish retryable (transient) from permanent provider failures
# with machine-readable codes, so the low-level codes below are additions to it).
EMAIL_SEND_FAILED: Final[str] = "email_send_failed"
PROVIDER_FAILOVER: Final[str] = "provider_failover"
# Transient classes (bounded retry, then failover — docs/08 §8.3).
MAIL_TIMEOUT: Final[str] = "mail_timeout"
MAIL_RATE_LIMITED: Final[str] = "mail_rate_limited"
MAIL_HTTP_5XX: Final[str] = "mail_http_5xx"
# Permanent classes (fail over without retry — docs/08 §8.3).
MAIL_INVALID_CREDENTIALS: Final[str] = "mail_invalid_credentials"
MAIL_INVALID_RECIPIENT: Final[str] = "mail_invalid_recipient"
MAIL_CONFIGURATION_ERROR: Final[str] = "mail_configuration_error"
MAIL_UNSUPPORTED_REQUEST: Final[str] = "mail_unsupported_request"
MAIL_QUOTA_EXHAUSTED: Final[str] = "mail_quota_exhausted"
# Notification-layer lifecycle.
MAIL_BREAKER_OPEN: Final[str] = "mail_breaker_open"
MAIL_ALL_PROVIDERS_FAILED: Final[str] = "mail_all_providers_failed"
MAIL_LOCAL_RATE_LIMIT: Final[str] = "mail_local_rate_limit"
NOTIFICATION_NO_VERDICT: Final[str] = "notification_no_verdict"
NOTIFICATION_INVALID_VERDICT: Final[str] = "notification_invalid_verdict"
NOTIFICATION_ROUTING_FAILED: Final[str] = "notification_routing_failed"

# Deduplication / pipeline integrity (docs/04 §4.3 catalogue is explicitly non-exhaustive;
# these codes are required by prompt 05 §13's no-silent-failure handling).
DEDUP_TRANSACTION_FAILED: Final[str] = "dedup_transaction_failed"
DEDUP_MISSING_IDENTITY: Final[str] = "dedup_missing_identity"
DEDUP_INVALID_STATE: Final[str] = "dedup_invalid_state"
DEDUP_MALFORMED_CANDIDATE: Final[str] = "dedup_malformed_candidate"

# Persistence layer (prompt 06 §4, §6, §10). The docs/04 §4.3 catalogue is non-exhaustive;
# these codes let repository failures surface as machine-readable, loggable errors.
PERSISTENCE_NOT_FOUND: Final[str] = "persistence_not_found"
PERSISTENCE_CONSTRAINT_VIOLATION: Final[str] = "persistence_constraint_violation"
PERSISTENCE_INVALID_STATUS_TRANSITION: Final[str] = "persistence_invalid_status_transition"
PERSISTENCE_TRANSACTION_FAILED: Final[str] = "persistence_transaction_failed"

# Pipeline orchestration (prompt 10 §10). docs/04 §4.3's catalogue is non-exhaustive, and §10
# permits a new code only for a condition that is genuinely orchestration-specific — which is
# what these three are. Every *stage* failure reuses the owning stage's own code where one
# exists (`source_unreachable`, `dedup_*`, `persistence_*`, ...); STAGE_FAILED covers only the
# residual case where a stage raised without a code of its own to carry.
CONFIGURATION_LOAD_FAILED: Final[str] = "configuration_load_failed"
SOURCE_NOT_RUNNABLE: Final[str] = "source_not_runnable"
STAGE_FAILED: Final[str] = "stage_failed"

ERROR_CODES: Final[tuple[str, ...]] = (
    SOURCE_UNREACHABLE,
    PARSER_MISMATCH,
    DOCUMENT_DOWNLOAD_FAILED,
    OCR_FAILED,
    PARSE_FAILED,
    AI_CALL_TIMEOUT,
    AI_INVALID_OUTPUT,
    BUDGET_EXCEEDED,
    EMAIL_SEND_FAILED,
    PROVIDER_FAILOVER,
    MAIL_TIMEOUT,
    MAIL_RATE_LIMITED,
    MAIL_HTTP_5XX,
    MAIL_INVALID_CREDENTIALS,
    MAIL_INVALID_RECIPIENT,
    MAIL_CONFIGURATION_ERROR,
    MAIL_UNSUPPORTED_REQUEST,
    MAIL_QUOTA_EXHAUSTED,
    MAIL_BREAKER_OPEN,
    MAIL_ALL_PROVIDERS_FAILED,
    MAIL_LOCAL_RATE_LIMIT,
    NOTIFICATION_NO_VERDICT,
    NOTIFICATION_INVALID_VERDICT,
    NOTIFICATION_ROUTING_FAILED,
    DEDUP_TRANSACTION_FAILED,
    DEDUP_MISSING_IDENTITY,
    DEDUP_INVALID_STATE,
    DEDUP_MALFORMED_CANDIDATE,
    PERSISTENCE_NOT_FOUND,
    PERSISTENCE_CONSTRAINT_VIOLATION,
    PERSISTENCE_INVALID_STATUS_TRANSITION,
    PERSISTENCE_TRANSACTION_FAILED,
    CONFIGURATION_LOAD_FAILED,
    SOURCE_NOT_RUNNABLE,
    STAGE_FAILED,
)

ERROR_CODE_VALUES: Final[frozenset[str]] = frozenset(ERROR_CODES)


def is_valid_error_code(code: str) -> bool:
    """Return True when *code* is one of the recognised error codes."""
    return code in ERROR_CODE_VALUES

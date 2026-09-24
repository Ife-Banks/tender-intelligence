"""Provider failure taxonomy for the notification chain (docs/08 §8.3).

Adapters convert transport/HTTP details into :class:`MailError` before the chain sees them.
The chain therefore makes retry/failover decisions from a machine-readable classification,
never by matching free-text provider messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from tender_intelligence.core.errors import (
    MAIL_CONFIGURATION_ERROR,
    MAIL_HTTP_5XX,
    MAIL_INVALID_CREDENTIALS,
    MAIL_INVALID_RECIPIENT,
    MAIL_QUOTA_EXHAUSTED,
    MAIL_RATE_LIMITED,
    MAIL_TIMEOUT,
    MAIL_UNSUPPORTED_REQUEST,
)


class MailError(Exception):
    """A provider-level failure safe to persist and classify.

    ``message`` is intentionally short and provider-neutral.  Adapters must not put response
    bodies, URLs containing credentials, or document contents in it.  ``retry_after_seconds``
    carries only a numeric provider hint (for example HTTP ``Retry-After``), never the raw
    response.
    """

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = False,
        permanent: bool = False,
        possible_duplicate: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.retryable = bool(retryable)
        self.permanent = bool(permanent)
        self.possible_duplicate = bool(possible_duplicate)
        self.retry_after_seconds = (
            max(0.0, float(retry_after_seconds)) if retry_after_seconds is not None else None
        )


def retryable(
    code: str,
    message: str,
    *,
    possible_duplicate: bool = False,
    retry_after_seconds: float | None = None,
) -> MailError:
    """Construct a transient failure (bounded retry, then failover)."""

    return MailError(
        code,
        message,
        retryable=True,
        possible_duplicate=possible_duplicate,
        retry_after_seconds=retry_after_seconds,
    )


def permanent(code: str, message: str, *, possible_duplicate: bool = False) -> MailError:
    """Construct a permanent failure (fail over immediately, never retry)."""

    return MailError(code, message, permanent=True, possible_duplicate=possible_duplicate)


def timeout_error(message: str = "provider timed out") -> MailError:
    """A timeout after a request may have reached the provider."""

    return retryable(MAIL_TIMEOUT, message, possible_duplicate=True)


def quota_exhausted(message: str = "provider quota exhausted") -> MailError:
    """A daily/monthly provider quota has been exhausted; this is permanent for this send."""

    return permanent(MAIL_QUOTA_EXHAUSTED, message)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        return max(0.0, (target - datetime.now(UTC)).total_seconds())


def http_status_error(
    status_code: int,
    *,
    retry_after: str | None = None,
    error_code: str | None = None,
) -> MailError:
    """Classify an HTTP response using the documented provider taxonomy.

    The optional ``error_code`` is a short provider code, not a response body.  It is used
    only to distinguish an explicit quota/invalid-recipient condition from a generic 4xx.
    """

    provider_code = (error_code or "").lower()
    if status_code in (401, 403):
        return permanent(MAIL_INVALID_CREDENTIALS, f"provider rejected credentials ({status_code})")
    if status_code == 408:
        return retryable(MAIL_TIMEOUT, "provider request timed out (408)", possible_duplicate=True)
    if status_code == 429:
        if "quota" in provider_code or "daily" in provider_code or "monthly" in provider_code:
            return quota_exhausted("provider quota exhausted")
        return retryable(
            MAIL_RATE_LIMITED,
            "provider rate limit reached (429)",
            retry_after_seconds=_retry_after_seconds(retry_after),
        )
    if status_code in (400, 422):
        if "recipient" in provider_code or "address" in provider_code or "email" in provider_code:
            return permanent(MAIL_INVALID_RECIPIENT, "provider rejected a recipient address")
        return permanent(MAIL_UNSUPPORTED_REQUEST, f"provider rejected the request ({status_code})")
    if 500 <= status_code <= 599:
        return retryable(MAIL_HTTP_5XX, f"provider returned {status_code}")
    return permanent(MAIL_UNSUPPORTED_REQUEST, f"provider returned {status_code}")


def configuration_error(message: str) -> MailError:
    """Provider cannot be used because its configuration is invalid (permanent)."""

    return permanent(MAIL_CONFIGURATION_ERROR, message)


def invalid_recipient(message: str = "provider rejected a recipient address") -> MailError:
    """A recipient address was rejected by the provider (permanent)."""

    return permanent(MAIL_INVALID_RECIPIENT, message)

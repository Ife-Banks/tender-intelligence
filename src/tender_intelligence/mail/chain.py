"""Provider selection, bounded retry, failover, and attempt capture (docs/08 §8.3).

The chain is transport- and database-agnostic.  It returns every attempt it made; the
notification service persists those values and owns the durable outbox transaction.  Keeping
that boundary explicit makes it possible to use the same seam with a mock server in tests and
with Sendlib (or a later, selected provider) in production.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from tender_intelligence.core.errors import (
    EMAIL_SEND_FAILED,
    MAIL_ALL_PROVIDERS_FAILED,
    MAIL_BREAKER_OPEN,
    MAIL_LOCAL_RATE_LIMIT,
)
from tender_intelligence.mail.breaker import BreakerState, CircuitBreaker
from tender_intelligence.mail.errors import MailError
from tender_intelligence.mail.message import EmailMessage
from tender_intelligence.mail.planner import AttachmentPlanner
from tender_intelligence.mail.provider import Capabilities, MailProvider, SendError

log = logging.getLogger("tender_intelligence.mail.chain")


@dataclass(frozen=True)
class MailRetryPolicy:
    """Bounded exponential backoff for one provider.

    ``attempts`` is the total number of tries.  Thus the documented default of two retries is
    represented by ``attempts=3``; ``attempts=1`` disables retry.  ``retries`` is exposed as a
    read-only property to make the policy's intent unambiguous to callers.
    """

    attempts: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be >= 1")
        if self.backoff_base_seconds < 0 or self.backoff_max_seconds < 0:
            raise ValueError("backoff values must be >= 0")

    @property
    def retries(self) -> int:
        return self.attempts - 1

    @property
    def max_attempts(self) -> int:
        return self.attempts

    def delay_for(self, attempt: int, retry_after_seconds: float | None = None) -> float:
        """Return the bounded delay after a failed *attempt* (1-based)."""

        if attempt < 1:
            attempt = 1
        raw = self.backoff_base_seconds * float(2 ** (attempt - 1))
        bounded = min(raw, self.backoff_max_seconds)
        if retry_after_seconds is not None:
            return max(bounded, retry_after_seconds)
        return bounded


@dataclass(frozen=True)
class ProviderEntry:
    """One configured provider in chain order, with its runtime breaker."""

    provider_id: int
    adapter: MailProvider
    breaker: CircuitBreaker
    active: bool = True
    priority: int | None = None


@dataclass(frozen=True)
class ChainAttempt:
    """One attempt through one provider, ready for ``NotificationAttempt`` persistence."""

    provider_id: int | None
    provider_name: str
    attempt_number: int
    status: str  # "sent" | "failed"
    error_code: str | None = None
    duration_ms: int | None = None
    possible_duplicate: bool = False
    provider_message_id: str | None = None
    permanent: bool = False


@dataclass(frozen=True)
class ChainResult:
    """What the whole chain did for one message (prompt 11 §6, §8)."""

    delivered: bool
    provider_used: str | None = None
    provider_id: int | None = None
    provider_message_id: str | None = None
    attempts: tuple[ChainAttempt, ...] = field(default_factory=tuple)
    error_code: str | None = None
    possible_duplicate: bool = False
    notification_status: str = "pending_retry"
    failovers: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)

    @property
    def attempts_count(self) -> int:
        return len(self.attempts)


class ProviderUsageLimiter:
    """Best-effort local enforcement of provider request limits.

    Provider limits are configuration, not business decisions.  This window prevents a
    backlog flush from hammering a provider in one process.  A provider's own 429 response is
    still authoritative and is handled with the documented backoff below; a future durable
    limiter can replace this class without changing ``MailProvider``.
    """

    def __init__(self, *, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._minute: dict[int, deque[float]] = defaultdict(deque)
        self._day: dict[int, tuple[int, int]] = {}

    def allows(self, provider_id: int, adapter: MailProvider) -> bool:
        capabilities = adapter.capabilities
        now = self._clock()
        day_number = int(now // 86_400)
        day_count, day_id = self._day.get(provider_id, (0, day_number))
        if day_id != day_number:
            day_count, day_id = 0, day_number
        if capabilities.daily_limit is not None and day_count >= capabilities.daily_limit:
            self._day[provider_id] = (day_count, day_id)
            return False
        recent = self._minute[provider_id]
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if (
            capabilities.rate_limit_per_min is not None
            and len(recent) >= capabilities.rate_limit_per_min
        ):
            self._day[provider_id] = (day_count, day_id)
            return False
        recent.append(now)
        self._day[provider_id] = (day_count + 1, day_number)
        return True


class ProviderChain:
    """Ordered provider chain with retry, failover, and circuit-breaker integration."""

    def __init__(
        self,
        providers: Sequence[ProviderEntry],
        *,
        retry_policy: MailRetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        usage_limiter: ProviderUsageLimiter | None = None,
        usage_reserver: Callable[[int, Capabilities], bool] | None = None,
        probe_claimer: Callable[[ProviderEntry], bool] | None = None,
    ) -> None:
        self.providers = list(providers)
        self.retry_policy = retry_policy or MailRetryPolicy()
        self._sleeper = sleeper
        self._usage = usage_limiter or ProviderUsageLimiter(clock=clock)
        self._usage_reserver = usage_reserver
        self._probe_claimer = probe_claimer
        self._breaker_dirty_ids: set[int] = set()

    def deliver(
        self,
        message: EmailMessage,
        *,
        before_attempt: Callable[[], None] | None = None,
    ) -> ChainResult:
        self._breaker_dirty_ids.clear()
        attempts: list[ChainAttempt] = []
        last_code: str | None = None
        possible_duplicate = False
        entries = self._ordered_entries()

        for entry in entries:
            if not entry.active:
                continue
            if self._probe_claimer is not None and not self._probe_claimer(entry):
                last_code = MAIL_BREAKER_OPEN
                continue
            if not entry.breaker.allow_probe():
                last_code = MAIL_BREAKER_OPEN
                continue
            self._breaker_dirty_ids.add(entry.provider_id)
            if self._usage_reserver is not None and not self._usage_reserver(
                entry.provider_id, entry.adapter.capabilities
            ):
                entry.breaker.cancel_probe()
                last_code = MAIL_LOCAL_RATE_LIMIT
                continue
            if not self._usage.allows(entry.provider_id, entry.adapter):
                entry.breaker.cancel_probe()
                last_code = MAIL_LOCAL_RATE_LIMIT
                continue
            if not _message_fits(entry.adapter, message):
                entry.breaker.cancel_probe()
                last_code = EMAIL_SEND_FAILED
                continue

            provider_attempt = 0
            while provider_attempt < self.retry_policy.attempts:
                provider_attempt += 1
                # A local rate window is checked before every request, not just the first.  A
                # provider 429 still gets its bounded retry delay; a full local window simply
                # defers the whole chain to its durable outbox.
                if provider_attempt > 1:
                    if self._usage_reserver is not None and not self._usage_reserver(
                        entry.provider_id, entry.adapter.capabilities
                    ):
                        entry.breaker.cancel_probe()
                        last_code = MAIL_LOCAL_RATE_LIMIT
                        break
                    if not self._usage.allows(entry.provider_id, entry.adapter):
                        entry.breaker.cancel_probe()
                        last_code = MAIL_LOCAL_RATE_LIMIT
                        break
                if before_attempt is not None:
                    before_attempt()
                started = time.monotonic()
                try:
                    send_result = entry.adapter.send(message)
                    if not getattr(send_result, "ok", False):
                        raise MailError(
                            send_result.error_code or EMAIL_SEND_FAILED,
                            "provider returned an unsuccessful send result",
                        )
                except MailError as exc:
                    last_code = exc.error_code
                    possible_duplicate = possible_duplicate or exc.possible_duplicate
                    entry.breaker.record_failure(exc.error_code)
                    attempts.append(
                        ChainAttempt(
                            provider_id=entry.provider_id,
                            provider_name=entry.adapter.name,
                            attempt_number=provider_attempt,
                            status="failed",
                            error_code=exc.error_code,
                            duration_ms=_elapsed_ms(started),
                            possible_duplicate=exc.possible_duplicate,
                            permanent=exc.permanent or not exc.retryable,
                        )
                    )
                    if exc.retryable and provider_attempt < self.retry_policy.attempts:
                        delay = self.retry_policy.delay_for(
                            provider_attempt, exc.retry_after_seconds
                        )
                        if delay > 0:
                            self._sleeper(delay)
                        continue
                    break
                except SendError as exc:
                    # Compatibility with pre-Prompt-11 adapters.  A legacy error is permanent
                    # unless it explicitly carries the transient timeout/rate/5xx code.
                    transient = exc.error_code in {
                        "mail_timeout",
                        "mail_rate_limited",
                        "mail_http_5xx",
                    }
                    error = MailError(
                        exc.error_code,
                        exc.message,
                        retryable=transient,
                        permanent=not transient,
                        possible_duplicate=transient and exc.error_code == "mail_timeout",
                    )
                    last_code = error.error_code
                    entry.breaker.record_failure(error.error_code)
                    attempts.append(
                        ChainAttempt(
                            provider_id=entry.provider_id,
                            provider_name=entry.adapter.name,
                            attempt_number=provider_attempt,
                            status="failed",
                            error_code=error.error_code,
                            duration_ms=_elapsed_ms(started),
                            possible_duplicate=error.possible_duplicate,
                            permanent=error.permanent,
                        )
                    )
                    if error.retryable and provider_attempt < self.retry_policy.attempts:
                        delay = self.retry_policy.delay_for(provider_attempt)
                        if delay > 0:
                            self._sleeper(delay)
                        continue
                    break
                except Exception as exc:  # noqa: BLE001 - adapter boundary is failure-contained
                    last_code = EMAIL_SEND_FAILED
                    entry.breaker.record_failure(EMAIL_SEND_FAILED)
                    attempts.append(
                        ChainAttempt(
                            provider_id=entry.provider_id,
                            provider_name=entry.adapter.name,
                            attempt_number=provider_attempt,
                            status="failed",
                            error_code=EMAIL_SEND_FAILED,
                            duration_ms=_elapsed_ms(started),
                            permanent=True,
                        )
                    )
                    log.warning(
                        "provider %s raised an unexpected error",
                        entry.adapter.name,
                        extra={
                            "status": "error",
                            "stage": "email_send",
                            "error_code": EMAIL_SEND_FAILED,
                            "exception_type": type(exc).__name__,
                        },
                    )
                    break

                entry.breaker.record_success()
                attempts.append(
                    ChainAttempt(
                        provider_id=entry.provider_id,
                        provider_name=entry.adapter.name,
                        attempt_number=provider_attempt,
                        status="sent",
                        duration_ms=_elapsed_ms(started),
                        provider_message_id=send_result.provider_message_id,
                    )
                )
                return ChainResult(
                    delivered=True,
                    provider_used=entry.adapter.name,
                    provider_id=entry.provider_id,
                    provider_message_id=send_result.provider_message_id,
                    attempts=tuple(attempts),
                    possible_duplicate=possible_duplicate,
                    notification_status="sent",
                    failovers=_failovers(attempts, entries),
                )

        return ChainResult(
            delivered=False,
            attempts=tuple(attempts),
            error_code=last_code or MAIL_ALL_PROVIDERS_FAILED,
            possible_duplicate=possible_duplicate,
            notification_status="pending_retry",
            failovers=_failovers(attempts, entries),
        )

    @property
    def breaker_dirty_ids(self) -> frozenset[int]:
        """Provider IDs whose in-memory breaker may need persistence."""

        return frozenset(self._breaker_dirty_ids)

    def _ordered_entries(self) -> list[ProviderEntry]:
        if all(entry.priority is None for entry in self.providers):
            return list(self.providers)
        return sorted(
            self.providers,
            key=lambda entry: (
                entry.priority if entry.priority is not None else 2**31,
                entry.provider_id,
            ),
        )


def _failovers(
    attempts: list[ChainAttempt], entries: list[ProviderEntry]
) -> tuple[tuple[str, str, str], ...]:
    """Describe each failed provider once when another eligible provider follows it."""

    order = {entry.provider_id: index for index, entry in enumerate(entries)}
    seen: set[str] = set()
    events: list[tuple[str, str, str]] = []
    for attempt in attempts:
        if not attempt.permanent or attempt.provider_name in seen:
            continue
        seen.add(attempt.provider_name)
        index = order.get(attempt.provider_id or -1)
        if index is not None and any(entry.active for entry in entries[index + 1 :]):
            events.append((attempt.provider_name, attempt.error_code or EMAIL_SEND_FAILED, "next"))
    return tuple(events)


def _message_fits(provider: MailProvider, message: EmailMessage) -> bool:
    """Guard the final message against a provider's declared capability limits."""

    body_bytes = (
        max(
            len(message.text_body.encode("utf-8")),
            len((message.html_body or "").encode("utf-8")),
        )
        + 2048
    )
    plan = AttachmentPlanner(provider.capabilities).plan(
        list(message.attachments), message_overhead_bytes=body_bytes
    )
    return plan.fits_whole_set


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def any_breaker_open(entries: Sequence[ProviderEntry]) -> bool:
    """Return True when any provider's breaker is currently OPEN."""

    return any(entry.breaker.state == BreakerState.OPEN for entry in entries)

""":mod:`tender_intelligence.mail.breaker` — per-provider circuit breaker (docs/08 §8.3).

The breaker is a pure state machine with an injected clock and an optional listener so the
chain can persist ``breaker_state``/``breaker_until`` on the ``MailProvider`` row and the
specification's CLOSED → OPEN → HALF-OPEN → CLOSED life cycle is exercised without any real
timing dependency (prompt 11 §9).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

log = logging.getLogger("tender_intelligence.mail.breaker")


class BreakerState(StrEnum):
    """Provider availability states (docs/08 §8.3; DB stores the lowercase value)."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class BreakerEvent:
    """A change the outside world should observe (alert hook, log, persistence)."""

    provider_name: str
    state: BreakerState
    consecutive_failures: int
    message: str = ""


@runtime_checkable
class BreakerListener(Protocol):
    """Receives breaker transitions. Single method so transport stays out of the breaker."""

    def on_breaker_event(self, event: BreakerEvent) -> None:
        """Observe a circuit-breaker transition (e.g. tripped / recovered)."""


class NullBreakerListener:
    """Default listener: accept and remember events (assertable in tests, no side effects)."""

    def __init__(self) -> None:
        self.events: list[BreakerEvent] = []

    def on_breaker_event(self, event: BreakerEvent) -> None:
        self.events.append(event)


class CircuitBreaker:
    """Count consecutive failures and block a provider after the configurable threshold.

    The breaker holds no storage dependency; the chain maps state + cooldown onto the
    ``MailProvider.breaker_state`` / ``breaker_until`` columns. ``clock`` returns **wall-clock**
    Unix seconds so the cooldown persists across processes and restarts (``breaker_until`` is a
    real timestamp); tests inject a deterministic clock, so no real timing is ever waited on.
    """

    def __init__(
        self,
        *,
        provider_name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.time,
        listener: BreakerListener | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        self.provider_name = provider_name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = float(cooldown_seconds)
        self._clock = clock
        self._listener = listener
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_in_flight = False

    @classmethod
    def from_persisted(
        cls,
        *,
        provider_name: str,
        state: str,
        breaker_until_epoch: float | None,
        consecutive_failures: int = 0,
        failure_threshold: int,
        cooldown_seconds: float,
        clock: Callable[[], float] = time.time,
        listener: BreakerListener | None = None,
    ) -> CircuitBreaker:
        """Rebuild a breaker from the ``MailProvider`` row (restart-safe, prompt 11 §9).

        ``consecutive_failures`` is the persisted running count feeding the OPEN transition,
        so a provider that fails once per message can still trip the threshold across messages.
        """
        breaker = cls(
            provider_name=provider_name,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
            clock=clock,
            listener=listener,
        )
        try:
            breaker._state = BreakerState(state)
        except ValueError:
            # Unknown/legacy value in the DB: fail safe toward closed.
            breaker._state = BreakerState.CLOSED
        breaker._consecutive_failures = max(0, int(consecutive_failures))
        breaker._open_until = breaker_until_epoch
        breaker._probe_in_flight = False
        return breaker

    @property
    def state(self) -> BreakerState:
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def open_until(self) -> float | None:
        return self._open_until

    def allow_probe(self) -> bool:
        """Answer "may this provider be used now?"

        CLOSED: yes. OPEN with the cooldown passed: transition to HALF-OPEN and allow exactly
        one recovery probe. OPEN before the cooldown: no. HALF-OPEN: the single probe slot.
        """
        if self._state == BreakerState.CLOSED:
            return True
        now = self._clock()
        if self._state == BreakerState.OPEN and (
            self._open_until is None or now >= self._open_until
        ):
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = True
            # Reuse breaker_until as a durable probe lease while half-open.  A crashed
            # half-open worker therefore cannot let an unbounded number of probes through.
            self._open_until = now + self.cooldown_seconds
            self._emit("cooldown passed; recovery probe allowed")
            return True
        if self._state == BreakerState.HALF_OPEN and not self._probe_in_flight:
            if self._open_until is not None and now < self._open_until:
                return False
            self._probe_in_flight = True
            return True
        return False

    def record_success(self) -> None:
        """A delivery succeeded: close the breaker and reset the failure counter."""
        was_open = self._state != BreakerState.CLOSED
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._open_until = None
        self._probe_in_flight = False
        if was_open:
            self._emit("closed after successful delivery")

    def record_failure(self, error_code: str | None = None) -> None:
        """A delivery failed: count it, and trip open once the threshold is reached."""
        code = error_code or "email_send_failed"
        self._consecutive_failures += 1
        now = self._clock()
        if self._state == BreakerState.HALF_OPEN:
            # Recovery probe failed: back to OPEN with a fresh cooldown.
            self._state = BreakerState.OPEN
            self._open_until = now + self.cooldown_seconds
            self._probe_in_flight = False
            self._emit(f"recovery probe failed ({code})")
            return
        if (
            self._state == BreakerState.CLOSED
            and self._consecutive_failures >= self.failure_threshold
        ):
            self._state = BreakerState.OPEN
            self._open_until = now + self.cooldown_seconds
            self._probe_in_flight = False
            self._emit(f"opened after {self._consecutive_failures} consecutive failures ({code})")

    def cancel_probe(self) -> None:
        """Release a half-open probe slot when no request was actually attempted."""

        if self._state == BreakerState.HALF_OPEN:
            self._probe_in_flight = False

    def force_recover(self) -> None:
        """A successful probe / test-email closes the breaker regardless of state (docs/08)."""
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._open_until = None

    def _emit(self, message: str) -> None:
        if self._listener is None:
            return
        try:
            self._listener.on_breaker_event(
                BreakerEvent(
                    provider_name=self.provider_name,
                    state=self._state,
                    consecutive_failures=self._consecutive_failures,
                    message=message,
                )
            )
        except Exception:  # noqa: BLE001 - observability must not break failover
            log.warning(
                "circuit-breaker listener failed",
                extra={
                    "stage": "email",
                    "status": "warning",
                    "provider": self.provider_name,
                    "error_code": "mail_breaker_open",
                },
            )

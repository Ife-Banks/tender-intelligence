"""Unit tests for the mail circuit breaker (docs/08 §8.3, prompt 11 §9).

The breaker uses wall-clock seconds so the cooldown persists across restarts via
``breaker_until``; tests drive a deterministic injected clock, so no real timing is waited on.
"""

from __future__ import annotations

import pytest

from tender_intelligence.mail.breaker import (
    BreakerState,
    CircuitBreaker,
    NullBreakerListener,
)


class _Clock:
    """Deterministic wall-clock stand-in (epoch seconds)."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _breaker(*, clock: _Clock, threshold: int = 3, cooldown: float = 300.0) -> CircuitBreaker:
    return CircuitBreaker(
        provider_name="sendlib-one",
        failure_threshold=threshold,
        cooldown_seconds=cooldown,
        clock=clock,
    )


class TestClosedState:
    def test_closed_allows_probe(self) -> None:
        breaker = _breaker(clock=_Clock())
        assert breaker.state == BreakerState.CLOSED
        assert breaker.allow_probe()

    def test_consecutive_failures_count_without_tripping(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock, threshold=3)
        breaker.record_failure("mail_timeout")
        breaker.record_failure("mail_timeout")
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 2
        assert breaker.open_until is None


class TestOpenState:
    def test_opens_after_threshold_with_cooldown(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_http_5xx")
        assert breaker.state == BreakerState.OPEN
        assert breaker.open_until == pytest.approx(clock.now + 300.0)

    def test_open_blocks_probe_within_cooldown(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_http_5xx")
        clock.advance(10)  # still inside the 300 s window
        assert not breaker.allow_probe()

    def test_open_allows_single_probe_after_cooldown(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_http_5xx")
        clock.advance(301)
        assert breaker.allow_probe()
        assert breaker.state == BreakerState.HALF_OPEN


class TestHalfOpen:
    def test_half_open_failed_probe_reopens_with_fresh_cooldown(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_http_5xx")
        clock.advance(301)
        assert breaker.allow_probe()
        breaker.record_failure("mail_timeout")
        assert breaker.state == BreakerState.OPEN
        assert breaker.open_until == pytest.approx(clock.now + 300.0)

    def test_half_open_success_closes(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_http_5xx")
        clock.advance(301)
        assert breaker.allow_probe()
        breaker.record_success()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0
        assert breaker.open_until is None


class TestSuccessPath:
    def test_success_resets_counter_while_closed(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        breaker.record_failure("mail_timeout")
        breaker.record_failure("mail_timeout")
        breaker.record_success()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0

    def test_force_recover(self) -> None:
        clock = _Clock()
        breaker = _breaker(clock=clock)
        for _ in range(3):
            breaker.record_failure("mail_timeout")
        assert breaker.state == BreakerState.OPEN
        breaker.force_recover()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.consecutive_failures == 0


class TestPersistence:
    def test_from_persisted_restores_open_state_and_until(self) -> None:
        clock = _Clock(start=2_000_000.0)
        rebuilt = CircuitBreaker.from_persisted(
            provider_name="sendlib-one",
            state="open",
            breaker_until_epoch=2_000_000.0 + 300.0,
            failure_threshold=3,
            cooldown_seconds=300.0,
            clock=clock,
        )
        assert rebuilt.state == BreakerState.OPEN
        assert not rebuilt.allow_probe()

    def test_from_persisted_write_after_until_probes(self) -> None:
        clock = _Clock(start=2_000_000.0)
        rebuilt = CircuitBreaker.from_persisted(
            provider_name="sendlib-one",
            state="open",
            breaker_until_epoch=1_900_000.0,  # cooldown already passed
            failure_threshold=3,
            cooldown_seconds=300.0,
            clock=clock,
        )
        assert rebuilt.allow_probe()

    def test_from_persisted_unknown_state_falls_back_closed(self) -> None:
        rebuilt = CircuitBreaker.from_persisted(
            provider_name="sendlib-one",
            state="legacy_state",
            breaker_until_epoch=None,
            failure_threshold=3,
            cooldown_seconds=300.0,
            clock=_Clock(),
        )
        assert rebuilt.state == BreakerState.CLOSED


class TestGuardRails:
    def test_threshold_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            _breaker(clock=_Clock(), threshold=0)

    def test_listener_receives_trip_and_recovery(self) -> None:
        clock = _Clock()
        listener = NullBreakerListener()
        breaker = CircuitBreaker(
            provider_name="sendlib-one",
            failure_threshold=2,
            cooldown_seconds=300.0,
            clock=clock,
            listener=listener,
        )
        breaker.record_failure("mail_timeout")
        breaker.record_failure("mail_timeout")
        assert [e.state for e in listener.events] == [BreakerState.OPEN]
        assert listener.events[0].provider_name == "sendlib-one"

        clock.advance(301)
        assert breaker.allow_probe()
        breaker.record_success()
        assert [e.state for e in listener.events] == [
            BreakerState.OPEN,
            BreakerState.HALF_OPEN,
            BreakerState.CLOSED,
        ]

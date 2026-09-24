"""Unit tests for the provider chain (docs/08 §8.3, prompt 11 §6–§9).

Retry/failover, breaker skipping, and the possible-duplicate flag are tested with scripted
in-memory adapters and an injected sleeper so no real time is ever waited on.
"""

from __future__ import annotations

from typing import Any

from tender_intelligence.core import errors as codes
from tender_intelligence.mail.breaker import BreakerState, CircuitBreaker
from tender_intelligence.mail.chain import (
    MailRetryPolicy,
    ProviderChain,
    ProviderEntry,
    any_breaker_open,
)
from tender_intelligence.mail.errors import permanent, retryable, timeout_error
from tender_intelligence.mail.message import EmailMessage
from tender_intelligence.mail.provider import Capabilities, MailProvider, SendResult


class _Clock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedProvider(MailProvider):
    """Pop outcomes off a script; raises are delivered as-is."""

    def __init__(
        self,
        outcomes: list[Any],
        *,
        name: str = "scripted",
        capabilities: Capabilities | None = None,
    ) -> None:
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0
        self._capabilities = capabilities or Capabilities()

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    def send(self, message: EmailMessage) -> SendResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


def _entry(provider: ScriptedProvider, *, provider_id: int = 1) -> ProviderEntry:
    return ProviderEntry(
        provider_id=provider_id,
        adapter=provider,
        breaker=CircuitBreaker(provider_name=provider.name, clock=_Clock()),
    )


def _msg() -> EmailMessage:
    return EmailMessage(to=("dev@opex.example",), subject="x", text_body="y")


class TestDeliverSuccess:
    def test_first_provider_delivers(self) -> None:
        p1 = ScriptedProvider(
            [SendResult(provider_name="s1", ok=True, provider_message_id="m1")],
            name="s1",
        )
        p2 = ScriptedProvider([SendResult(provider_name="s2", ok=True)], name="s2")
        result = ProviderChain([_entry(p1), _entry(p2)]).deliver(_msg())
        assert result.delivered
        assert result.provider_used == "s1"
        assert result.provider_id == 1
        assert result.provider_message_id == "m1"
        assert p1.calls == 1 and p2.calls == 0
        assert result.attempts[0].status == "sent"
        assert result.attempts[0].error_code is None
        assert result.notification_status == "sent"


class TestRetryAndFailover:
    def test_transient_retry_then_success(self) -> None:
        delays: list[float] = []
        p1 = ScriptedProvider(
            [retryable(codes.MAIL_TIMEOUT, "t"), SendResult(provider_name="s1", ok=True)],
            name="s1",
        )
        chain = ProviderChain(
            [_entry(p1)],
            retry_policy=MailRetryPolicy(attempts=3, backoff_base_seconds=1.0),
            sleeper=delays.append,
        )
        result = chain.deliver(_msg())
        assert result.delivered
        # The retry policy slept 1 s after the first failed attempt.
        assert delays == [1.0]
        assert [a.status for a in result.attempts] == ["failed", "sent"]

    def test_transient_exhausted_fails_over(self) -> None:
        delays: list[float] = []
        p1 = ScriptedProvider(
            [
                retryable(codes.MAIL_TIMEOUT, "t"),
                retryable(codes.MAIL_TIMEOUT, "t"),
                retryable(codes.MAIL_TIMEOUT, "t"),
            ],
            name="s1",
        )
        p2 = ScriptedProvider(
            [SendResult(provider_name="s2", ok=True, provider_message_id="done")],
            name="s2",
        )
        chain = ProviderChain(
            [_entry(p1), _entry(p2)],
            retry_policy=MailRetryPolicy(attempts=2, backoff_base_seconds=1.0),
            sleeper=delays.append,
        )
        result = chain.deliver(_msg())
        assert result.delivered and result.provider_used == "s2"
        # Attempts: fail(1), sleep 1, fail(2), failover, sent. The sleep only runs
        # between attempts, so exactly one backoff is observed.
        assert delays == [1.0]
        statuses = [a.status for a in result.attempts]
        assert statuses.count("failed") == 2
        assert statuses[-1] == "sent"

    def test_permanent_error_skips_retry(self) -> None:
        delays: list[float] = []
        p1 = ScriptedProvider(
            [permanent(codes.MAIL_INVALID_CREDENTIALS, "bad key")],
            name="s1",
        )
        p2 = ScriptedProvider([SendResult(provider_name="s2", ok=True)], name="s2")
        chain = ProviderChain(
            [_entry(p1), _entry(p2)],
            retry_policy=MailRetryPolicy(attempts=3),
            sleeper=delays.append,
        )
        result = chain.deliver(_msg())
        assert result.delivered and result.provider_used == "s2"
        assert delays == []  # permanent failures never sleep
        assert result.attempts[0].status == "failed"
        assert result.attempts[0].error_code == codes.MAIL_INVALID_CREDENTIALS

    def test_all_fail_pending_retry_with_code(self) -> None:
        p1 = ScriptedProvider(
            [permanent(codes.MAIL_QUOTA_EXHAUSTED, "quota")],
            name="s1",
        )
        p2 = ScriptedProvider([retryable(codes.MAIL_TIMEOUT, "t")], name="s2")
        result = ProviderChain(
            [_entry(p1, provider_id=1), _entry(p2, provider_id=2)],
            retry_policy=MailRetryPolicy(attempts=1),
        ).deliver(_msg())
        assert not result.delivered
        assert result.error_code == codes.MAIL_TIMEOUT
        assert result.notification_status == "pending_retry"
        assert result.attempts_count == 2


class TestBreakerIntegration:
    def test_open_breaker_skipped_reports_breaker_code(self) -> None:
        clock = _Clock()
        p1 = ScriptedProvider([SendResult(provider_name="s1", ok=True)])
        breaker = CircuitBreaker(
            provider_name="s1", failure_threshold=3, cooldown_seconds=300, clock=clock
        )
        for _ in range(3):
            breaker.record_failure(codes.MAIL_TIMEOUT)
        assert breaker.state == BreakerState.OPEN
        entry = ProviderEntry(provider_id=1, adapter=p1, breaker=breaker)
        result = ProviderChain([entry]).deliver(_msg())
        assert not result.delivered
        assert p1.calls == 0
        assert result.error_code == codes.MAIL_BREAKER_OPEN
        assert result.attempts_count == 0

    def test_half_open_probe_success_closes(self) -> None:
        clock = _Clock()
        p1 = ScriptedProvider([SendResult(provider_name="s1", ok=True)])
        breaker = CircuitBreaker(
            provider_name="s1", failure_threshold=3, cooldown_seconds=300, clock=clock
        )
        for _ in range(3):
            breaker.record_failure(codes.MAIL_TIMEOUT)
        clock.advance(301)
        result = ProviderChain([ProviderEntry(provider_id=1, adapter=p1, breaker=breaker)]).deliver(
            _msg()
        )
        assert result.delivered
        assert breaker.state == BreakerState.CLOSED

    def test_inactive_provider_skipped(self) -> None:
        p1 = ScriptedProvider([SendResult(provider_name="s1", ok=True)])
        p2 = ScriptedProvider([SendResult(provider_name="s2", ok=True)])
        result = ProviderChain(
            [
                ProviderEntry(
                    provider_id=1,
                    adapter=p1,
                    breaker=CircuitBreaker(provider_name="s1", clock=_Clock()),
                    active=False,
                ),
                ProviderEntry(
                    provider_id=2,
                    adapter=p2,
                    breaker=CircuitBreaker(provider_name="s2", clock=_Clock()),
                ),
            ]
        ).deliver(_msg())
        assert p1.calls == 0 and p2.calls == 1
        assert result.provider_id == 2


class TestPossibleDuplicate:
    def test_possible_duplicate_propagates_on_failover(self) -> None:
        p1 = ScriptedProvider([timeout_error("t")])
        p2 = ScriptedProvider([SendResult(provider_name="s2", ok=True)])
        result = ProviderChain(
            [_entry(p1, provider_id=1), _entry(p2, provider_id=2)],
            retry_policy=MailRetryPolicy(attempts=1),
        ).deliver(_msg())
        assert result.delivered
        assert result.possible_duplicate
        assert result.attempts[0].possible_duplicate

    def test_clean_success_has_no_duplicate_flag(self) -> None:
        p1 = ScriptedProvider([SendResult(provider_name="s1", ok=True)])
        result = ProviderChain([_entry(p1)]).deliver(_msg())
        assert result.delivered and not result.possible_duplicate


class TestUnexpectedAdapterError:
    def test_adapter_bug_counts_as_failed_send(self) -> None:
        p1 = ScriptedProvider([RuntimeError("spurious")])
        result = ProviderChain([_entry(p1)]).deliver(_msg())
        assert not result.delivered
        assert [a.error_code for a in result.attempts] == [codes.EMAIL_SEND_FAILED]


class TestRetryPolicy:
    def test_default_policy_has_two_retries(self) -> None:
        policy = MailRetryPolicy()
        assert policy.retries == 2
        assert policy.max_attempts == 3

    def test_delay_for_bounds(self) -> None:
        policy = MailRetryPolicy(attempts=5, backoff_base_seconds=2.0, backoff_max_seconds=5.0)
        assert policy.delay_for(1) == 2.0
        assert policy.delay_for(2) == 4.0
        assert policy.delay_for(3) == 5.0  # capped
        assert policy.delay_for(99) == 5.0

    def test_delay_for_never_negative(self) -> None:
        policy = MailRetryPolicy(attempts=1)
        assert policy.delay_for(0) == policy.delay_for(1) == 1.0


    def test_local_rate_limit_defers_second_message(self) -> None:
        clock = _Clock()
        provider = ScriptedProvider(
            [
                SendResult(provider_name="limited", ok=True),
                SendResult(provider_name="limited", ok=True),
            ],
            name="limited",
            capabilities=Capabilities(rate_limit_per_min=1),
        )
        chain = ProviderChain(
            [_entry(provider)],
            clock=clock,
            retry_policy=MailRetryPolicy(attempts=1),
        )
        assert chain.deliver(_msg()).delivered
        second = chain.deliver(_msg())
        assert not second.delivered
        assert second.error_code == "mail_local_rate_limit"
        assert provider.calls == 1


class TestHelpers:
    def test_any_breaker_open(self) -> None:
        clock = _Clock()
        closed = CircuitBreaker(provider_name="s1", clock=clock)
        open_b = CircuitBreaker(
            provider_name="s2", failure_threshold=2, cooldown_seconds=300, clock=clock
        )
        open_b.record_failure(codes.MAIL_TIMEOUT)
        open_b.record_failure(codes.MAIL_TIMEOUT)
        entries = [
            ProviderEntry(provider_id=1, adapter=ScriptedProvider([]), breaker=closed),
            ProviderEntry(provider_id=2, adapter=ScriptedProvider([]), breaker=open_b),
        ]
        assert any_breaker_open(entries)
        assert not any_breaker_open(entries[:1])

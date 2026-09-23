"""Bounded retry with backoff for orchestrator-owned operations (prompt 10 §11).

The retry matrix
================

Prompt 10 §11 warns against multiplying retry loops that an earlier stage already owns, and
names the 3x3=9 trap explicitly. The stages below already retry **internally**, at the HTTP
layer, using ``CrawlPolicy.max_retries`` (default 3) with exponential backoff:

* stage 04/07 — :class:`~tender_intelligence.sources.polite.PoliteHttpClient`, via ``get()``
* stage 08     — :class:`~tender_intelligence.acquisition.fetcher.DocumentFetcher`, via ``fetch()``

The orchestrator therefore must **not** re-drive a transport failure. What it does own is
the whole pipeline step whose *local* effects failed — in practice only acquisition, whose
service performs no retry of its own and instead reports a per-failure ``retryable`` flag
and a ``category``. So the matrix is:

===========================  ==================  ==========================  ===============
Stage                        Retried by          Retried by orchestrator     Worst case
===========================  ==================  ==========================  ===============
04 discovery                 http client (3x)    no — code propagates        3 requests
05 dedup                     nobody              no — transactional          1 transaction
06 persistence               nobody              no — inside 05's txn        0 (no call)
07 detail/document discovery  http client (3x)   no — per-tender isolation   3 requests
08 acquisition               nobody              **yes** — policy below      2 x 1 fetch
09 processing                nobody              no — per-tender isolation   1 pass
===========================  ==================  ==========================  ===============

2 x 1, not 2 x 3: the orchestrator retries acquisition **only** when every failure the
service reported is ``retryable`` *and* its category is stage-local — ``storage_failed`` or
``persistence_failed`` — which are conditions the fetcher never touched. ``transport``,
``http_error`` and ``response_too_large`` have already been retried to exhaustion inside the
fetcher; re-driving them here is the multiplication §11 forbids. A failure the fetcher
already gave up on is final.

The category values are imported from the acquisition service's own taxonomy rather than
retyped here, so a rename in prompt 08 cannot silently desynchronise this policy.

Termination is deterministic: the attempt count is bounded by :attr:`RetryPolicy.attempts`,
the delay is a bounded exponential, and ``should_retry`` is evaluated per failure rather
than in a loop condition. The ambient correlation ID is a context variable and is never
cleared between attempts, so every attempt logs under the same correlation as the run that
owns it (prompt 10 §4).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from tender_intelligence.acquisition.errors import PERSISTENCE_FAILED, STORAGE_FAILED

#: Failure categories the orchestrator may re-drive. Everything else is final: either the
#: fetcher already retried it (transport / http_error / response_too_large) or re-running
#: cannot help (invalid_response, unsupported_scheme, unsafe_archive_member).
STAGE_LOCAL_RETRYABLE_CATEGORIES: frozenset[str] = frozenset(
    {STORAGE_FAILED, PERSISTENCE_FAILED}
)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential backoff (prompt 10 §11).

    ``attempts`` is the **total** number of tries, not the number of extra ones, so
    ``attempts=2`` means "run it, and if it fails, run it once more". ``attempts=1``
    disables retry entirely.
    """

    attempts: int = 2
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0

    def delay_for(self, attempt: int) -> float:
        """Seconds to wait after *attempt* (1-based) before the next try."""
        if attempt < 1:
            attempt = 1
        raw = self.backoff_base_seconds * float(2 ** (attempt - 1))
        return min(raw, self.backoff_max_seconds)


@dataclass(frozen=True)
class RetryOutcome[T]:
    """What happened across all attempts (prompt 10 §11: attempts are recorded)."""

    value: T | None
    attempts: int
    delays: tuple[float, ...]
    error: BaseException | None
    exhausted: bool

    @property
    def succeeded(self) -> bool:
        return self.error is None


def run_with_retry[T](
    operation: Callable[[], T],
    *,
    policy: RetryPolicy,
    should_retry: Callable[[BaseException], bool],
    sleep: Callable[[float], None] = time.sleep,
) -> RetryOutcome[T]:
    """Run *operation*, retrying per *policy* while *should_retry* agrees.

    *sleep* is injectable so tests assert the exact backoff without waiting for it.
    """
    delays: list[float] = []
    attempts = 0
    last: BaseException | None = None

    for attempt in range(1, max(1, policy.attempts) + 1):
        attempts = attempt
        try:
            return RetryOutcome(
                value=operation(),
                attempts=attempts,
                delays=tuple(delays),
                error=None,
                exhausted=False,
            )
        except Exception as exc:  # noqa: BLE001 - should_retry is the policy boundary
            last = exc
            if attempt >= policy.attempts or not should_retry(exc):
                break
            delay = policy.delay_for(attempt)
            delays.append(delay)
            sleep(delay)

    return RetryOutcome(
        value=None,
        attempts=attempts,
        delays=tuple(delays),
        error=last,
        exhausted=True,
    )

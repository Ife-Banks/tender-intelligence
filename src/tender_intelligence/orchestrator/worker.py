"""The worker-level loop: one pass over every due source (prompt 10 §2, §15).

Prompt 10 §2 asks for a loop that determines the enabled/due sources, runs each one under
its own isolated run, and carries on regardless of what happened to the previous one. That
is exactly — and only — what :meth:`Worker.run_once` does.

There is no scheduler here of its own: due-ness is :class:`SourceScheduler`'s decision, and
each run's lifecycle is :class:`RunCoordinator`'s. This class owns the *iteration and the
isolation between iterations*, nothing else (prompt 10 §21: do not create a second scheduler;
do not create a second persistence layer).

Isolation is enforced at the loop, not trusted to the coordinator: ``run_source`` is wrapped
so that even a failure the coordinator did not contain — an unknown source id, an unreadable
configuration, a bug — is recorded against that source and the loop moves on. A source A
failure can never prevent source B from running (prompt 10 §15).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from tender_intelligence.core.correlation import correlation_context, new_correlation_id
from tender_intelligence.core.errors import SOURCE_NOT_RUNNABLE, STAGE_FAILED
from tender_intelligence.orchestrator.coordinator import RunCoordinator
from tender_intelligence.orchestrator.scheduler import SourceScheduler, SourceSpec
from tender_intelligence.orchestrator.status import RunReport, RunStatus

log = logging.getLogger("tender_intelligence.orchestrator.worker")


@dataclass(frozen=True)
class SourceFailure:
    """A source whose run could not even be started (prompt 10 §15).

    Distinct from a :class:`RunReport` with a FAILED status: that source *ran* and failed,
    which is a result. This is a source that produced no run at all — and it must still be
    visible rather than swallowed by the loop.
    """

    source_id: int
    source_name: str
    error_code: str
    message: str


@dataclass(frozen=True)
class WorkerReport:
    """The outcome of one pass over the due sources (prompt 10 §18, §22).

    Carries no secrets and no document contents: it holds only run reports and identifiers.
    """

    correlation_id: str
    started_at: datetime
    ended_at: datetime
    runs: tuple[RunReport, ...] = field(default=())
    failures: tuple[SourceFailure, ...] = field(default=())
    skipped: tuple[SourceSpec, ...] = field(default=())
    dry_run: bool = False

    @property
    def ran(self) -> int:
        return len(self.runs)

    @property
    def failed(self) -> int:
        return sum(1 for run in self.runs if run.status is RunStatus.FAILED) + len(self.failures)

    @property
    def succeeded(self) -> bool:
        """True when nothing failed at all — the worker's exit-code input."""
        return self.failed == 0

    def run_for(self, source_id: int) -> RunReport | None:
        for run in self.runs:
            if run.source_id == source_id:
                return run
        return None


class Worker:
    """Runs one pass over the due sources (prompt 10 §2)."""

    def __init__(
        self,
        *,
        coordinator: RunCoordinator,
        scheduler: SourceScheduler,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._scheduler = scheduler
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(
        self,
        *,
        dry_run: bool = False,
        source_ids: Sequence[int] | None = None,
        force: bool = False,
    ) -> WorkerReport:
        """Run every due source once (prompt 10 §2).

        *source_ids* narrows the pass to explicitly-named sources — an operator asking for
        specific sources is not a scheduler tick, so those are run whether or not they are
        due (and an inactive one only with *force*).

        *dry_run* is a genuinely read-only pass (prompt 10 §14): it still enumerates and
        inspects, and writes nothing at all.
        """
        started_at = self._clock()
        pass_cid = new_correlation_id()
        with correlation_context(pass_cid):
            selected, skipped, refusals = self._select(source_ids)
            log.info(
                "worker pass starting: %d source(s) selected, %d skipped",
                len(selected),
                len(skipped),
                extra={
                    "stage": "worker",
                    "status": "start",
                    "correlation_id": pass_cid,
                    "dry_run": dry_run,
                },
            )
            runs: list[RunReport] = []
            failures: list[SourceFailure] = list(refusals)
            for spec in selected:
                try:
                    runs.append(self._coordinator.run_source(spec.id, dry_run=dry_run, force=force))
                except Exception as exc:  # noqa: BLE001 - the loop's isolation boundary
                    # A source that could not be started is a per-source failure, never a
                    # worker failure: the next source must still run (prompt 10 §15).
                    failures.append(self._failure(spec, exc))

        ended_at = self._clock()
        report = WorkerReport(
            correlation_id=pass_cid,
            started_at=started_at,
            ended_at=ended_at,
            runs=tuple(runs),
            failures=tuple(failures),
            skipped=tuple(skipped),
            dry_run=dry_run,
        )
        log.info(
            "worker pass finished: %d run(s), %d failure(s)",
            report.ran,
            report.failed,
            extra={
                "stage": "worker",
                "status": "done" if report.succeeded else "error",
                "correlation_id": pass_cid,
                "runs": report.ran,
                "failures": report.failed,
            },
        )
        return report

    # -- selection ----------------------------------------------------------

    def _select(
        self, source_ids: Sequence[int] | None
    ) -> tuple[list[SourceSpec], list[SourceSpec], list[SourceFailure]]:
        """The sources to run, those deliberately left alone, and any that cannot be resolved.

        With no explicit ids this is a scheduler tick: only due sources run. With explicit
        ids, an operator is asking for those sources specifically, so due-ness does not
        decide — but an id that names nothing exists is refused here, with the same
        ``source_not_runnable`` code the coordinator would use, because there is no spec to
        hand the coordinator and a silently-dropped id would be a lie in the report.
        """
        if source_ids is None:
            decisions = self._scheduler.decisions()
            return (
                [d.spec for d in decisions if d.due],
                [d.spec for d in decisions if not d.due],
                [],
            )

        selected: list[SourceSpec] = []
        skipped: list[SourceSpec] = []
        refusals: list[SourceFailure] = []
        for source_id in source_ids:
            decision = self._scheduler.get(source_id)
            if decision is None:
                refusals.append(
                    SourceFailure(
                        source_id=int(source_id),
                        source_name=f"source-{source_id}",
                        error_code=SOURCE_NOT_RUNNABLE,
                        message=f"unknown source_id {source_id}",
                    )
                )
                continue
            # Inactive sources are still selected: the coordinator owns that refusal, so the
            # reason and its error code reach the report through one path, not two.
            selected.append(decision.spec)
            if not decision.due:
                skipped.append(decision.spec)
        return selected, skipped, refusals

    def _failure(self, spec: SourceSpec, exc: BaseException) -> SourceFailure:
        """Record a source that could not be started, without leaking a secret (prompt 10 §10)."""
        code = getattr(exc, "error_code", None) or STAGE_FAILED
        message = getattr(exc, "message", None) or type(exc).__name__
        log.error(
            "source %s could not be run: %s",
            spec.name,
            code,
            extra={
                "stage": "worker",
                "status": "error",
                "source_id": spec.id,
                "error_code": code,
            },
        )
        return SourceFailure(
            source_id=spec.id,
            source_name=spec.name,
            error_code=str(code),
            message=str(message),
        )

""":mod:`tender_intelligence.db.repositories.runs` — RunHistory persistence (prompt 06).

The run lifecycle is derived, not stored (docs/04 §4.8): ``ended_at IS NULL`` ⇒ RUNNING,
``error_count > 0`` ⇒ ERRORED, otherwise COMPLETED. The repository persists the RUNNING
row, then completes or marks errored. Both completion and error-marking are idempotent: a
second call on an already-ended run is a no-op so a crash/retry never double-stamps or
overwrites counts.

Who creates the row depends on the caller. Run standalone (prompt 05's entry point),
``DedupService`` creates and completes it. Run under the orchestrator (prompt 10 §5), the
row spans the whole run — stage 04 through stage 09 — so the coordinator creates it before
discovery and finalises it afterwards, and dedup is handed ``run_history_id`` instead.
``record_stages`` writes the per-stage map last, after the run has ended either way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.repositories.base import Repository


@dataclass(frozen=True)
class RunCounts:
    """The listing tallies a run recorded, whether it finished or failed (prompt 10 §5)."""

    listings_found: int = 0
    new_count: int = 0
    update_count: int = 0
    unchanged_count: int = 0


class RunHistoryRepository(Repository):
    """Row-level reads/writes for :class:`RunHistory` (prompt 06 §4, docs/03 §3.2)."""

    def create(
        self,
        source_id: int,
        correlation_id: str,
        *,
        started_at: datetime | None = None,
    ) -> RunHistory:
        run = RunHistory(
            source_id=source_id,
            started_at=started_at or datetime.now(UTC),
            listings_found=0,
            new_count=0,
            update_count=0,
            unchanged_count=0,
            error_count=0,
            failed_correlation_ids=None,
            correlation_id=correlation_id,
        )
        self.session.add(run)
        return run

    def complete(
        self,
        run: RunHistory,
        *,
        listings_found: int,
        new_count: int,
        update_count: int,
        unchanged_count: int,
        ended_at: datetime | None = None,
    ) -> None:
        if run.ended_at is not None:
            return
        run.listings_found = listings_found
        run.new_count = new_count
        run.update_count = update_count
        run.unchanged_count = unchanged_count
        run.ended_at = ended_at or datetime.now(UTC)

    def mark_errored(
        self,
        run: RunHistory,
        correlation_id: str,
        *,
        counts: RunCounts | None = None,
    ) -> None:
        """End a failed run (docs/04 §4.8).

        *counts* lets an orchestrated run keep the listing tallies it had already
        established before the failure — a run that discovered 12 tenders and then died in
        acquisition should still say so (prompt 10 §5). Omitting it leaves the existing
        columns untouched, which is prompt 05's behaviour.
        """
        if run.ended_at is not None:
            return
        run.ended_at = datetime.now(UTC)
        run.error_count = (run.error_count or 0) + 1
        failed = list(run.failed_correlation_ids or [])
        if correlation_id not in failed:
            failed.append(correlation_id)
        run.failed_correlation_ids = failed
        if counts is not None:
            run.listings_found = counts.listings_found
            run.new_count = counts.new_count
            run.update_count = counts.update_count
            run.unchanged_count = counts.unchanged_count

    def record_stages(
        self,
        run: RunHistory,
        *,
        stages: dict[str, str],
        config_version: int | None = None,
        failed_stage: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Record the per-stage outcome of an orchestrated run (prompt 10 §5, §8).

        Deliberately **not** guarded by ``ended_at``, unlike :meth:`complete` and
        :meth:`mark_errored`: the stage map is written last, *after* the run has already
        been completed or errored, so that a run remains reconstructable either way.
        """
        run.stages = dict(stages)
        run.config_version = config_version
        run.failed_stage = failed_stage
        run.error_code = error_code

    def get(self, run_id: int) -> RunHistory | None:
        return self.session.get(RunHistory, run_id)

    def get_by_correlation(self, correlation_id: str) -> RunHistory | None:
        return self.session.scalar(
            select(RunHistory).where(RunHistory.correlation_id == correlation_id)
        )

    def list_by_source(self, source_id: int) -> list[RunHistory]:
        return list(
            self.session.scalars(
                select(RunHistory)
                .where(RunHistory.source_id == source_id)
                .order_by(RunHistory.started_at)
            ).all()
        )

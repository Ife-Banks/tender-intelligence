""":mod:`tender_intelligence.db.repositories.runs` — RunHistory persistence (prompt 06).

The run lifecycle is derived, not stored (docs/04 §4.8): ``ended_at IS NULL`` ⇒ RUNNING,
``error_count > 0`` ⇒ ERRORED, otherwise COMPLETED. The repository persists the RUNNING
row (prompt 05's ``_open_run`` commits it), then completes or marks errored. Both
completion and error-marking are idempotent: a second call on an already-ended run is a
no-op so a crash/retry never double-stamps or overwrites counts.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.repositories.base import Repository


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

    def mark_errored(self, run: RunHistory, correlation_id: str) -> None:
        if run.ended_at is not None:
            return
        run.ended_at = datetime.now(UTC)
        run.error_count = (run.error_count or 0) + 1
        failed = list(run.failed_correlation_ids or [])
        if correlation_id not in failed:
            failed.append(correlation_id)
        run.failed_correlation_ids = failed

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

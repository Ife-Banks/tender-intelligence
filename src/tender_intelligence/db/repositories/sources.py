""":mod:`tender_intelligence.db.repositories.sources` — Source reads (prompt 06)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories.base import Repository


class SourceRepository(Repository):
    """Read access to configured discovery sources (docs/03 §3.1)."""

    def get(self, source_id: int) -> Source | None:
        return self.session.get(Source, source_id)

    def exists(self, source_id: int) -> bool:
        return self.session.scalar(select(Source.id).where(Source.id == source_id)) is not None

    def list_all(self) -> list[Source]:
        return list(self.session.scalars(select(Source).order_by(Source.id)).all())

    def list_runnable(self) -> list[Source]:
        """Active sources, ordered deterministically (prompt 10 §2).

        Due-ness is applied by the scheduler, which owns the crawl-frequency policy; this
        only excludes disabled sources (``active = False``), which must never be crawled.
        """
        return list(
            self.session.scalars(
                select(Source).where(Source.active.is_(True)).order_by(Source.id)
            ).all()
        )

    def mark_run(
        self,
        source_id: int,
        *,
        at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        """Stamp a source's crawl outcome (prompt 10 §2, §9).

        ``last_run_at`` is the authoritative scheduler input. ``error`` records the run's
        failure on the source itself so an operator sees it without reading RunHistory;
        pass ``None`` on success to clear a previous failure. The caller owns the commit.
        """
        source = self.session.get(Source, source_id)
        if source is None:
            return
        source.last_run_at = at or datetime.now(UTC)
        source.last_error = error


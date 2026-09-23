"""Which sources are due for a run (prompt 10 §2).

``Source.last_run_at`` is the authoritative scheduler input; the orchestrator stamps it via
:meth:`SourceRepository.mark_run`. Due-ness is deliberately simple and inspectable:

* an **inactive** source is never due, whatever its timestamps say;
* a source that has **never run** is due;
* a source with **no crawl frequency configured** is due on every pass — the operator has not
  asked for throttling, and inventing a default here would silently change crawl cadence;
* otherwise the source is due once ``crawl_frequency_minutes`` have elapsed since
  ``last_run_at``.

Sources are read as immutable :class:`SourceSpec` snapshots, not ORM instances, so nothing
downstream can lazily load a detached object or accidentally mutate a row it does not own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories import SourceRepository


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a stored timestamp to aware UTC.

    SQLite drops tzinfo on ``DateTime(timezone=True)``, so a round-tripped ``last_run_at``
    can come back naive. Treating a naive value as UTC is correct here: everything this
    pipeline writes is UTC (``datetime.now(UTC)``).
    """
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class SourceSpec:
    """Immutable snapshot of one configured source (prompt 10 §2)."""

    id: int
    name: str
    source_type: str
    base_url: str
    listing_url: str | None
    active: bool
    crawl_frequency_minutes: int | None
    last_run_at: datetime | None
    parser_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Source) -> SourceSpec:
        return cls(
            id=int(row.id),
            name=str(row.name),
            source_type=str(row.source_type or ""),
            base_url=str(row.base_url or ""),
            listing_url=str(row.listing_url) if row.listing_url else None,
            active=bool(row.active),
            crawl_frequency_minutes=row.crawl_frequency_minutes,
            last_run_at=_as_utc(row.last_run_at),
            parser_config=dict(row.parser_config or {}),
        )


@dataclass(frozen=True)
class ScheduleDecision:
    """Why a source is (or is not) being run, for the log and the run report."""

    spec: SourceSpec
    due: bool
    reason: str
    next_due_at: datetime | None = None


class SourceScheduler:
    """Decides which configured sources to crawl on this pass (prompt 10 §2)."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._maker = session_factory
        self._clock = clock

    def decisions(self, *, now: datetime | None = None) -> list[ScheduleDecision]:
        """Every active source with its due-ness and the reason for it."""
        moment = _as_utc(now) or self._clock()
        with self._maker() as session:
            rows = SourceRepository(session).list_runnable()
            return [self._decide(SourceSpec.from_row(row), moment) for row in rows]

    def due(self, *, now: datetime | None = None) -> list[SourceSpec]:
        """The sources to run on this pass, in stable id order."""
        return [d.spec for d in self.decisions(now=now) if d.due]

    def get(self, source_id: int, *, now: datetime | None = None) -> ScheduleDecision | None:
        """One source's decision, or ``None`` when it does not exist.

        Used by the single-source entry point (prompt 10 §2), which runs a named source
        whether or not it is due — an operator asking for a source explicitly is not a
        scheduler tick.
        """
        moment = _as_utc(now) or self._clock()
        with self._maker() as session:
            row = SourceRepository(session).get(source_id)
            if row is None:
                return None
            return self._decide(SourceSpec.from_row(row), moment)

    def _decide(self, spec: SourceSpec, now: datetime) -> ScheduleDecision:
        if not spec.active:
            return ScheduleDecision(spec=spec, due=False, reason="source is inactive")
        frequency = spec.crawl_frequency_minutes
        if spec.last_run_at is None:
            return ScheduleDecision(spec=spec, due=True, reason="source has never run")
        if not frequency or frequency <= 0:
            return ScheduleDecision(
                spec=spec,
                due=True,
                reason="no crawl frequency configured; always due",
                next_due_at=now,
            )
        next_due_at = spec.last_run_at + timedelta(minutes=int(frequency))
        if now >= next_due_at:
            return ScheduleDecision(
                spec=spec, due=True, reason="crawl interval elapsed", next_due_at=next_due_at
            )
        return ScheduleDecision(
            spec=spec,
            due=False,
            reason="crawled within its configured interval",
            next_due_at=next_due_at,
        )

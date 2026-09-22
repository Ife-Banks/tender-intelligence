""":mod:`tender_intelligence.db.models.runs` — RunHistory entity (docs/03 §3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin


class RunHistory(Base, TimestampMixin):
    """Per-source crawl run summary, independent of tender records (v1.1 §6.1).

    ``ended_at is NULL`` means the run is still in progress (or crashed mid-run); the
    lifecycle is derived: a completed run has ``ended_at`` set, an errored run also
    bumps ``error_count`` and records the run correlation ID in ``failed_correlation_ids``.
    ``update_count`` / ``unchanged_count`` were added in migration 0002 (prompt 05 §7;
    docs/04 §4.8).
    """

    __tablename__ = "run_history"
    __table_args__ = (Index("ix_run_history_started_at", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listings_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_correlation_ids: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

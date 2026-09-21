""":mod:`tender_intelligence.db.models.alerts` — AlertEvent entity (docs/03 §3.2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

ALERT_STATES = ("open", "recovered")
ALERT_SEVERITIES = ("info", "warning", "critical")


class AlertEvent(Base, TimestampMixin):
    """Open/recovered alert lifecycle with throttling support (v1.1 §5.10.4)."""

    __tablename__ = "alert_events"
    __table_args__ = (Index("ix_alert_events_state", "state"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False, default="warning")
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_raised_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )
    last_reminded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
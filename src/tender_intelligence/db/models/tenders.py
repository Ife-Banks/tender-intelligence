""":mod:`tender_intelligence.db.models.tenders` — Tender entity (docs/03 §3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_intelligence.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from tender_intelligence.db.models.documents import Document
    from tender_intelligence.db.models.sources import Source
    from tender_intelligence.db.models.deadline import TenderDeadlineResolution

TENDER_STATUSES = ("new", "updated", "processed", "verdict_failed", "awaiting_budget", "awaiting_approved_provider")


class Tender(Base, TimestampMixin):
    """A listing found on a source, de-duplicated per source (v1.1 §5.2)."""

    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_tenders_source_external"),
        Index("ix_tenders_deadline", "deadline"),
        Index("ix_tenders_correlation_id", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="new", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )
    correlation_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    is_update: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    source: Mapped[Source] = relationship(back_populates="tenders")
    documents: Mapped[list[Document]] = relationship(
        back_populates="tender", passive_deletes=True
    )
    # Prompt 12.1 — one-to-one resolution row (migration 0010, separate table)
    deadline_resolution: Mapped[TenderDeadlineResolution | None] = relationship(
        "TenderDeadlineResolution",
        back_populates="tender",
        uselist=False,
        passive_deletes=True,
        lazy="joined",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tender id={self.id} status={self.status!r}>"

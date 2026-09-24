""":mod:`tender_intelligence.db.models.deadline` — deadline resolution entity (Prompt 12.1).

One row per tender, created/updated by the deadline resolution stage (Stage 07 extension).
Kept in a separate table from ``tenders`` so:

  1. The ``Tender`` model is completely unchanged — existing tests at partial migration
     checkpoints continue to work.
  2. Deadline resolution state is a pure additive concern; nothing in Prompts 04–12
     depends on these columns being present.

``deadline_resolved``   — best available UTC deadline from the full source hierarchy.
                          NULL when status is UNRESOLVED or CONFLICTING.
``deadline_source``     — one of: listing_page / detail_page / document /
                          conflicting / unresolved.
``deadline_evidence``   — bounded JSON provenance (≤200-char excerpt; never secrets).
``deadline_resolved_at`` — when the resolution stage last ran (Timeline trigger).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_intelligence.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from tender_intelligence.db.models.tenders import Tender

#: Allowed values for ``deadline_source`` (mirrors deadline.model constants).
DEADLINE_SOURCES = (
    "listing_page",
    "detail_page",
    "document",
    "conflicting",
    "unresolved",
)


class TenderDeadlineResolution(Base, TimestampMixin):
    """One deadline resolution record per tender (Prompt 12.1, migration 0010)."""

    __tablename__ = "tender_deadline_resolution"
    __table_args__ = (
        Index(
            "uq_tender_deadline_resolution_tender",
            "tender_id",
            unique=True,
        ),
        Index("ix_tender_deadline_resolution_source", "deadline_source"),
        Index("ix_tender_deadline_resolution_resolved_at", "deadline_resolved_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deadline_resolved: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deadline_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    deadline_resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    tender: Mapped[Tender] = relationship(back_populates="deadline_resolution")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TenderDeadlineResolution tender_id={self.tender_id} "
            f"source={self.deadline_source!r}>"
        )

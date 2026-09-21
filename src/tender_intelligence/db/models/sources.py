""":mod:`tender_intelligence.db.models.sources` — Source entity (docs/03 §3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_intelligence.db.base import Base, TimestampMixin


class Source(Base, TimestampMixin):
    """A configurable tender site (v1.1 §5.1). Credentials are encrypted at rest."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    listing_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    parser_config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    crawl_frequency_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_languages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    recipient_scope: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    tenders: Mapped[list["Tender"]] = relationship(  # noqa: F821
        back_populates="source", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source id={self.id} name={self.name!r}>"
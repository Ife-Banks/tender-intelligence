""":mod:`tender_intelligence.db.models.verdicts` — Verdict entity (docs/03 §3.2)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

VERDICT_RECOMMENDATIONS = ("APPLY", "DO NOT APPLY", "APPLY WITH CONDITIONS")


class Verdict(Base, TimestampMixin):
    """The Stage B structured assessment (v1.1 §5.9.7)."""

    __tablename__ = "verdicts"
    __table_args__ = (
        Index("ix_verdicts_generated_at", "generated_at"),
        Index("ix_verdicts_recommendation", "recommendation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recommendation: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    background_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    requirements_summary: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    gap_analysis: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    urgency_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    knowledge_base_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_base_versions.id", ondelete="SET NULL"), nullable=True
    )
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    incomplete_inputs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

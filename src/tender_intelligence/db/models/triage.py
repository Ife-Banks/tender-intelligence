"""Persisted Stage A triage results (docs/07 §7.1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

TRIAGE_STATUSES = ("passed", "triage_discarded", "triage_failed")


class TriageResult(Base, TimestampMixin):
    """One durable relevance decision for a tender.

    The reasons are compact rule/model metadata only. Tender text, prompts, and provider
    responses are deliberately not stored here.
    """

    __tablename__ = "triage_results"
    __table_args__ = (
        Index("ix_triage_results_tender_id", "tender_id"),
        Index("ix_triage_results_correlation_id", "correlation_id"),
        Index("ix_triage_results_run_id", "run_id"),
        Index("ix_triage_results_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("run_history.id", ondelete="SET NULL"), nullable=True
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    llm_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

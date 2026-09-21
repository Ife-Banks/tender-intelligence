""":mod:`tender_intelligence.db.models.llm` — LLMProfile, LLMRoleAssignment, LLMCall."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_intelligence.db.base import Base, TimestampMixin

LLM_ROLES = ("triage", "verdict", "embeddings", "vision_ocr")


class LLMProfile(Base, TimestampMixin):
    """Saved configuration for talking to one model (v1.1 §5.9.1)."""

    __tablename__ = "llm_profiles"
    __table_args__ = (Index("ix_llm_profiles_name", "name", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    context_window_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=8000)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    extra_headers: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    supports_json: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_per_1k_input: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_per_1k_output: Mapped[float | None] = mapped_column(Float, nullable=True)
    approved_for_company_docs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LLMProfile id={self.id} name={self.name!r}>"


class LLMRoleAssignment(Base, TimestampMixin):
    """Binds a role to a profile (+ optional fallback). Fallback must obey the same
    ``approved_for_company_docs`` policy as the primary (v1.1 §5.9.3)."""

    __tablename__ = "llm_role_assignments"
    __table_args__ = (Index("ix_llm_roles_role", "role", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="RESTRICT"), nullable=False
    )
    fallback_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="RESTRICT"), nullable=True
    )

    profile: Mapped[LLMProfile] = relationship(foreign_keys=[profile_id])
    fallback_profile: Mapped[LLMProfile | None] = relationship(
        foreign_keys=[fallback_profile_id]
    )


class LLMCall(Base, TimestampMixin):
    """Per-call usage and outcome record. Metadata only — never the prompt/content
    (v1.1 §5.9.6); feeds the budget guard."""

    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_correlation_id", "correlation_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_profiles.id", ondelete="SET NULL"), nullable=True
    )
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    est_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
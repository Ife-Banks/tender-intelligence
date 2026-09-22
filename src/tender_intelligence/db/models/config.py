""":mod:`tender_intelligence.db.models.config` — Settings singleton + ConfigChangeLog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin


class Setting(Base, TimestampMixin):
    """System-wide configuration singleton (v1.1 §7). Test Mode is ON by default."""

    __tablename__ = "settings_singleton"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    test_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    test_mode_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    test_mode_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    test_mode_enabled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    urgency_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    triage_threshold: Mapped[float | None] = mapped_column(nullable=True)
    triage_rules: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    monthly_ai_budget: Mapped[float | None] = mapped_column(nullable=True)
    alert_thresholds: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    retention_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    link_expiry_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    @classmethod
    def seed_default(cls) -> Setting:
        """Return a fresh defaults singleton, ensuring the row always exists with id=1."""
        return cls(id=1)


class ConfigChangeLog(Base, TimestampMixin):
    """Audit trail of configuration changes — never stores secret values (docs/03 §3.2)."""

    __tablename__ = "config_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(nullable=True)
    changed_fields: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

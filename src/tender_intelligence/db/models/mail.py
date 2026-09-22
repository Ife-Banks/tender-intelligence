""":mod:`tender_intelligence.db.models.mail` — MailProvider, NotificationLog, Attempt."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

NOTIFICATION_STATUSES = ("sent", "failed", "pending_retry")


class MailProvider(Base, TimestampMixin):
    """Mail provider configuration plus breaker state (v1.1 §5.10.2)."""

    __tablename__ = "mail_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    from_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    breaker_state: Mapped[str] = mapped_column(String(24), nullable=False, default="closed")
    breaker_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<MailProvider id={self.id} name={self.name!r}>"


class NotificationLog(Base, TimestampMixin):
    """Message-level record; source of truth for what was sent (v1.1 §5.7, §5.10.1)."""

    __tablename__ = "notification_logs"
    __table_args__ = (
        Index("ix_notifications_dedupe_key", "dedupe_key", unique=True),
        Index("ix_notifications_sent_at", "sent_at"),
        Index("ix_notifications_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    verdict_id: Mapped[int | None] = mapped_column(
        ForeignKey("verdicts.id", ondelete="SET NULL"), nullable=True
    )
    recipients_snapshot: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    links: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="sent")
    provider_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationAttempt(Base, TimestampMixin):
    """One attempt through one provider (v1.1 §5.10.2)."""

    __tablename__ = "notification_attempts"
    __table_args__ = (Index("ix_notification_attempts_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notification_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_providers.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="failed")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )

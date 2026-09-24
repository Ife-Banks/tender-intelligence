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
from sqlalchemy.orm import Mapped, mapped_column, validates

from tender_intelligence.crypto.secrets import ENVELOPE_PREFIX
from tender_intelligence.db.base import Base, TimestampMixin
from tender_intelligence.db.models.recipients import is_valid_email

# Confirmed data-model values (docs/03 §3.2).  The durable outbox additionally uses the
# lifecycle values below as an additive implementation state machine; keeping the confirmed
# tuple stable avoids breaking existing API/UI consumers.
NOTIFICATION_STATUSES = ("sent", "failed", "pending_retry")
NOTIFICATION_OUTBOX_STATUSES = (
    "pending",
    "sending",
    "sent",
    "failed",
    "pending_retry",
    "possible_duplicate",
)


class MailProviderUsage(Base, TimestampMixin):
    """Durable per-provider request counters for configured daily/rate limits."""

    __tablename__ = "mail_provider_usage"
    __table_args__ = (
        Index("ix_mail_provider_usage_provider_id", "provider_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("mail_providers.id", ondelete="CASCADE"), nullable=False
    )
    minute_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    minute_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    day_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
    breaker_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    @validates("credentials_encrypted")
    def _validate_credentials_envelope(
        self, _key: str, value: str | None
    ) -> str | None:
        if value is not None and not value.startswith(ENVELOPE_PREFIX):
            raise ValueError("provider credentials must use the encrypted envelope format")
        return value

    @validates("provider_type")
    def _validate_provider_type(self, _key: str, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("provider_type must not be empty")
        return value.strip().lower()

    @validates("from_address")
    def _validate_from_address(self, _key: str, value: str) -> str:
        if not is_valid_email(value):
            raise ValueError("invalid provider from_address")
        return value

    @validates("reply_to")
    def _validate_reply_to(self, _key: str, value: str | None) -> str | None:
        if value is not None and not is_valid_email(value):
            raise ValueError("invalid provider reply_to")
        return value

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
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    recipients_snapshot: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachments: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    links: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    provider_used: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Durable outbox metadata.  These are additive implementation fields, not new business
    # choices: they make a reservation survive a crash and let two workers claim one row.
    notification_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    message_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NotificationAttempt(Base, TimestampMixin):
    """One attempt through one provider (v1.1 §5.10.2)."""

    __tablename__ = "notification_attempts"
    __table_args__ = (Index("ix_notification_attempts_status", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notification_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("mail_providers.id", ondelete="SET NULL"), nullable=True
    )
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="failed")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.now
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

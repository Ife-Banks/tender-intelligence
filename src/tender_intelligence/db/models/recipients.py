""":mod:`tender_intelligence.db.models.recipients` — Recipient entity (docs/03 §3.2)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

RECIPIENT_LIST_TYPES = ("tender", "dev_alert")
RECIPIENT_DELIVERY = ("to", "cc", "bcc")
RECIPIENT_RECEIVES_FILTERS = ("all", "apply", "urgent")


def is_valid_email(email: str) -> bool:
    """Minimal RFC-5322 sanity check for addresses used to seed recipients."""
    if len(email) > 255 or "@" not in email:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    return all(0x20 < ord(c) < 0x7F and c not in " \t\r\n" for c in email)


class Recipient(Base, TimestampMixin):
    """One person/address on the tender or dev-alert list (v1.1 §5.10.1)."""

    __tablename__ = "recipients"
    __table_args__ = (
        Index("ix_recipients_list_type", "list_type"),
        Index("ix_recipients_active", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    list_type: Mapped[str] = mapped_column(String(24), nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False, default="to")
    source_scope: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    receives_filter: Mapped[str] = mapped_column(String(24), nullable=False, default="all")
    alert_types: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    min_severity: Mapped[str] = mapped_column(String(24), nullable=False, default="critical")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Recipient id={self.id} list={self.list_type!r} email={self.email!r}>"

""":mod:`tender_intelligence.db.models.recipients` — Recipient entity (docs/03 §3.2)."""

from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any

from sqlalchemy import JSON, Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, validates

from tender_intelligence.db.base import Base, TimestampMixin

RECIPIENT_LIST_TYPES = ("tender", "dev_alert")
RECIPIENT_DELIVERY = ("to", "cc", "bcc")
RECIPIENT_RECEIVES_FILTERS = ("all", "apply", "urgent")


def is_valid_email(email: str) -> bool:
    """Validate a conservative single-address form before configuration persistence."""

    if not isinstance(email, str) or len(email) > 255 or email.count("@") != 1:
        return False
    parsed_name, parsed_address = parseaddr(email)
    if parsed_name or parsed_address != email:
        return False
    local, domain = email.split("@", 1)
    if not local or len(local) > 64 or not domain or len(domain) > 253:
        return False
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False
    if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+", local):
        return False
    labels = domain.split(".")
    if any(not label or len(label) > 63 for label in labels):
        return False
    if not all(re.fullmatch(r"[A-Za-z0-9-]+", label) for label in labels):
        return False
    return all(0x20 < ord(char) < 0x7F and char not in " \t\r\n" for char in email)


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

    @validates("email")
    def _validate_email(self, _key: str, value: str) -> str:
        if not is_valid_email(value):
            raise ValueError("invalid recipient email")
        return value.strip().lower()

    @validates("list_type")
    def _validate_list_type(self, _key: str, value: str) -> str:
        if value not in RECIPIENT_LIST_TYPES:
            raise ValueError("invalid recipient list_type")
        return value

    @validates("delivery")
    def _validate_delivery(self, _key: str, value: str) -> str:
        if value not in RECIPIENT_DELIVERY:
            raise ValueError("invalid recipient delivery")
        return value

    @validates("receives_filter")
    def _validate_receives(self, _key: str, value: str) -> str:
        if value not in RECIPIENT_RECEIVES_FILTERS:
            raise ValueError("invalid recipient receives_filter")
        return value

    @validates("min_severity")
    def _validate_severity(self, _key: str, value: str) -> str:
        if value not in {"info", "warning", "critical"}:
            raise ValueError("invalid recipient min_severity")
        return value

    @property
    def recipient_scope(self) -> list[Any] | None:
        """Prompt-facing alias for the confirmed ``source_scope`` field."""

        return self.source_scope

    @recipient_scope.setter
    def recipient_scope(self, value: list[Any] | None) -> None:
        self.source_scope = value

    @property
    def receives(self) -> str:
        """Prompt-facing alias for the confirmed ``receives_filter`` field."""

        return self.receives_filter

    @receives.setter
    def receives(self, value: str) -> None:
        self.receives_filter = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Recipient id={self.id} list={self.list_type!r} email={self.email!r}>"

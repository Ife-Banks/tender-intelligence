""":mod:`tender_intelligence.db.models.users` — AdminUser entity (docs/03 §3.2)."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

ADMIN_ROLES = ("admin", "viewer")


class AdminUser(Base, TimestampMixin):
    """Admin-app account. Viewers never see KB or secrets (docs/03 §3.2, confirmed).
    Exact login method and account provisioning = open decision (docs/13 O11)."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
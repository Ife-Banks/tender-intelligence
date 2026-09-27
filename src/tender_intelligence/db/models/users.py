""":mod:`tender_intelligence.db.models.users` — reserved legacy AdminUser table (O11 deferred)."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin

ADMIN_ROLES = ("admin", "viewer")


class AdminUser(Base, TimestampMixin):
    """Reserved schema artifact from the original v1.1 proposal; current Admin API does not use identities (O11)."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

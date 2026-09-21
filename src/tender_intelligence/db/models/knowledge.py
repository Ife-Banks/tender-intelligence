""":mod:`tender_intelligence.db.models.knowledge` — KnowledgeBaseVersion (docs/03 §3.2)."""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tender_intelligence.db.base import Base, TimestampMixin


class KnowledgeBaseVersion(Base, TimestampMixin):
    """Immutable snapshot of the single structured OPEX knowledge document (v1.1 §5.5)."""

    __tablename__ = "knowledge_base_versions"
    __table_args__ = (
        Index("ix_kb_versions_content_hash", "content_hash", unique=True),
        Index("ix_kb_versions_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_ref: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeBaseVersion id={self.id} tokens={self.token_count}>"
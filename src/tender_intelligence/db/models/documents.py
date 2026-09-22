""":mod:`tender_intelligence.db.models.documents` — Document entity (docs/03 §3.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tender_intelligence.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from tender_intelligence.db.models.tenders import Tender

DOWNLOAD_STATUSES = ("pending", "downloaded", "failed")
EXTRACTION_STATUSES = ("pending", "extracted", "failed", "skipped")


class Document(Base, TimestampMixin):
    """One attachment under a tender; tracks download + extraction state."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tender_id", "checksum", name="uq_documents_tender_checksum"),
        Index("ix_documents_checksum", "checksum"),
        Index("ix_documents_download_status", "download_status"),
        Index("ix_documents_extraction_status", "extraction_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(
        ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=False)
    extracted_text_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    extraction_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending"
    )
    extraction_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    tender: Mapped[Tender] = relationship(back_populates="documents")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} filename={self.filename!r}>"

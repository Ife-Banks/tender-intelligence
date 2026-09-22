""":mod:`tender_intelligence.db.repositories.sources` — Source reads (prompt 06)."""

from __future__ import annotations

from sqlalchemy import select

from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories.base import Repository


class SourceRepository(Repository):
    """Read access to configured discovery sources (docs/03 §3.1)."""

    def get(self, source_id: int) -> Source | None:
        return self.session.get(Source, source_id)

    def exists(self, source_id: int) -> bool:
        return self.session.scalar(select(Source.id).where(Source.id == source_id)) is not None

    def list_all(self) -> list[Source]:
        return list(self.session.scalars(select(Source).order_by(Source.id)).all())

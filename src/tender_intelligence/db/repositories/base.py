""":mod:`tender_intelligence.db.repositories.base` — persistence error + repo base.

Prompt 06 owns row-level persistence, so failures surface as a single, structured
:class:`PersistenceError` carrying a machine-readable ``error_code`` from
:mod:`tender_intelligence.core.errors` (prompt 06 §4, §10). Repositories never own
commits/rollbacks — commit points stay with the transactional owner (e.g.
:class:`tender_intelligence.dedup.service.DedupService` for prompt 05's run transaction).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tender_intelligence.core.errors import PERSISTENCE_TRANSACTION_FAILED


class PersistenceError(Exception):
    """Structured persistence failure carrying a machine-readable code (prompt 06 §4, §10)."""

    def __init__(
        self,
        message: str,
        error_code: str = PERSISTENCE_TRANSACTION_FAILED,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.context = context or {}


class Repository:
    """Base class for session-scoped repositories.

    A repository wraps a single :class:`Session` and performs row-level reads/writes. It
    never commits: the caller (service/transaction owner) decides the commit boundary.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

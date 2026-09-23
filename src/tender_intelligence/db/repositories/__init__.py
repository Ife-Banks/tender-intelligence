"""Persistence/repository layer for prompt 06 (tender persistence).

Consumers (prompt 05's dedup service, pipeline stages, admin/API) use these
session-scoped repositories for row-level operations. Repositories never commit;
the caller owns the transaction boundary. Failures raise :class:`PersistenceError`
with machine-readable codes from :mod:`tender_intelligence.core.errors`.
"""

from tender_intelligence.db.repositories.base import PersistenceError, Repository
from tender_intelligence.db.repositories.documents import DocumentRepository
from tender_intelligence.db.repositories.runs import RunCounts, RunHistoryRepository
from tender_intelligence.db.repositories.sources import SourceRepository
from tender_intelligence.db.repositories.status import (
    TENDER_TRANSITION_MATRIX,
    assert_valid_transition,
)
from tender_intelligence.db.repositories.tenders import (
    TenderClaim,
    TenderRepository,
)

__all__ = [
    "DocumentRepository",
    "PersistenceError",
    "Repository",
    "RunCounts",
    "RunHistoryRepository",
    "SourceRepository",
    "TENDER_TRANSITION_MATRIX",
    "TenderClaim",
    "TenderRepository",
    "assert_valid_transition",
]

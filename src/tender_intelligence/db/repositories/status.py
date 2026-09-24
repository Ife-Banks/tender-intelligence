""":mod:`tender_intelligence.db.repositories.status` — status enums and transition rules.

Tender statuses and document statuses are defined by the data model (docs/03 §3.2;
tenders.py / documents.py). The specification does not publish an explicit transition
table, so this module encodes only the transitions the documented lifecycle requires
(docs/04 §4.13, §4.14, v1.1 §5.2/§5.6) plus idempotent self-transitions; everything else
is rejected with ``PERSISTENCE_INVALID_STATUS_TRANSITION`` so unknown/un-spec'd moves
fail loudly rather than being persisted silently (prompt 06 §6, docs/04 §4.3).

Derivation notes (also flagged as assumptions in the prompt-06 report):
- ``processed -> updated`` is required by docs/04 §4.14 (an addendum/deadline change to a
  previously processed tender is a distinct update event).
- ``awaiting_budget -> processed`` is required by docs/04 §4.13 (budget restored, run resumes).
- ``awaiting_budget -> updated``: a now-seen tender can still receive a material change
  while queued for budget (same §4.14 semantics; inferred, no explicit contradicting edge).
- ``verdict_failed`` has no documented recovery edge (v1.1 §5.6 marks the run errored and
  falls back to the raw notice), so it is terminal here.
"""

from __future__ import annotations

from typing import Final

from tender_intelligence.core.errors import PERSISTENCE_INVALID_STATUS_TRANSITION
from tender_intelligence.db.models.documents import (
    DOWNLOAD_STATUSES,
    EXTRACTION_STATUSES,
    Document,
)
from tender_intelligence.db.models.tenders import TENDER_STATUSES, Tender
from tender_intelligence.db.repositories.base import PersistenceError

# Supported outgoing-target sets per current tender status.
TENDER_TRANSITION_MATRIX: Final[dict[str, frozenset[str]]] = {
    "new": frozenset(TENDER_STATUSES),
    "updated": frozenset({"updated", "processed", "verdict_failed", "awaiting_budget", "awaiting_approved_provider"}),
    "processed": frozenset({"processed", "updated"}),
    "awaiting_budget": frozenset({"awaiting_budget", "updated", "processed", "verdict_failed", "awaiting_approved_provider"}),
    "awaiting_approved_provider": frozenset({"awaiting_approved_provider", "updated", "processed", "verdict_failed", "awaiting_budget"}),
    "verdict_failed": frozenset({"verdict_failed"}),
}


def assert_valid_tender_status(value: str, *, entity_id: int | None = None) -> None:
    if value not in TENDER_STATUSES:
        raise PersistenceError(
            f"unknown tender status {value!r}; expected one of {sorted(TENDER_STATUSES)}",
            error_code=PERSISTENCE_INVALID_STATUS_TRANSITION,
            context={"entity_id": entity_id, "status": value},
        )


def assert_valid_transition(tender: Tender, new_status: str) -> None:
    assert_valid_tender_status(new_status, entity_id=tender.id)
    allowed = TENDER_TRANSITION_MATRIX.get(tender.status)
    if allowed is None or new_status not in allowed:
        raise PersistenceError(
            f"status {tender.status!r} -> {new_status!r} is not a supported transition",
            error_code=PERSISTENCE_INVALID_STATUS_TRANSITION,
            context={
                "tender_id": tender.id,
                "source_id": tender.source_id,
                "external_id": tender.external_id,
                "current_status": tender.status,
                "requested_status": new_status,
            },
        )


def assert_valid_document_status(doc: Document, field: str, value: str) -> None:
    catalog: dict[str, tuple[str, ...]] = {
        "download_status": DOWNLOAD_STATUSES,
        "extraction_status": EXTRACTION_STATUSES,
    }
    allowed = catalog.get(field)
    if allowed is None:
        raise PersistenceError(
            f"unknown document status field {field!r}",
            error_code=PERSISTENCE_INVALID_STATUS_TRANSITION,
            context={"document_id": doc.id, "field": field, "status": value},
        )
    if value not in allowed:
        raise PersistenceError(
            f"invalid document {field} value {value!r}; expected one of {sorted(allowed)}",
            error_code=PERSISTENCE_INVALID_STATUS_TRANSITION,
            context={"document_id": doc.id, "field": field, "status": value},
        )

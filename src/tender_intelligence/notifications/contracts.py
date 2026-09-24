"""Small event contract consumed by the notification layer.

The current pipeline persists verdicts and documents but does not yet have a Prompt 13/14
notification event table.  This value object is the seam for those future stages: it carries
the already-produced verdict identity and the deduplicator's change classification, without
importing or reimplementing either stage.
"""

from __future__ import annotations

from dataclasses import dataclass

from tender_intelligence.mail.templates import NotificationKind


@dataclass(frozen=True)
class NotificationEvent:
    """A tender notification candidate produced by an upstream stage.

    ``material_change`` and ``change_types`` come from Prompt 05's ``DedupOutcome`` when an
    update is wired.  ``changed_document_ids`` is optional because the current document
    repository does not persist a document diff; when omitted, the service conservatively
    carries the current tender document set and records that fact rather than guessing which
    files changed.
    """

    tender_id: int
    verdict_id: int
    kind: NotificationKind = NotificationKind.NEW
    material_change: bool = True
    change_types: tuple[str, ...] = ()
    change_details: tuple[tuple[str, str], ...] = ()
    changed_document_ids: tuple[int, ...] = ()
    correlation_id: str | None = None


__all__ = ["NotificationEvent"]

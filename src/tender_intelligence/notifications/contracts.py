"""Small event contract consumed by the notification layer.

The current pipeline persists verdicts and documents but does not yet have a Prompt 13/14
notification event table.  This value object is the seam for those future stages: it carries
the already-produced verdict identity and the deduplicator's change classification, without
importing or reimplementing either stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationKind(StrEnum):
    """Channel-neutral category for a tender notification.

    ``RAW_NOTICE`` is the v1.1 §5.6 failure path: Stage B could not produce a verdict, so the
    tender is still reported — carrying the source notice and a clear "automatic assessment
    unavailable" banner — rather than being silently dropped (docs/04 §4.2 row 7).
    """

    NEW = "new"
    UPDATE = "update"
    RAW_NOTICE = "raw_notice"


@dataclass(frozen=True)
class NotificationEvent:
    """A tender notification candidate produced by an upstream stage.

    ``material_change`` and ``change_types`` come from Prompt 05's ``DedupOutcome`` when an
    update is wired.  ``changed_document_ids`` is optional because the current document
    repository does not persist a document diff; when omitted, the service conservatively
    carries the current tender document set and records that fact rather than guessing which
    files changed.

    ``verdict_id`` is optional **only** for :attr:`NotificationKind.RAW_NOTICE`, which exists
    precisely because no verdict exists. Every other kind still requires one, and the service
    rejects a verdict-less event that is not a raw notice, so the relaxation cannot be used to
    send a normal assessment mail with no assessment behind it.
    """

    tender_id: int
    verdict_id: int | None = None
    kind: NotificationKind = NotificationKind.NEW
    material_change: bool = True
    change_types: tuple[str, ...] = ()
    change_details: tuple[tuple[str, str], ...] = ()
    changed_document_ids: tuple[int, ...] = ()
    correlation_id: str | None = None
    #: Why the assessment is unavailable, surfaced verbatim in the raw-notice banner.
    unavailability_reason: str | None = None


@dataclass(frozen=True)
class NotificationOutcome:
    """Channel-neutral delivery outcome, including the existing audit record identity."""

    status: str
    notification_log_id: int | None = None
    provider_used: str | None = None
    possible_duplicate: bool = False
    error_code: str | None = None
    dedupe_key: str = ""


__all__ = ["NotificationEvent", "NotificationKind", "NotificationOutcome"]

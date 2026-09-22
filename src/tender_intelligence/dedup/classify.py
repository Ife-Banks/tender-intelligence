""":mod:`tender_intelligence.dedup.classify` — deterministic change detection (prompt 05 §2).

Compares a fresh discovery candidate (:class:`TenderListing`) against a persisted seen
:class:`Tender` row using **normalized** values, so incidental parser differences never count
as material changes. Materiality is scoped by :class:`ChangePolicy` (prompt 05: "do not invent
a materiality rule that contradicts the specification"; the policy's defaults encode docs/04
§4.14 — addendum / extended deadline are material, arbitrary incidental diffs are not).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.interfaces.source import TenderListing

NEW: Final[str] = "NEW"
UPDATE: Final[str] = "UPDATE"
UNCHANGED: Final[str] = "UNCHANGED"

TITLE_CHANGED: Final[str] = "TITLE_CHANGED"
DEADLINE_CHANGED: Final[str] = "DEADLINE_CHANGED"
ADDENDUM_ADDED: Final[str] = "ADDENDUM_ADDED"
CHANGE_MARKER_CHANGED: Final[str] = "CHANGE_MARKER_CHANGED"
OTHER_MATERIAL_CHANGE: Final[str] = "OTHER_MATERIAL_CHANGE"

#: Reserved raw_metadata key that carries an addendum/change marker when a source emits one.
#: Prompt 04's WAHO adapter never populates it today (see docs/04a) — the seam is defined so
#: classification is contract-ready; ADDENDUM_ADDED / CHANGE_MARKER_CHANGED only fire once a
#: source supplies this marker (documented gap).
ADDENDUM_MARKER_KEY: Final[str] = "addendum_marker"

COMPARISON_FIELDS: Final[tuple[str, ...]] = (
    "title",
    "url",
    "published_at",
    "deadline",
    "deadline_timezone",
    "reference",
    ADDENDUM_MARKER_KEY,
)

#: Field → structured change type. ``addendum_marker`` is refined at runtime: an empty prior
#: value that becomes non-empty is ADDENDUM_ADDED, otherwise CHANGE_MARKER_CHANGED.
CHANGE_TYPE_BY_FIELD: Final[dict[str, str]] = {
    "title": TITLE_CHANGED,
    "url": OTHER_MATERIAL_CHANGE,
    "published_at": OTHER_MATERIAL_CHANGE,
    "deadline": DEADLINE_CHANGED,
    "deadline_timezone": OTHER_MATERIAL_CHANGE,
    "reference": OTHER_MATERIAL_CHANGE,
    ADDENDUM_MARKER_KEY: CHANGE_MARKER_CHANGED,
}

#: Fields whose difference triggers an UPDATE (docs/04 §4.14). Anything else, however it
#: differs, is an incidental/parser-format difference that stays UNCHANGED until configured.
DEFAULT_MATERIAL_FIELDS: Final[frozenset[str]] = frozenset(
    {"title", "deadline", ADDENDUM_MARKER_KEY}
)


def _norm_text(value: str | None) -> str:
    """Collapse whitespace so significant formatting differences vanish (prompt 05 §2)."""
    return " ".join((value or "").split())


def _norm_dt(value: datetime | None) -> datetime | None:
    """Normalise a datetime for instant comparison.

    Naive datetimes are treated as UTC: with the original timezone stored separately in
    ``deadline_timezone``, and the WAHO site publishing in UTC, this keeps comparisons
    deterministic across aware/naive round-trips (SQLite returns naive datetimes).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _norm_ref(metadata: dict[str, Any] | None) -> str:
    value = metadata.get("reference") if metadata else None
    if not value:
        return ""
    return _norm_text(str(value))


def _norm_marker(metadata: dict[str, Any] | None) -> str:
    value = metadata.get(ADDENDUM_MARKER_KEY) if metadata else None
    if not value:
        return ""
    return _norm_text(str(value))


def normalize_listing(listing: TenderListing) -> dict[str, Any]:
    """Project a discovery candidate onto the comparison contract (prompt 05 §2)."""
    meta = listing.raw_metadata or {}
    return {
        "title": _norm_text(listing.title),
        "url": _norm_text(listing.url),
        "published_at": _norm_dt(listing.published_at),
        "deadline": _norm_dt(listing.deadline_at),
        "deadline_timezone": _norm_text(listing.deadline_timezone),
        "reference": _norm_ref(meta),
        ADDENDUM_MARKER_KEY: _norm_marker(meta),
    }


def normalize_tender(tender: Tender) -> dict[str, Any]:
    """Project a persisted seen row onto the same comparison contract."""
    meta = tender.raw_metadata or {}
    return {
        "title": _norm_text(tender.title),
        "url": _norm_text(tender.url),
        "published_at": _norm_dt(tender.published_date),
        "deadline": _norm_dt(tender.deadline),
        "deadline_timezone": _norm_text(tender.deadline_timezone),
        "reference": _norm_ref(meta),
        ADDENDUM_MARKER_KEY: _norm_marker(meta),
    }


@dataclass(frozen=True)
class ChangePolicy:
    """Which fields are material for update detection (prompt 05 §2, docs/04 §4.14)."""

    material_fields: frozenset[str] = field(default_factory=lambda: DEFAULT_MATERIAL_FIELDS)

    @classmethod
    def default(cls) -> ChangePolicy:
        return ChangePolicy()

    def is_material(self, field: str) -> bool:
        return field in self.material_fields


@dataclass(frozen=True)
class Comparison:
    """Deterministic result of comparing a candidate against a seen row (prompt 05 §2, §4)."""

    current: dict[str, Any]
    seen: dict[str, Any]
    changed_fields: tuple[str, ...]
    change_types: tuple[str, ...]
    material_change: bool

    @property
    def classification(self) -> str:
        return UPDATE if self.material_change else UNCHANGED


def compare(
    listing: TenderListing,
    tender: Tender,
    policy: ChangePolicy | None = None,
) -> Comparison:
    """Classify a seen candidate: UPDATE when a material change exists, else UNCHANGED.

    NEW (no seen record) is decided by the caller, not here — there is nothing to diff
    against (prompt 05 §1).
    """
    policy = policy or ChangePolicy.default()
    current = normalize_listing(listing)
    seen = normalize_tender(tender)

    changed: list[str] = []
    for key in COMPARISON_FIELDS:
        if current.get(key) != seen.get(key):
            changed.append(key)

    change_types: list[str] = []
    for key in changed:
        if key == ADDENDUM_MARKER_KEY and policy.is_material(key):
            change_types.append(
                ADDENDUM_ADDED if not seen.get(ADDENDUM_MARKER_KEY) else CHANGE_MARKER_CHANGED
            )
        elif policy.is_material(key):
            change_types.append(CHANGE_TYPE_BY_FIELD.get(key, OTHER_MATERIAL_CHANGE))

    material = bool(change_types)
    return Comparison(
        current=current,
        seen=seen,
        changed_fields=tuple(changed),
        change_types=tuple(change_types),
        material_change=material,
    )

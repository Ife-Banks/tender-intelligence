"""Unit tests for deterministic dedup change detection (prompt 05 §2)."""

from __future__ import annotations

from datetime import UTC, datetime

from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.dedup.classify import (
    ADDENDUM_ADDED,
    ADDENDUM_MARKER_KEY,
    CHANGE_MARKER_CHANGED,
    DEADLINE_CHANGED,
    OTHER_MATERIAL_CHANGE,
    TITLE_CHANGED,
    UNCHANGED,
    UPDATE,
    ChangePolicy,
    compare,
    normalize_listing,
)
from tender_intelligence.interfaces.source import TenderListing

D = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)


def _listing(**overrides) -> TenderListing:
    base = {
        "external_id": "WAHO-123",
        "title": "  Senior   Health Officer  ",
        "url": "https://data.wahooas.org/tenders/tenders/123/list",
        "published_at": D,
        "deadline_at": D,
        "deadline_timezone": "UTC",
        "raw_metadata": {"reference": "REF-7"},
    }
    base.update(overrides)
    return TenderListing(**base)


def _tender(**overrides) -> Tender:
    base = {
        "source_id": 1,
        "external_id": "WAHO-123",
        "title": "Senior Health Officer",
        "url": "https://data.wahooas.org/tenders/tenders/123/list",
        "published_date": D,
        "deadline": D,
        "deadline_timezone": "UTC",
        "raw_metadata": {"reference": "REF-7"},
        "status": "new",
        "correlation_id": "tender-correlation",
        "is_update": False,
    }
    base.update(overrides)
    return Tender(**base)


def test_normalize_collapses_whitespace() -> None:
    listing = _listing()
    values = normalize_listing(listing)
    assert values["title"] == "Senior Health Officer"


def test_identical_listing_is_unchanged() -> None:
    listing, tender = _listing(), _tender()
    result = compare(listing, tender)
    assert result.classification == UNCHANGED
    assert result.material_change is False
    assert result.changed_fields == ()
    assert result.change_types == ()


def test_title_change_is_update() -> None:
    listing, tender = _listing(title="  Procurement   Analyst  "), _tender()
    result = compare(listing, tender)
    assert result.classification == UPDATE
    assert result.material_change is True
    assert result.change_types == (TITLE_CHANGED,)
    assert result.changed_fields == ("title",)


def test_deadline_change_is_update() -> None:
    new_deadline = D.replace(day=30)
    listing, tender = _listing(deadline_at=new_deadline), _tender()
    result = compare(listing, tender)
    assert result.classification == UPDATE
    assert result.material_change is True
    assert result.change_types == (DEADLINE_CHANGED,)
    assert "deadline" in result.changed_fields


def test_naive_vs_aware_same_instant_is_unchanged() -> None:
    listing = _listing(deadline_at=D.replace(tzinfo=None))
    tender = _tender()
    result = compare(listing, tender)
    assert result.classification == UNCHANGED


def test_reference_only_change_is_unchanged_by_default() -> None:
    listing = _listing(raw_metadata={"reference": "REF-999"})
    tender = _tender()
    result = compare(listing, tender)
    assert result.classification == UNCHANGED
    assert result.material_change is False
    assert "reference" in result.changed_fields


def test_url_only_change_is_unchanged_by_default() -> None:
    listing = _listing(url="https://data.wahooas.org/tenders/tenders/999/list")
    result = compare(listing, _tender())
    assert result.classification == UNCHANGED


def test_addendum_added_is_material() -> None:
    listing = _listing(raw_metadata={"reference": "REF-7", ADDENDUM_MARKER_KEY: "addendum-1.pdf"})
    tender = _tender()
    result = compare(listing, tender)
    assert result.classification == UPDATE
    assert result.material_change is True
    assert result.change_types == (ADDENDUM_ADDED,)


def test_addendum_marker_changed_is_material() -> None:
    listing = _listing(raw_metadata={"reference": "REF-7", ADDENDUM_MARKER_KEY: "addendum-2.pdf"})
    tender = _tender(raw_metadata={"reference": "REF-7", ADDENDUM_MARKER_KEY: "addendum-1.pdf"})
    result = compare(listing, tender)
    assert result.classification == UPDATE
    assert result.material_change is True
    assert result.change_types == (CHANGE_MARKER_CHANGED,)


def test_custom_policy_can_mark_other_fields_material() -> None:
    policy = ChangePolicy(material_fields=frozenset({"reference"}))
    listing = _listing(raw_metadata={"reference": "REF-999"})
    result = compare(listing, _tender(), policy)
    assert result.classification == UPDATE
    assert result.material_change is True
    assert result.change_types == (OTHER_MATERIAL_CHANGE,)


def test_changed_fields_order_is_deterministic() -> None:
    listing = _listing(
        title="New Title",
        url="https://x.test/other",
        raw_metadata={"reference": "NEW-REF"},
    )
    result = compare(listing, _tender())
    assert result.changed_fields == ("title", "url", "reference")


def test_multiple_material_changes_collect_all_types() -> None:
    listing = _listing(title="Brand New", deadline_at=D.replace(month=10))
    result = compare(listing, _tender())
    assert result.material_change is True
    assert set(result.change_types) == {TITLE_CHANGED, DEADLINE_CHANGED}

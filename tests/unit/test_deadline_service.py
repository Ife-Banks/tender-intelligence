"""Unit tests for DeadlineResolutionService (Prompt 12.1 §16)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tender_intelligence.deadline.model import (
    CONFLICTING,
    REASON_CONFLICTING_SOURCES,
    REASON_MISSING_TIMEZONE,
    REASON_NOT_PRESENT_ON_LISTING,
    RESOLVED,
    SOURCE_DETAIL,
    SOURCE_DOCUMENT,
    SOURCE_LISTING,
    UNRESOLVED,
    DeadlineResult,
)
from tender_intelligence.deadline.service import (
    CONFLICT_TOLERANCE_SECONDS,
    DeadlineResolutionService,
)
from tender_intelligence.interfaces.source import TenderDetail, TenderListing

GMT = timezone.utc   # UTC == GMT for deadline purposes
WAT = timezone(timedelta(hours=1))

D_LISTING = datetime(2026, 9, 24, 13, 0, tzinfo=GMT)
D_DETAIL   = datetime(2026, 9, 24, 13, 0, tzinfo=GMT)   # same → no conflict
D_CONFLICT = datetime(2026, 9, 30, 17, 0, tzinfo=GMT)   # different → conflict


def _listing(deadline_at=None, deadline_timezone=None, raw_meta=None) -> TenderListing:
    return TenderListing(
        external_id="167",
        title="Test Tender",
        url="https://data.wahooas.org/tenders/tenders/167/list",
        deadline_at=deadline_at,
        deadline_timezone=deadline_timezone,
        raw_metadata=raw_meta or {},
    )


def _detail(deadline_at=None, deadline_timezone=None, raw_meta=None) -> TenderDetail:
    listing = _listing(deadline_at=deadline_at, deadline_timezone=deadline_timezone,
                       raw_meta=raw_meta)
    return TenderDetail(listing=listing)


SVC = DeadlineResolutionService()


class TestListingPageSource:
    def test_listing_deadline_resolved(self):
        r = SVC.resolve(_listing(D_LISTING, "GMT"))
        assert r.status == RESOLVED
        assert r.source == SOURCE_LISTING
        assert r.deadline_utc == D_LISTING

    def test_listing_deadline_timezone_preserved(self):
        r = SVC.resolve(_listing(D_LISTING, "WAT"))
        assert r.deadline_timezone == "WAT"

    def test_listing_no_deadline_no_detail_unresolved(self):
        r = SVC.resolve(_listing())
        assert r.status == UNRESOLVED
        assert r.evidence["reason_code"] == REASON_NOT_PRESENT_ON_LISTING

    def test_listing_deadline_raw_text_preserved_when_tz_missing(self):
        r = SVC.resolve(_listing(raw_meta={"deadline_raw": "Deadline: 24 September 2026"}))
        assert r.status == UNRESOLVED
        assert r.evidence.get("reason_code") == REASON_MISSING_TIMEZONE


class TestDetailPageFallback:
    def test_detail_used_when_listing_has_no_deadline(self):
        r = SVC.resolve(_listing(), detail=_detail(D_DETAIL, "GMT"))
        assert r.status == RESOLVED
        assert r.source == SOURCE_DETAIL
        assert r.deadline_utc == D_DETAIL

    def test_listing_wins_over_detail_when_both_resolved(self):
        # Same deadline — listing wins (no conflict)
        r = SVC.resolve(_listing(D_LISTING, "GMT"), detail=_detail(D_DETAIL, "GMT"))
        assert r.status == RESOLVED
        assert r.source == SOURCE_LISTING

    def test_detail_none_falls_through(self):
        r = SVC.resolve(_listing(), detail=None)
        assert r.status == UNRESOLVED


class TestConflictDetection:
    def test_conflict_when_listing_and_detail_disagree(self):
        r = SVC.resolve(_listing(D_LISTING, "GMT"), detail=_detail(D_CONFLICT, "GMT"))
        assert r.status == CONFLICTING
        assert r.evidence["reason_code"] == REASON_CONFLICTING_SOURCES
        assert "conflict_a" in r.evidence
        assert "conflict_b" in r.evidence

    def test_no_conflict_within_tolerance(self):
        # 10-minute difference is within the 15-minute tolerance
        d_close = datetime(2026, 9, 24, 13, 10, tzinfo=GMT)
        r = SVC.resolve(_listing(D_LISTING, "GMT"), detail=_detail(d_close, "GMT"))
        # Should not be CONFLICTING
        assert r.status != CONFLICTING

    def test_conflict_at_boundary(self):
        d_over = D_LISTING + timedelta(seconds=CONFLICT_TOLERANCE_SECONDS + 1)
        r = SVC.resolve(_listing(D_LISTING, "GMT"), detail=_detail(d_over, "GMT"))
        assert r.status == CONFLICTING


class TestDocumentFallback:
    def test_document_deadline_used_when_listing_and_detail_empty(self):
        bundle_docs = [
            (1, "RFP.pdf",
             "Deadline for submission of applications: 24 September 2026 at 1.00 pm GMT.")
        ]
        r = SVC.resolve(_listing(), detail=None, bundle_docs=bundle_docs)
        assert r.status == RESOLVED
        assert r.source == SOURCE_DOCUMENT

    def test_document_skipped_when_no_deadline_language(self):
        bundle_docs = [(1, "annex.pdf", "This is a background document about health.")]
        r = SVC.resolve(_listing(), detail=None, bundle_docs=bundle_docs)
        assert r.status == UNRESOLVED

    def test_listing_wins_over_document(self):
        bundle_docs = [
            (1, "RFP.pdf", "Deadline for submission: 30 September 2026 at 5 pm GMT.")
        ]
        r = SVC.resolve(_listing(D_LISTING, "GMT"), bundle_docs=bundle_docs)
        assert r.status == RESOLVED
        assert r.source == SOURCE_LISTING

    def test_empty_bundle_docs(self):
        r = SVC.resolve(_listing(), detail=None, bundle_docs=[])
        assert r.status == UNRESOLVED

    def test_none_bundle_docs(self):
        r = SVC.resolve(_listing(), detail=None, bundle_docs=None)
        assert r.status == UNRESOLVED


class TestNoGuessing:
    def test_no_deadline_anywhere_is_unresolved_not_today(self):
        r = SVC.resolve(_listing(), detail=None)
        assert r.status == UNRESOLVED
        assert r.deadline_utc is None

    def test_unresolved_deadline_utc_is_none(self):
        r = SVC.resolve(_listing())
        assert r.deadline_utc is None

    def test_conflicting_deadline_utc_is_none(self):
        r = SVC.resolve(_listing(D_LISTING, "GMT"), detail=_detail(D_CONFLICT, "GMT"))
        assert r.deadline_utc is None

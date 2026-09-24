"""Unit tests for the deadline resolution value objects (Prompt 12.1 §16)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tender_intelligence.deadline.model import (
    CONFLICTING,
    REASON_CONFLICTING_SOURCES,
    REASON_MISSING_TIMEZONE,
    REASON_NOT_PRESENT_ON_LISTING,
    RESOLVED,
    SOURCE_CONFLICTING,
    SOURCE_DOCUMENT,
    SOURCE_LISTING,
    SOURCE_UNRESOLVED,
    UNRESOLVED,
    DeadlineResult,
)

D = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)


class TestDeadlineResultResolved:
    def test_basic_resolved(self):
        r = DeadlineResult.resolved(D, SOURCE_LISTING, deadline_timezone="GMT")
        assert r.status == RESOLVED
        assert r.is_resolved
        assert r.deadline_utc == D
        assert r.deadline_timezone == "GMT"
        assert r.source == SOURCE_LISTING

    def test_resolved_with_evidence(self):
        r = DeadlineResult.resolved(
            D, SOURCE_LISTING,
            original_text="End Date: 2026-09-24 13:00:00 UTC",
            source_url="https://data.wahooas.org/tenders/tenders/167/list",
        )
        assert r.evidence["original_text"] == "End Date: 2026-09-24 13:00:00 UTC"
        assert "source_url" in r.evidence

    def test_resolved_document_source(self):
        r = DeadlineResult.resolved(
            D, SOURCE_DOCUMENT,
            document_id=42, filename="RFP.pdf",
        )
        assert r.evidence["document_id"] == 42
        assert r.evidence["filename"] == "RFP.pdf"

    def test_evidence_text_capped(self):
        long_text = "x" * 500
        r = DeadlineResult.resolved(D, SOURCE_LISTING, original_text=long_text)
        assert len(r.evidence["original_text"]) == 200

    def test_resolved_requires_deadline_utc(self):
        with pytest.raises(ValueError, match="must carry a deadline_utc"):
            DeadlineResult(status=RESOLVED, source=SOURCE_LISTING,
                           deadline_utc=None, deadline_timezone=None)


class TestDeadlineResultUnresolved:
    def test_basic_unresolved(self):
        u = DeadlineResult.unresolved(REASON_NOT_PRESENT_ON_LISTING)
        assert u.status == UNRESOLVED
        assert not u.is_resolved
        assert u.deadline_utc is None
        assert u.source == SOURCE_UNRESOLVED
        assert u.evidence["reason_code"] == REASON_NOT_PRESENT_ON_LISTING

    def test_unresolved_must_not_carry_deadline(self):
        with pytest.raises(ValueError, match="must not carry a deadline_utc"):
            DeadlineResult(status=UNRESOLVED, source=SOURCE_UNRESOLVED,
                           deadline_utc=D, deadline_timezone=None)

    def test_unresolved_with_extra(self):
        u = DeadlineResult.unresolved(
            REASON_MISSING_TIMEZONE,
            extra={"original_text": "Deadline: 24 September", "source_url": "http://x"},
        )
        assert u.evidence["original_text"] == "Deadline: 24 September"


class TestDeadlineResultConflicting:
    def test_conflicting(self):
        c = DeadlineResult.conflicting(
            conflict_a={"source": "listing_page", "deadline_utc": "2026-09-24T13:00:00+00:00"},
            conflict_b={"source": "detail_page", "deadline_utc": "2026-09-30T17:00:00+00:00"},
        )
        assert c.status == CONFLICTING
        assert not c.is_resolved
        assert c.deadline_utc is None
        assert c.source == SOURCE_CONFLICTING
        assert c.evidence["reason_code"] == REASON_CONFLICTING_SOURCES
        assert "conflict_a" in c.evidence
        assert "conflict_b" in c.evidence

    def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="invalid status"):
            DeadlineResult(status="bogus", source="listing_page",
                           deadline_utc=None, deadline_timezone=None)

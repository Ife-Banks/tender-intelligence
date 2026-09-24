"""Integration tests for deadline resolution persistence and timeline (Prompt 12.1 §16)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.db.models.deadline import TenderDeadlineResolution
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories.tenders import TenderRepository
from tender_intelligence.deadline.model import (
    CONFLICTING,
    REASON_CONFLICTING_SOURCES,
    REASON_NOT_PRESENT_ON_LISTING,
    RESOLVED,
    SOURCE_DETAIL,
    SOURCE_LISTING,
    SOURCE_UNRESOLVED,
    UNRESOLVED,
    DeadlineResult,
)
from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.core.correlation import new_correlation_id
from tender_intelligence.interfaces.source import TenderListing

D = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)
D2 = datetime(2026, 10, 15, 10, 0, tzinfo=UTC)


@pytest.fixture()
def source(db_session: Session) -> Source:
    src = Source(name="waho-test", source_type="paginated_html_list",
                 base_url="https://data.wahooas.org", active=True)
    db_session.add(src)
    db_session.commit()
    return src


@pytest.fixture()
def tender(db_session: Session, source: Source) -> Tender:
    cid = new_correlation_id()
    t = Tender(
        source_id=source.id,
        external_id="T-167",
        url="https://data.wahooas.org/tenders/tenders/167/list",
        title="Test Tender",
        status="new",
        first_seen_at=datetime.now(UTC),
        correlation_id=cid,
    )
    db_session.add(t)
    db_session.commit()
    return t


class TestApplyDeadlineResolution:
    def test_resolved_creates_row(self, db_session: Session, tender: Tender):
        repo = TenderRepository(db_session)
        result = DeadlineResult.resolved(
            D, SOURCE_LISTING, deadline_timezone="GMT",
            original_text="End Date: 2026-09-24 13:00:00 UTC",
            source_url=tender.url,
        )
        resolved_at = datetime.now(UTC)
        repo.apply_deadline_resolution(tender, result, resolved_at)
        db_session.commit()

        row = db_session.scalar(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        )
        assert row is not None
        assert row.deadline_resolved is not None
        # SQLite strips tzinfo on retrieval; compare datetime values ignoring tz
        assert row.deadline_resolved.replace(tzinfo=None) == D.replace(tzinfo=None)
        assert row.deadline_source == SOURCE_LISTING
        assert row.deadline_evidence["original_text"] == "End Date: 2026-09-24 13:00:00 UTC"
        assert row.deadline_resolved_at is not None

    def test_unresolved_creates_row_with_null_deadline(
            self, db_session: Session, tender: Tender):
        repo = TenderRepository(db_session)
        result = DeadlineResult.unresolved(REASON_NOT_PRESENT_ON_LISTING)
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        row = db_session.scalar(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        )
        assert row is not None
        assert row.deadline_resolved is None
        assert row.deadline_source == SOURCE_UNRESOLVED

    def test_conflicting_creates_row_with_conflict_evidence(
            self, db_session: Session, tender: Tender):
        repo = TenderRepository(db_session)
        result = DeadlineResult.conflicting(
            conflict_a={"source": SOURCE_LISTING, "deadline_utc": D.isoformat()},
            conflict_b={"source": SOURCE_DETAIL, "deadline_utc": D2.isoformat()},
        )
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        row = db_session.scalar(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        )
        assert row is not None
        assert row.deadline_resolved is None
        assert row.deadline_source == "conflicting"
        assert row.deadline_evidence["reason_code"] == REASON_CONFLICTING_SOURCES

    def test_upsert_overwrites_previous_result(self, db_session: Session, tender: Tender):
        """Running resolution twice updates the existing row in place."""
        repo = TenderRepository(db_session)
        r1 = DeadlineResult.unresolved(REASON_NOT_PRESENT_ON_LISTING)
        repo.apply_deadline_resolution(tender, r1, datetime.now(UTC))
        db_session.commit()

        r2 = DeadlineResult.resolved(D, SOURCE_DETAIL, deadline_timezone="GMT")
        repo.apply_deadline_resolution(tender, r2, datetime.now(UTC))
        db_session.commit()

        rows = db_session.scalars(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        ).all()
        assert len(rows) == 1, "upsert must not create duplicate rows"
        assert rows[0].deadline_resolved.replace(tzinfo=None) == D.replace(tzinfo=None)
        assert rows[0].deadline_source == SOURCE_DETAIL

    def test_deadline_column_untouched(self, db_session: Session, tender: Tender):
        """apply_deadline_resolution must never write to Tender.deadline."""
        original_deadline = tender.deadline  # None
        repo = TenderRepository(db_session)
        result = DeadlineResult.resolved(D, SOURCE_DETAIL, deadline_timezone="GMT")
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()
        db_session.refresh(tender)
        assert tender.deadline == original_deadline, (
            "Tender.deadline (owned by Prompt 05) must not be modified by deadline resolution"
        )


class TestTimelineDeadlineEvent:
    def test_deadline_event_appears_in_timeline(
            self, db_session: Session, source: Source, tender: Tender):
        """Timeline shows deadline_resolution event when resolution row exists."""
        repo = TenderRepository(db_session)
        result = DeadlineResult.resolved(D, SOURCE_LISTING, deadline_timezone="GMT",
                                          original_text="End Date: 2026-09-24 13:00:00 UTC")
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        tl = TimelineService(db_session).reconstruct_by_correlation(tender.correlation_id)
        stages = [e.stage for e in tl.events]
        assert "deadline_resolution" in stages

        dr_event = next(e for e in tl.events if e.stage == "deadline_resolution")
        assert dr_event.status == SOURCE_LISTING
        assert dr_event.tender_id == tender.id
        assert dr_event.processing_info is not None
        assert dr_event.processing_info["deadline_source"] == SOURCE_LISTING
        assert dr_event.processing_info["deadline_utc"] is not None

    def test_no_deadline_event_when_no_resolution_row(
            self, db_session: Session, tender: Tender):
        """Timeline must not emit a deadline_resolution event if resolution never ran."""
        tl = TimelineService(db_session).reconstruct_by_correlation(tender.correlation_id)
        stages = [e.stage for e in tl.events]
        assert "deadline_resolution" not in stages

    def test_unresolved_event_present_in_timeline(
            self, db_session: Session, tender: Tender):
        repo = TenderRepository(db_session)
        result = DeadlineResult.unresolved(REASON_NOT_PRESENT_ON_LISTING)
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        tl = TimelineService(db_session).reconstruct_by_correlation(tender.correlation_id)
        dr_event = next((e for e in tl.events if e.stage == "deadline_resolution"), None)
        assert dr_event is not None
        assert dr_event.status == SOURCE_UNRESOLVED


class TestDeadlineChangedDedup:
    """Verify that DEADLINE_CHANGED fires correctly after resolution (dedup owns deadline)."""

    def test_deadline_changed_fires_when_listing_deadline_changes(
            self, db_session: Session, source: Source):
        from tender_intelligence.dedup.classify import compare, DEADLINE_CHANGED

        # First run: listing had deadline D
        cid = new_correlation_id()
        listing1 = TenderListing(
            external_id="CHG-1",
            title="Changing Tender",
            url="https://data.wahooas.org/tenders/tenders/168/list",
            deadline_at=D,
            deadline_timezone="GMT",
        )
        repo = TenderRepository(db_session)
        claim = repo.claim_new(source.id, listing1, cid)
        db_session.commit()

        # Second run: listing deadline changed to D2
        listing2 = TenderListing(
            external_id="CHG-1",
            title="Changing Tender",
            url="https://data.wahooas.org/tenders/tenders/168/list",
            deadline_at=D2,
            deadline_timezone="GMT",
        )
        result = compare(listing2, claim.tender)
        assert result.material_change is True
        assert DEADLINE_CHANGED in result.change_types

    def test_unchanged_when_deadline_same(
            self, db_session: Session, source: Source):
        from tender_intelligence.dedup.classify import compare, UNCHANGED

        cid = new_correlation_id()
        listing = TenderListing(
            external_id="SAME-1",
            title="Same Tender",
            url="https://data.wahooas.org/tenders/tenders/169/list",
            deadline_at=D,
            deadline_timezone="GMT",
        )
        repo = TenderRepository(db_session)
        claim = repo.claim_new(source.id, listing, cid)
        db_session.commit()

        result = compare(listing, claim.tender)
        assert result.classification == UNCHANGED


class TestSecurityConstraints:
    def test_evidence_does_not_contain_secrets(
            self, db_session: Session, tender: Tender):
        """Evidence blob must never carry credentials or raw provider payloads."""
        repo = TenderRepository(db_session)
        result = DeadlineResult.resolved(
            D, SOURCE_LISTING, deadline_timezone="GMT",
            original_text="End Date: 2026-09-24 13:00:00 UTC",
        )
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        row = db_session.scalar(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        )
        evidence_str = str(row.deadline_evidence or {})
        for secret_keyword in ("password", "api_key", "bearer", "secret", "credential"):
            assert secret_keyword not in evidence_str.lower(), (
                f"Evidence must not contain {secret_keyword!r}"
            )

    def test_evidence_text_length_bounded(
            self, db_session: Session, tender: Tender):
        """Evidence original_text must never exceed EVIDENCE_TEXT_LIMIT chars."""
        from tender_intelligence.deadline.model import EVIDENCE_TEXT_LIMIT

        repo = TenderRepository(db_session)
        long_text = "x " * 300  # 600 chars
        result = DeadlineResult.resolved(
            D, SOURCE_LISTING, original_text=long_text,
        )
        repo.apply_deadline_resolution(tender, result, datetime.now(UTC))
        db_session.commit()

        row = db_session.scalar(
            select(TenderDeadlineResolution)
            .where(TenderDeadlineResolution.tender_id == tender.id)
        )
        text = row.deadline_evidence.get("original_text", "")
        assert len(text) <= EVIDENCE_TEXT_LIMIT

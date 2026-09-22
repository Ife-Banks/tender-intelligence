"""Prompt 06 — final behavioral acceptance tests.

Independent, end-to-end verification of the persistence layer against a real migrated
schema. Sections 3 (uniqueness), 7 (concurrency) and 8 (status matrix) are covered by
``test_persistence.py``; this file focuses on the remaining behavioural contracts:
creation fields, identity-preserving updates, idempotency, the four-step Prompt 05+06
sequence, RunHistory failure persistence, transaction rollback/recovery, correlation
readiness, and the document foundation for prompts 07-09.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.correlation import new_correlation_id
from tender_intelligence.core.errors import DEDUP_TRANSACTION_FAILED
from tender_intelligence.db.models.documents import (
    DOWNLOAD_STATUSES,
    EXTRACTION_STATUSES,
)
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories import (
    DocumentRepository,
    RunHistoryRepository,
    TenderRepository,
)
from tender_intelligence.dedup import (
    COMPLETED,
    DEADLINE_CHANGED,
    ERRORED,
    DedupError,
    DedupService,
    derive_run_status,
)
from tender_intelligence.interfaces.source import TenderListing

BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _listing(**overrides) -> TenderListing:
    base = {
        "external_id": "TEST-001",
        "title": "Consultancy for health systems",
        "url": "https://data.wahooas.org/tenders/tenders/test-001/list",
        "published_at": BASE,
        "deadline_at": BASE + timedelta(days=14),
        "deadline_timezone": "GMT",
        "raw_metadata": {"reference": "P-Z1-BZ0-012/C"},
    }
    base.update(overrides)
    return TenderListing(**base)


@pytest.fixture()
def source_id(db_session: Session) -> int:
    src = Source(
        name="wahoo",
        source_type="wahoo",
        base_url="https://data.wahooas.org",
        active=True,
    )
    db_session.add(src)
    db_session.commit()
    return int(src.id)


def _history(session: Session, source_id: int) -> list[RunHistory]:
    return list(
        session.scalars(
            select(RunHistory).where(RunHistory.source_id == source_id).order_by(RunHistory.id)
        ).all()
    )


# 2. Tender creation -------------------------------------------------------------
def test_creation_persists_required_fields_and_no_raw_html(
    db_session: Session, source_id: int
) -> None:
    cid = new_correlation_id()
    claim = TenderRepository(db_session).claim_new(
        source_id, _listing(raw_metadata={"reference": "REF"}), cid
    )
    db_session.commit()
    assert claim.created is True

    fresh = TenderRepository(db_session).get_by_identity(source_id, "TEST-001")
    assert fresh is not None
    assert fresh.source_id == source_id
    assert fresh.external_id == "TEST-001"
    assert fresh.title == "Consultancy for health systems"
    assert fresh.url.endswith("/test-001/list")
    assert fresh.deadline == BASE + timedelta(days=14)
    assert fresh.deadline_timezone == "GMT"
    assert fresh.raw_metadata["reference"] == "REF"
    assert fresh.correlation_id == cid  # first-seen correlation is preserved
    assert fresh.first_seen_at is not None
    assert fresh.status == "new"
    assert not hasattr(fresh, "raw_html")  # no undeclared raw HTML column exists/persisted
    assert "raw_html" not in {c.name for c in Tender.__table__.columns}


# 4. Tender update keeps identity and first-seen correlation ----------------------
def test_update_changes_mutable_fields_but_keeps_identity(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    with session_factory_gr() as s:
        repo = TenderRepository(s)
        cid = new_correlation_id()
        tender = repo.claim_new(source_id, _listing(), cid).tender
        s.commit()
        first_seen = tender.first_seen_at

        repo.apply_update(
            tender,
            _listing(title="Consultancy for health systems — revised scope"),
        )
        s.commit()

        fresh = repo.get_by_identity(source_id, "TEST-001")
        assert fresh.title == "Consultancy for health systems — revised scope"
        assert fresh.source_id == source_id
        assert fresh.external_id == "TEST-001"
        assert fresh.correlation_id == cid  # original first-seen correlation intact
        assert fresh.first_seen_at == first_seen
        assert fresh.is_update is True
        assert fresh.status == "updated"
        assert len(repo.list_by_source(source_id)) == 1  # no duplicate Tender


# 5. Idempotent update -------------------------------------------------------------
def test_identical_update_twice_is_benign(session_factory_gr: sessionmaker, source_id: int) -> None:
    with session_factory_gr() as s:
        repo = TenderRepository(s)
        tender = repo.claim_new(source_id, _listing(), new_correlation_id()).tender
        s.commit()

        update = _listing(title="Same revised title", deadline_at=BASE + timedelta(days=21))
        repo.apply_update(tender, update)
        s.commit()
        meta_after_first = dict(tender.raw_metadata or {})
        repo.apply_update(tender, update)
        s.commit()

        fresh = repo.get_by_identity(source_id, "TEST-001")
        assert fresh.title == "Same revised title"
        assert fresh.deadline == BASE + timedelta(days=21)
        assert fresh.status == "updated"
        assert fresh.is_update is True
        assert len(repo.list_by_source(source_id)) == 1  # no duplicate state
        assert fresh.raw_metadata == meta_after_first  # raw_metadata merged exactly once
        # ``updated_at`` is a TimestampMixin convention: it reflects the latest write.
        # That is intended behaviour, not corruption (no spec contradicting it; not flagged).


# 6. Prompt 05 + Prompt 06 four-step integration -----------------------------------
def test_prompt05_plus_prompt06_four_step_sequence(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)

    # Run 1: NEW
    r1 = service.run(source_id, [_listing()])
    assert r1.new_count == 1
    with session_factory_gr() as s:
        t = TenderRepository(s).get_by_identity(source_id, "TEST-001")
        assert t is not None and t.status == "new" and t.is_update is False
        assert _history(s, source_id)[-1].new_count == 1

    # Run 2: UNCHANGED
    r2 = service.run(source_id, [_listing()])
    assert r2.unchanged_count == 1
    with session_factory_gr() as s:
        assert _history(s, source_id)[-1].unchanged_count == 1

    # Run 3: deadline changed -> UPDATE, material, DEADLINE_CHANGED
    changed = _listing(deadline_at=BASE + timedelta(days=30))
    r3 = service.run(source_id, [changed])
    assert r3.update_count == 1
    assert r3.updates[0].material_change is True
    assert DEADLINE_CHANGED in r3.updates[0].change_types
    with session_factory_gr() as s:
        t = TenderRepository(s).get_by_identity(source_id, "TEST-001")
        assert t.deadline.replace(tzinfo=UTC) == BASE + timedelta(days=30)
        assert t.status == "updated" and t.is_update is True
        assert _history(s, source_id)[-1].update_count == 1

    # Run 4: same changed listing -> UNCHANGED (no re-notification)
    r4 = service.run(source_id, [changed])
    assert r4.unchanged_count == 1
    assert r4.update_count == 0
    with session_factory_gr() as s:
        assert len(TenderRepository(s).list_by_source(source_id)) == 1  # still one row
        assert _history(s, source_id)[-1].unchanged_count == 1


# 9. RunHistory RUNNING -> ERRORED (simulated failure) ------------------------------
class _FailingService(DedupService):
    """Simulates a real mid-run failure after valid state was emitted."""

    def _dedup_transaction(self, session, source_id, listings, run_row):
        self._dedup_classify(session, source_id, listings, run_row)
        raise DedupError(
            "simulated stage failure",
            error_code=DEDUP_TRANSACTION_FAILED,
            context={"correlation_id": run_row.correlation_id},
        )


def test_runhistory_running_to_errored_then_completed(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    fail_cid = "run-failing-0001"
    with pytest.raises(DedupError):
        _FailingService(session_factory_gr).run(
            source_id, [_listing()], run_correlation_id=fail_cid
        )

    with session_factory_gr() as s:
        run = RunHistoryRepository(s).get_by_correlation(fail_cid)
        assert run is not None
        assert run.source_id == source_id
        assert run.started_at is not None
        assert run.ended_at is not None and run.ended_at >= run.started_at
        assert run.error_count == 1
        assert fail_cid in (run.failed_correlation_ids or [])
        assert derive_run_status(run) == ERRORED
        assert run.new_count == 0  # nothing committed before the failure

    # Persistence after failure: a healthy run still completes.
    ok = DedupService(session_factory_gr).run(source_id, [_listing()])
    assert ok.new_count == 1
    with session_factory_gr() as s:
        assert len(TenderRepository(s).list_by_source(source_id)) == 1
        runs = _history(s, source_id)
        assert len(runs) == 2
        assert derive_run_status(runs[0]) == ERRORED
        assert derive_run_status(runs[1]) == COMPLETED


# 10. Transaction rollback and recovery on the same connection ----------------------
def test_rollback_keeps_atomicity_and_connection_recovers(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    with session_factory_gr() as s:
        repo = TenderRepository(s)
        tender = repo.claim_new(source_id, _listing(), new_correlation_id()).tender
        # A valid second write (document) followed by a deliberate failure:
        DocumentRepository(s).create(tender.id, "https://data.wahooas.org/a.pdf", "a.pdf")
        s.flush()
        try:
            raise RuntimeError("simulated mid-transaction failure")
        except RuntimeError:
            s.rollback()

        # Atomicity: nothing from the failed transaction was committed.
        assert repo.get_by_identity(source_id, "TEST-001") is None
        assert len(repo.list_by_source(source_id)) == 0

    # Same session factory (same connection pool) performs a successful op afterward.
    with session_factory_gr() as s:
        repo = TenderRepository(s)
        ok = repo.claim_new(source_id, _listing(), new_correlation_id())
        s.commit()
        assert ok.created is True
        fresh = repo.get_by_identity(source_id, "TEST-001")
        assert fresh is not None and fresh.status == "new"


# 11. Correlation / timeline readiness ---------------------------------------------
def test_correlation_timeline_reconstructable(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    r = service.run(source_id, [_listing()], run_correlation_id="timeline-run-1")

    with session_factory_gr() as s:
        tender = TenderRepository(s).get_by_correlation(r.correlation_map["TEST-001"])
        assert tender is not None and tender.external_id == "TEST-001"
        assert tender.first_seen_at is not None

        run = RunHistoryRepository(s).get_by_correlation("timeline-run-1")
        assert run is not None and run.source_id == source_id

        # Lifecycle reconstruction uses only model columns (no invented audit fields):
        after = {c.name for c in Tender.__table__.columns}
        assert {"correlation_id", "first_seen_at", "created_at", "updated_at"} <= after
        assert run.started_at is not None


# 12. Document persistence foundation (prompts 07-09 seed) --------------------------
def test_minimal_document_row_is_queryable_and_updatable(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    with session_factory_gr() as s:
        tender = TenderRepository(s).claim_new(source_id, _listing(), new_correlation_id()).tender
        repo = DocumentRepository(s)
        doc = repo.create(
            tender.id, "https://data.wahooas.org/tenders/test-001/annex.pdf", "annex.pdf"
        )
        s.commit()

        assert doc.tender_id == tender.id
        assert doc.download_status in DOWNLOAD_STATUSES
        assert doc.extraction_status in EXTRACTION_STATUSES
        assert doc.download_status == "pending" and doc.extraction_status == "pending"
        assert doc.checksum is None  # prompt 08 fills this after download

        assert repo.list_by_tender(tender.id) == [doc]

        # Prompts 08/09 later mutate the seam without new schema:
        repo.set_download_status(doc.id, "downloaded")
        repo.set_extraction_status(doc.id, "extracted")
        s.commit()
        assert repo.get(doc.id).download_status == "downloaded"
        assert repo.get(doc.id).extraction_status == "extracted"

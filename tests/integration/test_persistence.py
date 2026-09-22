"""Integration tests: prompt-06 persistence/repository layer across all 13 categories.

Covers the tender seen-store (claim/identity/update), the status transition matrix,
document persistence seams (download/extraction/checksum), run-history lifecycle and
reconstruction, correlation-backed reads, error-code registration, concurrency safety
of the atomic claim, and the 0003 checksum-nullability migration on top of a 0002 baseline.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.correlation import new_correlation_id
from tender_intelligence.core.errors import (
    ERROR_CODES,
    PERSISTENCE_CONSTRAINT_VIOLATION,
    PERSISTENCE_INVALID_STATUS_TRANSITION,
    PERSISTENCE_NOT_FOUND,
    is_valid_error_code,
)
from tender_intelligence.db.engine import build_engine
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories import (
    DocumentRepository,
    PersistenceError,
    RunHistoryRepository,
    SourceRepository,
    TenderRepository,
    assert_valid_transition,
)
from tender_intelligence.dedup import (
    COMPLETED,
    ERRORED,
    RUNNING,
    DedupService,
    derive_run_status,
)
from tender_intelligence.interfaces.source import TenderListing

D = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)


def _migrations_dir() -> str:
    return str(Path(__file__).resolve().parent.parent.parent / "migrations")


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


def _listing(**overrides) -> TenderListing:
    base = {
        "external_id": "WAHO-1",
        "title": "Senior Health Officer",
        "url": "https://data.wahooas.org/tenders/tenders/1/list",
        "published_at": D,
        "deadline_at": D,
        "deadline_timezone": "UTC",
        "raw_metadata": {"reference": "REF-1"},
    }
    base.update(overrides)
    return TenderListing(**base)


def _tender_rows(session: Session, source_id: int) -> list[Tender]:
    return list(session.scalars(select(Tender).where(Tender.source_id == source_id)))


# 1. Error-code taxonomy ---------------------------------------------------------------
def test_persistence_error_codes_are_registered(db_session: Session) -> None:
    for code in (
        "persistence_not_found",
        "persistence_constraint_violation",
        "persistence_invalid_status_transition",
        "persistence_transaction_failed",
    ):
        assert code in ERROR_CODES
        assert is_valid_error_code(code)


# 2. Tender seen-store: insert + identity lookup -----------------------------------------
def test_tender_insert_and_identity_lookup(db_session: Session, source_id: int) -> None:
    repo = TenderRepository(db_session)
    claim = repo.claim_new(source_id, _listing(), new_correlation_id())
    assert claim.created is True
    assert claim.tender.status == "new"
    assert claim.tender.is_update is False

    got = repo.get_by_identity(source_id, "WAHO-1")
    assert got is not None and got.id == claim.tender.id
    many = repo.get_many_by_identity(source_id, ["WAHO-1", "MISSING-9"])
    assert set(many) == {"WAHO-1"}
    assert repo.exists(source_id, "WAHO-1") is True
    assert repo.exists(source_id, "MISSING-9") is False
    assert repo.get_by_correlation(claim.tender.correlation_id) is not None


# 3. Atomic claim: unique-constraint race resolution -------------------------------------
def test_claim_new_resolves_race_when_winner_committed_first(
    db_session: Session, source_id: int
) -> None:
    repo = TenderRepository(db_session)
    winner = repo.claim_new(source_id, _listing(), "winner-cid")
    db_session.commit()

    raced = repo.claim_new(source_id, _listing(), "racer-cid")
    assert raced.created is False
    assert raced.tender.id == winner.tender.id
    assert len(_tender_rows(db_session, source_id)) == 1


def test_claim_no_winner_race_surfaces_constraint_error(
    db_session: Session, source_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = TenderRepository(db_session)
    repo.claim_new(source_id, _listing(), "winner-cid")
    db_session.commit()

    monkeypatch.setattr(TenderRepository, "get_by_identity", lambda self, sid, ext: None)
    with pytest.raises(PersistenceError) as exc:
        repo.claim_new(source_id, _listing(), "racer-cid")
    assert exc.value.error_code == PERSISTENCE_CONSTRAINT_VIOLATION


# 4. Update persistence: mutable fields + is_update + status ------------------------------
def test_apply_update_persists_changes_and_transitions_status(
    db_session: Session, source_id: int
) -> None:
    repo = TenderRepository(db_session)
    claim = repo.claim_new(source_id, _listing(), new_correlation_id())
    db_session.commit()

    repo.apply_update(
        claim.tender,
        _listing(
            title="Senior Health Officer (revised)",
            url="https://data.wahooas.org/tenders/tenders/1/list/v2",
            raw_metadata={"reference": "REF-1", "addendum_marker": "addendum-a.pdf"},
        ),
    )
    db_session.commit()

    row = repo.get_by_identity(source_id, "WAHO-1")
    assert row is not None
    assert row.title == "Senior Health Officer (revised)"
    assert row.url.endswith("/v2")
    assert row.is_update is True
    assert row.status == "updated"
    assert row.raw_metadata["addendum_marker"] == "addendum-a.pdf"


# 5. Status transition matrix: documented moves ------------------------------------------
def test_status_transition_matrix(session_factory_gr: sessionmaker, source_id: int) -> None:
    valid_moves = {
        "new": ["new", "updated", "processed", "verdict_failed", "awaiting_budget"],
        "updated": ["updated", "processed", "verdict_failed", "awaiting_budget"],
        "processed": ["processed", "updated"],
        "awaiting_budget": ["awaiting_budget", "updated", "processed"],
        "verdict_failed": ["verdict_failed"],
    }
    with session_factory_gr() as session:
        repo = TenderRepository(session)
        for current, targets in valid_moves.items():
            tender = repo.claim_new(
                source_id,
                _listing(external_id=f"S-{current}"),
                new_correlation_id(),
            ).tender
            for target in targets:
                tender.status = current  # arrange, bypassing the guard
                repo.set_status(tender, target)
                assert tender.status == target


def test_documented_pipeline_flow_is_legal(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    with session_factory_gr() as session:
        repo = TenderRepository(session)
        t = repo.claim_new(source_id, _listing(external_id="FLOW-1"), new_correlation_id()).tender
        repo.set_status(t, "awaiting_budget")  # Stage B budget exhausted (docs/04 §4.13)
        repo.set_status(t, "processed")  # budget restored, run completes
        assert t.status == "processed"
        repo.apply_update(t, _listing(external_id="FLOW-1", title="FLOW-1 addendum"))
        assert t.status == "updated"  # addendum to processed tender is a distinct event (§4.14)


# 6. Status transition matrix: illegal moves rejected ------------------------------------
def test_illegal_status_transitions_rejected(db_session: Session, source_id: int) -> None:
    repo = TenderRepository(db_session)
    t = repo.claim_new(source_id, _listing(external_id="ILL-1"), new_correlation_id()).tender
    with pytest.raises(PersistenceError) as exc:
        repo.set_status(t, "bogus_status")
    assert exc.value.error_code == PERSISTENCE_INVALID_STATUS_TRANSITION

    assert_valid_transition(t, "new")  # new -> new is legal
    repo.set_status(t, "verdict_failed")
    with pytest.raises(PersistenceError) as exc:
        repo.set_status(t, "processed")  # verdict_failed is terminal (no documented recovery)
    assert exc.value.error_code == PERSISTENCE_INVALID_STATUS_TRANSITION
    assert t.status == "verdict_failed"  # guard rejects before the field is ever written
    with pytest.raises(PersistenceError):
        assert_valid_transition(t, "new")


# 7. Document creation: pending defaults, NULL checksum, list-by-tender ------------------
def test_document_creation_pending_defaults(db_session: Session, source_id: int) -> None:
    tender = (
        TenderRepository(db_session).claim_new(source_id, _listing(), new_correlation_id()).tender
    )
    db_session.commit()

    docs = DocumentRepository(db_session)
    docs.create(
        tender.id,
        "https://data.wahooas.org/a.pdf",
        "a.pdf",
        mime_type="application/pdf",
    )
    docs.create(tender.id, "https://data.wahooas.org/b.zip", "b.zip")
    db_session.commit()
    rows = docs.list_by_tender(tender.id)
    assert {r.filename for r in rows} == {"a.pdf", "b.zip"}
    assert all(r.download_status == "pending" for r in rows)  # prompt 08 owns downloads
    assert all(r.extraction_status == "pending" for r in rows)
    assert all(r.checksum is None for r in rows)  # prompt 08 computes checksums later


# 8. Document status validation -----------------------------------------------------------
def test_document_statuses_are_validated_and_persisted(db_session: Session, source_id: int) -> None:
    tender = (
        TenderRepository(db_session).claim_new(source_id, _listing(), new_correlation_id()).tender
    )
    doc = DocumentRepository(db_session).create(
        tender.id, "https://data.wahooas.org/a.pdf", "a.pdf"
    )
    db_session.commit()

    repo = DocumentRepository(db_session)

    with pytest.raises(PersistenceError) as exc:
        repo.set_download_status(doc.id, "stuck")
    assert exc.value.error_code == PERSISTENCE_INVALID_STATUS_TRANSITION

    with pytest.raises(PersistenceError) as exc:
        repo.set_extraction_status(doc.id, "done")
    assert exc.value.error_code == PERSISTENCE_INVALID_STATUS_TRANSITION

    repo.set_download_status(doc.id, "downloaded")
    repo.set_extraction_status(doc.id, "failed", error_code="ocr_failed")
    db_session.commit()

    fresh = repo.get(doc.id)
    assert fresh.download_status == "downloaded"
    assert fresh.extraction_status == "failed"
    assert fresh.extraction_error_code == "ocr_failed"


def test_document_ops_on_missing_row_raise_not_found(db_session: Session) -> None:
    repo = DocumentRepository(db_session)
    with pytest.raises(PersistenceError) as exc:
        repo.set_download_status(999_999_999, "downloaded")
    assert exc.value.error_code == PERSISTENCE_NOT_FOUND


# 9. Document seam setters for prompts 08/09 ----------------------------------------------
def test_document_seam_setters(db_session: Session, source_id: int) -> None:
    tender = (
        TenderRepository(db_session).claim_new(source_id, _listing(), new_correlation_id()).tender
    )
    doc = DocumentRepository(db_session).create(
        tender.id, "https://data.wahooas.org/a.pdf", "a.pdf"
    )
    db_session.commit()

    repo = DocumentRepository(db_session)
    repo.set_checksum(doc.id, "sha256:abcdef" * 2)
    repo.set_storage_path(doc.id, "s3://bucket/a.pdf")
    repo.set_extracted_text_ref(doc.id, "ref://text/a.pdf.txt")
    db_session.commit()

    fresh = repo.get(doc.id)
    assert fresh.checksum == "sha256:abcdef" * 2
    assert fresh.storage_path == "s3://bucket/a.pdf"
    assert fresh.extracted_text_ref == "ref://text/a.pdf.txt"


# 10. RunHistory lifecycle: RUNNING -> COMPLETED ------------------------------------------
def test_run_history_lifecycle(session_factory_gr: sessionmaker, source_id: int) -> None:
    with session_factory_gr() as s:
        run = RunHistoryRepository(s).create(source_id, "run-cid-1")
        assert run.ended_at is None
        assert derive_run_status(run) == RUNNING
        RunHistoryRepository(s).complete(
            run, listings_found=3, new_count=2, update_count=1, unchanged_count=0
        )
        s.commit()
    assert derive_run_status(run) == COMPLETED

    with session_factory_gr() as s:
        got = RunHistoryRepository(s).get(run.id)
        assert got is not None
        assert got.listings_found == 3
        assert got.new_count == 2


def test_run_history_errored_state_records_correlation(db_session: Session, source_id: int) -> None:
    repo = RunHistoryRepository(db_session)
    run = repo.create(source_id, "run-cid-fail")
    db_session.commit()
    repo.mark_errored(run, "run-cid-fail")
    db_session.commit()

    assert run.ended_at is not None
    assert run.error_count == 1
    assert "run-cid-fail" in (run.failed_correlation_ids or [])
    assert derive_run_status(run) == ERRORED


# 11. RunHistory idempotency (no double-stamp on retry) -----------------------------------
def test_run_history_complete_and_errored_are_idempotent(
    db_session: Session, source_id: int
) -> None:
    repo = RunHistoryRepository(db_session)

    run = repo.create(source_id, "run-ok")
    db_session.commit()
    repo.complete(run, listings_found=5, new_count=5, update_count=0, unchanged_count=0)
    repo.complete(run, listings_found=99, new_count=99, update_count=99, unchanged_count=99)
    db_session.commit()
    assert run.listings_found == 5
    assert run.new_count == 5
    assert run.ended_at is not None

    failed = repo.create(source_id, "run-fail")
    db_session.commit()
    repo.mark_errored(failed, "run-fail")
    repo.mark_errored(failed, "run-fail")
    db_session.commit()
    assert failed.error_count == 1
    assert failed.failed_correlation_ids == ["run-fail"]
    assert failed.ended_at is not None


# 12. Correlation/source-backed reads -----------------------------------------------------
def test_run_history_reads_and_source_repo(db_session: Session, source_id: int) -> None:
    runs = RunHistoryRepository(db_session)
    for cid in ("a", "b", "c"):
        runs.create(source_id, f"run-{cid}")
    db_session.commit()

    assert {r.correlation_id for r in runs.list_by_source(source_id)} >= {"run-a", "run-b", "run-c"}
    assert runs.get_by_correlation("run-b").correlation_id == "run-b"

    src_repo = SourceRepository(db_session)
    assert src_repo.exists(source_id) is True
    assert src_repo.exists(999_999) is False
    assert src_repo.get(source_id) is not None
    assert {s.source_type for s in src_repo.list_all()} == {"wahoo"}


def test_dedup_run_counts_land_in_run_history_via_repo(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(source_id, [_listing(external_id="A-1")])
    service.run(
        source_id,
        [
            _listing(external_id="A-1", title="A-1 revised"),
            _listing(external_id="B-1"),
        ],
    )

    with session_factory_gr() as s:
        runs = RunHistoryRepository(s).list_by_source(source_id)
        assert [(r.new_count, r.update_count, r.unchanged_count) for r in runs] == [
            (1, 0, 0),
            (1, 1, 0),
        ]
        assert runs[-1].listings_found == 2


# 13. Concurrency: two threads claim the same tender row ----------------------------------
def test_concurrent_claims_leave_single_winner(tmp_path: Path, source_id: int) -> None:
    db_path = tmp_path / "claims.db"
    engine = build_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _rec) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        session.add(
            Source(
                name="wahoo",
                source_type="wahoo",
                base_url="https://data.wahooas.org",
                active=True,
            )
        )
        session.commit()
        seeded = int(session.scalars(select(Source.id).where(Source.name == "wahoo")).one())

    listing = _listing()
    barrier = threading.Barrier(2)
    created_flags: list[bool] = []
    errors: list[str] = []

    def _runner() -> None:
        barrier.wait()
        try:
            with factory() as session:
                claim = TenderRepository(session).claim_new(seeded, listing, new_correlation_id())
                created_flags.append(claim.created)
                session.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=_runner) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert sum(created_flags) == 1
    with factory() as session:
        rows = _tender_rows(session, seeded)
        assert len(rows) == 1
        assert rows[0].correlation_id  # a real, winning correlation_id was persisted


# Migration 0003: checksum becomes nullable on top of a 0002 baseline ----------------------
def test_migration_0003_checksum_nullable_over_baseline(tmp_path: Path) -> None:
    db_path = tmp_path / "baseline.db"
    engine = build_engine(f"sqlite:///{db_path}")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0002_run_history_counts")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        source = Source(
            name="wahoo",
            source_type="wahoo",
            base_url="https://data.wahooas.org",
            active=True,
        )
        tender = Tender(
            source=source,
            external_id="WAHO-MIG",
            url="https://data.wahooas.org/tenders/tenders/1/list",
            title="Migrated tender",
            status="new",
            first_seen_at=datetime.now(UTC),
            correlation_id="mig-cid",
        )
        session.add(tender)
        session.flush()
        session.add(
            Document(
                tender_id=tender.id,
                filename="a.pdf",
                source_url="https://data.wahooas.org/a.pdf",
                checksum="sha256:legacy",  # NOT NULL at 0002
            )
        )
        session.commit()

    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")

    with engine.begin() as conn:
        nullable = conn.execute(text("pragma table_info(documents)")).all()
        checksum_col = [c for c in nullable if c[1] == "checksum"][0]
        assert checksum_col[3] == 0  # notnull flag 0 -> nullable after 0003
        before = conn.execute(
            text("select checksum from documents where filename='a.pdf'")
        ).scalar()
        assert before == "sha256:legacy"

    with factory() as session:
        tender_id = int(
            session.scalars(select(Tender.id).where(Tender.external_id == "WAHO-MIG")).one()
        )
        pending = DocumentRepository(session).create(
            tender_id, "https://data.wahooas.org/b.pdf", "b.pdf"
        )
        session.commit()
        assert pending.checksum is None
        engine.dispose()

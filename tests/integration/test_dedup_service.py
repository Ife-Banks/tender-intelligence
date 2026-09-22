"""Integration tests for dedup service against a real database (prompt 05 §5.2)."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, select
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.errors import (
    DEDUP_INVALID_STATE,
    DEDUP_MALFORMED_CANDIDATE,
    DEDUP_MISSING_IDENTITY,
)
from tender_intelligence.db.engine import build_engine
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.dedup import (
    COMPLETED,
    DEADLINE_CHANGED,
    ERRORED,
    RUNNING,
    TITLE_CHANGED,
    DedupError,
    DedupResult,
    DedupService,
    derive_run_status,
)
from tender_intelligence.interfaces.source import TenderListing

D = datetime(2026, 9, 24, 13, 0, tzinfo=UTC)


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


def _history_rows(db_session: Session, source_id: int) -> list[RunHistory]:
    return list(db_session.scalars(select(RunHistory).where(RunHistory.source_id == source_id)))


def _tender_rows(db_session: Session, source_id: int) -> list[Tender]:
    return list(db_session.scalars(select(Tender).where(Tender.source_id == source_id)))


def test_first_run_inserts_new_tender(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    listing = _listing()
    result = service.run(source_id, [listing])

    assert result.run_status == COMPLETED
    assert result.new_count == 1
    assert result.unchanged_count == 0
    assert result.update_count == 0
    assert result.duplicate_count == 0
    assert set(result.correlation_map) == {"WAHO-1"}
    assert result.run_correlation_id

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert len(rows) == 1
        assert rows[0].external_id == "WAHO-1"
        assert rows[0].status == "new"
        assert rows[0].is_update is False
        assert rows[0].correlation_id == result.correlation_map["WAHO-1"]
        assert rows[0].first_seen_at is not None

        runs = _history_rows(session, source_id)
        assert len(runs) == 1
        assert runs[0].listings_found == 1
        assert runs[0].new_count == 1
        assert runs[0].update_count == 0
        assert runs[0].unchanged_count == 0
        assert runs[0].ended_at is not None
        assert derive_run_status(runs[0]) == COMPLETED


def test_identical_second_run_is_unchanged_and_no_duplicate(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    listing = _listing()

    first = service.run(source_id, [listing])
    assert first.new_count == 1

    second = service.run(source_id, [listing])
    assert second.run_status == COMPLETED
    assert second.new_count == 0
    assert second.update_count == 0
    assert second.unchanged_count == 1
    assert second.duplicate_count == 0

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert len(rows) == 1  # no duplicate seen-state
        runs = _history_rows(session, source_id)
        assert len(runs) == 2
        assert runs[1].new_count == 0
        assert runs[1].unchanged_count == 1


def test_deadline_change_is_update(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(source_id, [_listing()])

    changed = _listing(deadline_at=D.replace(day=30))
    result = service.run(source_id, [changed])

    assert result.new_count == 0
    assert result.update_count == 1
    versions = [o.change_types for o in result.updates]
    assert versions == [(DEADLINE_CHANGED,)]
    update = result.updates[0]
    assert update.material_change is True
    assert update.change_details["deadline"]["before"] is not None
    assert update.change_details["deadline"]["after"] == D.replace(day=30)

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert len(rows) == 1
        assert rows[0].deadline.replace(tzinfo=UTC) == D.replace(day=30)
        assert rows[0].status == "updated"
        assert rows[0].is_update is True
        assert rows[0].correlation_id == update.correlation_id


def test_title_and_reference_change(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(source_id, [_listing()])

    result = service.run(
        source_id,
        [_listing(title="Procurement Analyst", raw_metadata={"reference": "REF-2"})],
    )
    assert result.update_count == 1
    update = result.updates[0]
    assert update.material_change is True
    assert TITLE_CHANGED in update.change_types
    assert update.change_details["title"]["before"] == "Senior Health Officer"

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert rows[0].title == "Procurement Analyst"
        assert rows[0].raw_metadata["reference"] == "REF-2"


def test_addendum_marker_change_is_material(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(source_id, [_listing(raw_metadata={"reference": "REF-1"})])

    result = service.run(
        source_id,
        [_listing(raw_metadata={"reference": "REF-1", "addendum_marker": "addendum-a.pdf"})],
    )
    assert result.new_count == 0
    assert result.update_count == 1
    assert result.updates[0].material_change is True

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert rows[0].raw_metadata["addendum_marker"] == "addendum-a.pdf"


def test_two_sources_with_same_external_id_are_independent(
    session_factory_gr: sessionmaker, db_session: Session, source_id: int
) -> None:
    other = Source(name="other", source_type="other", base_url="https://example.org", active=True)
    db_session.add(other)
    db_session.commit()
    other_id = int(other.id)

    service = DedupService(session_factory_gr)
    listing = _listing()
    r1 = service.run(source_id, [listing])
    r2 = service.run(other_id, [listing])

    assert r1.new_count == 1
    assert r2.new_count == 1
    with session_factory_gr() as session:
        rows = list(session.scalars(select(Tender).where(Tender.external_id == "WAHO-1")))
        assert len(rows) == 2
        assert {row.source_id for row in rows} == {source_id, other_id}


def test_duplicate_candidate_in_one_run_logs_and_counts(
    session_factory_gr: sessionmaker, source_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    service = DedupService(session_factory_gr)
    listing = _listing()
    result = service.run(source_id, [listing, listing])

    assert result.new_count == 1
    assert result.duplicate_count == 1
    assert result.unchanged_count == 0
    assert len(result.correlation_map) == 1

    with session_factory_gr() as session:
        assert len(_tender_rows(session, source_id)) == 1
    assert any(rec.status == "duplicate" for rec in caplog.records if hasattr(rec, "status"))


def test_crash_mid_transaction_leaves_no_seen_state_and_recovers(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    """Simulate process death between new-insert and the single commit; next run recovers."""

    class Killed(BaseException):  # noqa: N818 - simulates SIGKILL, bypasses except Exception
        pass

    class KillService(DedupService):
        def _dedup_transaction(self, session, source_id, listings, run_row) -> DedupResult:
            # Real classify (INSERTs inside savepoints) but the process dies before commit.
            self._dedup_classify(session, source_id, listings, run_row)
            raise Killed()

    with pytest.raises(Killed):
        KillService(session_factory_gr).run(source_id, [_listing()])

    with session_factory_gr() as session:
        assert len(_tender_rows(session, source_id)) == 0  # rollback discarded partial state
        runs = _history_rows(session, source_id)
        assert len(runs) == 1
        assert runs[0].ended_at is None  # RUNNING, reconstructable (docs/04 §4.8)

    # Next scheduled run classifies purely as NEW again, exactly once.
    result = DedupService(session_factory_gr).run(source_id, [_listing()])
    assert result.new_count == 1
    with session_factory_gr() as session:
        assert len(_tender_rows(session, source_id)) == 1
        runs = _history_rows(session, source_id)
        assert len(runs) == 2
        assert runs[-1].ended_at is not None


def test_concurrent_runs_do_not_double_create_seen_state(
    tmp_path: Path, source_id: int
) -> None:
    db_path = tmp_path / "concurrent.db"
    engine = build_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _wal(dbapi_conn, _rec) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
    cfg = Config()
    cfg.set_main_option("script_location", str(_migrations_dir()))
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
        seeded_source_id = int(
            session.scalars(select(Source.id).where(Source.name == "wahoo")).one()
        )

    service = DedupService(factory)
    listing = _listing()
    barrier = threading.Barrier(2)

    def _runner(results: list[str]) -> None:
        barrier.wait()
        try:
            results.append(service.run(seeded_source_id, [listing]).run_status)
        except Exception as exc:  # noqa: BLE001
            results.append(f"error:{type(exc).__name__}:{exc}")

    results_a: list[str] = []
    results_b: list[str] = []
    t1 = threading.Thread(target=_runner, args=(results_a,))
    t2 = threading.Thread(target=_runner, args=(results_b,))
    t1.start()
    t2.start()
    t1.join(timeout=60)
    t2.join(timeout=60)

    with factory() as session:
        rows = _tender_rows(session, seeded_source_id)
        assert len(rows) == 1  # the unique race never double-creates
    statuses = results_a + results_b
    assert statuses.count("COMPLETED") >= 1
    assert not any(s.startswith("error:") for s in statuses)


def test_run_history_correlation_and_failure_traceability(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)

    class Boom(DedupService):
        def _dedup_transaction(self, session, source_id, listings, run_row) -> DedupResult:
            raise DedupError("boom", error_code=DEDUP_MISSING_IDENTITY, context={"k": "v"})

    bomby = Boom(session_factory_gr)
    with pytest.raises(DedupError):
        bomby.run(source_id, [_listing()])

    with session_factory_gr() as session:
        runs = _history_rows(session, source_id)
        assert len(runs) == 1
        failed_run = runs[0]
        assert failed_run.ended_at is not None
        assert failed_run.error_count == 1
        assert failed_run.correlation_id in (failed_run.failed_correlation_ids or [])
        assert failed_run.failed_correlation_ids
        assert failed_run.correlation_id  # run cid is stamped on the row
        assert derive_run_status(failed_run) == ERRORED
        assert derive_run_status(RunHistory(ended_at=None)) == RUNNING

    # Retry after failure is clean.
    ok = service.run(source_id, [_listing()])
    assert ok.new_count == 1
    assert ok.run_status == COMPLETED
    with session_factory_gr() as session:
        runs = _history_rows(session, source_id)
        assert len(runs) == 2
        assert derive_run_status(runs[-1]) == COMPLETED
        assert len(_tender_rows(session, source_id)) == 1


def test_missing_identity_and_malformed_abort_run(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)

    with pytest.raises(DedupError) as exc:
        service.run(source_id, [_listing(external_id="  ")])
    assert exc.value.error_code == DEDUP_MISSING_IDENTITY

    with pytest.raises(DedupError) as exc:
        service.run(source_id, [_listing(title="   ")])
    assert exc.value.error_code == DEDUP_MALFORMED_CANDIDATE

    with pytest.raises(DedupError) as exc:
        service.run(source_id, [_listing(url=" ")])
    assert exc.value.error_code == DEDUP_MALFORMED_CANDIDATE

    with session_factory_gr() as session:
        assert len(_tender_rows(session, source_id)) == 0


def test_unknown_source_aborts_with_invalid_state(
    session_factory_gr: sessionmaker,
) -> None:
    service = DedupService(session_factory_gr)
    with pytest.raises(DedupError) as exc:
        service.run(999_999, [_listing()])
    assert exc.value.error_code == DEDUP_INVALID_STATE


def test_multi_listing_run_mixed_classifications(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(
        source_id,
        [
            _listing(external_id="A-1", title="Alpha"),
            _listing(external_id="B-1", title="Beta"),
        ],
    )
    result = service.run(
        source_id,
        [
            _listing(external_id="A-1", title="Alpha"),
            _listing(external_id="B-1", title="Beta 2"),
            _listing(external_id="C-1", title="Gamma"),
        ],
    )
    assert result.new_count == 1
    assert result.update_count == 1
    assert result.unchanged_count == 1
    assert result.new_listings[0].external_id == "C-1"
    assert result.updates[0].listing.external_id == "B-1"


def test_outcomes_carry_description_and_raw_metadata_is_merged(
    session_factory_gr: sessionmaker, source_id: int
) -> None:
    service = DedupService(session_factory_gr)
    service.run(source_id, [_listing()])
    result = service.run(source_id, [_listing(raw_metadata={"reference": "REF-1"})])
    assert result.unchanged_count == 1

    with session_factory_gr() as session:
        rows = _tender_rows(session, source_id)
        assert rows[0].raw_metadata["reference"] == "REF-1"

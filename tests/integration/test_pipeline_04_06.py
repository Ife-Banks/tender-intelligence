"""End-to-end integration: prompt 04 (discovery) -> prompt 05 (dedup) -> prompt 06 (persistence).

Runs the real chain together against offline WAHO fixtures and a migrated SQLite schema:
``WahoPaginatedAdapter.list_new_tenders`` feeds ``DedupService.run`` onto the repository layer,
and the persisted state is read back through the prompt-06 repositories. No network, no mocks
beyond the adapter's fetcher seam (the same seam the prompt-04 unit suite uses).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.correlation import correlation_context, new_correlation_id
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.repositories import (
    DocumentRepository,
    RunHistoryRepository,
    TenderRepository,
)
from tender_intelligence.dedup import COMPLETED, DedupService, derive_run_status
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse
from tender_intelligence.sources.waho import WahoPaginatedAdapter

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "sources" / "waho"
LISTING_URL = "https://data.wahooas.org/tenders/tenders/list"
TITLE_167 = "RECRUTEMENT D'UN CABINET DE CONSEIL | RFP OF RECRUTEMENT OF CONSULTING FIRM"


def _fixture(name: str) -> str:
    with open(os.fspath(FIXTURES / name), encoding="utf-8") as fh:
        return fh.read()


def _fetcher(page1_html: str | None = None) -> Callable[[str], HttpResponse]:
    """Fetcher serving the real listing fixtures, keyed by listing URL."""
    pages = {
        LISTING_URL: page1_html or _fixture("listing_page_1.html"),
        LISTING_URL + "?page=2": _fixture("listing_page_2.html"),
    }

    def fetch(url: str) -> HttpResponse:
        return HttpResponse(200, {"content-type": "text/html; charset=utf-8"}, pages[url], url)

    return fetch


def _adapter(page1_html: str | None = None) -> WahoPaginatedAdapter:
    return WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=_fetcher(page1_html),
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )


def _seed_source(db_session: Session) -> int:
    src = Source(
        name="wahoo",
        source_type="wahoo",
        base_url="https://data.wahooas.org",
        active=True,
    )
    db_session.add(src)
    db_session.commit()
    return int(src.id)


def _history_rows(session: Session, source_id: int) -> list[RunHistory]:
    return list(
        session.scalars(
            select(RunHistory).where(RunHistory.source_id == source_id).order_by(RunHistory.id)
        ).all()
    )


def test_discovery_dedup_persistence_chain(
    session_factory_gr: sessionmaker, db_session: Session
) -> None:
    source_id = _seed_source(db_session)
    adapter = _adapter()
    service = DedupService(session_factory_gr)

    with correlation_context(new_correlation_id()):
        listings = adapter.list_new_tenders()  # prompt 04: real discovery, offline
        first = service.run(source_id, listings)  # prompt 05
        second = service.run(source_id, listings)  # idempotent re-run

    assert [c.external_id for c in listings] == ["167", "166", "165"]
    assert first.new_count == 3
    assert first.update_count == 0
    assert second.new_count == 0
    assert second.unchanged_count == 3

    with session_factory_gr() as session:
        repo = TenderRepository(session)
        tenders = repo.list_by_source(source_id)
        assert len(tenders) == 3
        assert {t.external_id for t in tenders} == {"167", "166", "165"}
        assert all(t.status == "new" for t in tenders)
        assert len({t.correlation_id for t in tenders}) == 3  # per-tender correlation ids

        doc = DocumentRepository(session).create(
            tenders[0].id, "https://data.wahooas.org/tenders/tenders/167/a.pdf", "a.pdf"
        )
        session.commit()
        assert doc.checksum is None  # prompt 08 computes checksums, not prompt 06
        assert doc.download_status == "pending"

        runs = _history_rows(session, source_id)
        assert len(runs) == 2
        assert [(r.new_count, r.update_count, r.unchanged_count) for r in runs] == [
            (3, 0, 0),
            (0, 0, 3),
        ]
        assert all(run.correlation_id for run in runs)
        assert all(derive_run_status(run) == COMPLETED for run in runs)
        assert {  # prompt-05 run correlation ids are distinct from tender correlation ids
            r.correlation_id for r in runs
        }.isdisjoint({t.correlation_id for t in tenders})


def test_discovery_material_change_updates_persisted_tender(
    session_factory_gr: sessionmaker, db_session: Session
) -> None:
    source_id = _seed_source(db_session)
    service = DedupService(session_factory_gr)

    service.run(source_id, _adapter().list_new_tenders())

    revised_page1 = _fixture("listing_page_1.html").replace(
        TITLE_167, "CABINET DE CONSEIL — JUNIOR AND SENIOR POSITIONS"
    )
    updated_listings = _adapter(revised_page1).list_new_tenders()
    assert "167" in {c.external_id for c in updated_listings}

    outcome = service.run(source_id, updated_listings)
    assert outcome.new_count == 0
    assert outcome.update_count == 1
    assert {u.listing.external_id for u in outcome.updates} == {"167"}

    with session_factory_gr() as session:
        tender = TenderRepository(session).get_by_identity(source_id, "167")
        assert tender is not None
        assert tender.is_update is True
        assert tender.status == "updated"
        assert tender.title == "CABINET DE CONSEIL — JUNIOR AND SENIOR POSITIONS"
        runs = RunHistoryRepository(session).list_by_source(source_id)
        assert runs[-1].update_count == 1
        assert runs[-1].unchanged_count == 2

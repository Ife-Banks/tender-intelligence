"""Prompt 10 §19 A – L — the integrated 04 → 09 path, end to end and offline.

This is the file that satisfies prompt 10 §18's "at least one test must exercise the actual
integrated 04→09 path". Nothing here is stubbed except the socket: real WAHO HTML fixtures go
in, real ``Tender``/``Document``/``RunHistory`` rows and real extraction artifacts come out,
and every assertion reads the database or the object store rather than the returned report.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
from sqlalchemy import select

from support.documents import mixed_pdf
from support.pipeline import (
    BASE_URL,
    LISTING_EXTERNAL_IDS,
    LISTING_URL,
    OfflineSite,
    build_harness,
    fixture,
)
from support.stubs import StubOcrEngine
from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.core.errors import OCR_FAILED, SOURCE_NOT_RUNNABLE, STAGE_FAILED
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.dedup.service import derive_run_status
from tender_intelligence.orchestrator.retry import RetryPolicy
from tender_intelligence.orchestrator.stages import StageExecution, StageReport, StageRunner
from tender_intelligence.orchestrator.status import (
    STAGE_ORDER,
    RunStatus,
    StageNumber,
    StageStatus,
)
from tender_intelligence.storage.interface import StoredObject


def _run_rows(session_factory, source_id: int) -> list[RunHistory]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(RunHistory)
                .where(RunHistory.source_id == source_id)
                .order_by(RunHistory.id)
            ).all()
        )


def _tenders(session_factory, source_id: int) -> list[Tender]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(Tender).where(Tender.source_id == source_id).order_by(Tender.external_id)
            ).all()
        )


def _documents(session_factory, tender_id: int) -> list[Document]:
    with session_factory() as session:
        return list(session.scalars(select(Document).where(Document.tender_id == tender_id)).all())


def _stored_source(harness, source_id: int) -> Source:
    with harness.session_factory() as session:
        row = session.get(Source, source_id)
        assert row is not None
        return row


# --------------------------------------------------------------------------- §19 A


def test_full_offline_source_run_walks_04_to_09_and_persists_every_stage(
    session_factory_gr, tmp_path
) -> None:
    """§19 A — RunHistory START → 04 → 05 → 06 → 07 → 08 → 09 → COMPLETED.

    Every claim is checked against persisted state: the run row, the tender rows, the document
    rows and the extraction artifacts on disk.
    """
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    config_version = harness.ensure_settings()

    report = harness.coordinator.run_source(source_id)

    assert report.status is RunStatus.COMPLETED, report.stage_map()
    assert report.dry_run is False
    assert report.run_history_id is not None

    # -- the run row, read back from the database --------------------------
    rows = _run_rows(session_factory_gr, source_id)
    assert len(rows) == 1
    run = rows[0]
    assert run.id == report.run_history_id
    assert run.ended_at is not None, "a finished run must have an end timestamp"
    assert run.error_count == 0
    assert run.correlation_id == report.correlation_id
    assert derive_run_status(run) == "COMPLETED"
    assert run.config_version == config_version
    assert run.failed_stage is None
    assert run.error_code is None

    # The stage map is the reconstruction §8 requires: all six, in order, all COMPLETED.
    assert run.stages is not None
    assert list(run.stages) == [stage.value for stage in STAGE_ORDER]
    assert all(
        value == StageStatus.COMPLETED.value for key, value in run.stages.items() if key != "14-verdict"
    )
    assert run.stages["14-verdict"] == StageStatus.SKIPPED_NOT_IMPLEMENTED.value

    # -- listing tallies ---------------------------------------------------
    assert run.listings_found == 3
    assert run.new_count == 3
    assert run.update_count == 0
    assert run.unchanged_count == 0

    # -- the tenders, read back from the database --------------------------
    tenders = _tenders(session_factory_gr, source_id)
    assert [t.external_id for t in tenders] == ["165", "166", "167"]
    assert all(t.correlation_id for t in tenders), "every tender keeps its own identity (§4)"
    assert report.tenders_considered == 3

    # -- documents and their artifacts, read back from the store -----------
    assert report.documents_acquired > 0, "the fixture's attachments must have been fetched"
    assert report.documents_processed > 0, "acquisition must have produced extractable files"

    keys = harness.stored_keys()
    for tender in tenders:
        documents = _documents(session_factory_gr, tender.id)
        assert documents, f"tender {tender.external_id} produced no document rows"
        assert any(
            d.storage_path and harness.storage.exists(d.storage_path) for d in documents
        ), f"tender {tender.external_id} has no document object in storage"
        assert f"tenders/{tender.id}/extracted/bundle.json" in keys, (
            f"tender {tender.external_id} has no persisted bundle"
        )

    # -- the run finished cleanly at the source ----------------------------
    source = _stored_source(harness, source_id)
    assert source.last_run_at is not None, "a finished run stamps the source"
    assert source.last_error is None


def test_triage_handoff_only_receives_pass_and_records_run_link(session_factory_gr, tmp_path):
    calls = []
    verdict_calls = []
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        ocr=StubOcrEngine(),
        coordinator_overrides={"triage_pass_handoff": calls.append, "verdict_handoff": verdict_calls.append},
    )
    source_id = harness.seed_source()
    harness.ensure_settings()
    with session_factory_gr() as session:
        from tender_intelligence.db.models.config import Setting

        settings = session.get(Setting, 1)
        assert settings is not None
        settings.triage_rules = {"exclude_keywords": ["procurement"]}
        session.commit()

    report = harness.coordinator.run_source(source_id)
    assert report.run_history_id is not None
    with session_factory_gr() as session:
        rows = list(session.query(TriageResult).order_by(TriageResult.id))
        assert len(rows) == 3
        assert all(row.run_id == report.run_history_id for row in rows)
        assert [row.status for row in rows].count("passed") == 1
        assert [row.status for row in rows].count("triage_discarded") == 2
    assert len(calls) == 1
    assert calls[0].status == "passed"
    assert calls[0].correlation_id
    assert len(verdict_calls) == 1
    assert verdict_calls[0].status == "passed"
    assert report.stage(StageNumber.VERDICT).status is StageStatus.COMPLETED
    with session_factory_gr() as session:
        timeline = TimelineService(session).reconstruct_by_tender(rows[0].tender_id)
        assert timeline is not None
        triage_event = next(event for event in timeline.events if event.stage == "triage")
        assert triage_event.run_id == report.run_history_id


def test_run_only_touches_the_configured_source(session_factory_gr, tmp_path) -> None:
    """The run's requests all target the source's own host — no admin app, no other site."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    harness.coordinator.run_source(source_id)

    assert harness.site.requests, "the run must actually have made requests"
    assert all(url.startswith("https://data.wahooas.org/") for url in harness.site.requests)
    assert LISTING_URL in harness.site.requests


# --------------------------------------------------------------------------- §19 J


def test_repeated_run_reuses_state_and_records_a_separate_run(session_factory_gr, tmp_path) -> None:
    """§19 J — the same fixture twice: no duplicate identity, no re-extraction, two run rows."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    first = harness.coordinator.run_source(source_id)
    tender_ids_after_first = [t.id for t in _tenders(session_factory_gr, source_id)]
    keys_after_first = harness.stored_keys()
    documents_after_first = sum(
        len(_documents(session_factory_gr, tid)) for tid in tender_ids_after_first
    )
    assert first.documents_acquired > 0

    second = harness.coordinator.run_source(source_id)

    # No duplicate tender identity: same rows, same ids.
    tenders = _tenders(session_factory_gr, source_id)
    assert [t.id for t in tenders] == tender_ids_after_first
    assert [t.external_id for t in tenders] == list(reversed(LISTING_EXTERNAL_IDS))

    # The second run recognised everything as already seen.
    rows = _run_rows(session_factory_gr, source_id)
    assert len(rows) == 2, "each run gets its own RunHistory record (§5)"
    assert rows[0].id != rows[1].id
    assert rows[1].correlation_id == second.correlation_id
    assert rows[1].unchanged_count == 3
    assert rows[1].new_count == 0
    assert derive_run_status(rows[1]) == "COMPLETED"

    # Nothing changed for an unchanged tender, so stages 07–09 had nothing to do.
    assert second.status is RunStatus.COMPLETED
    assert second.stage(StageNumber.DETAIL).status is StageStatus.PENDING  # type: ignore[union-attr]
    assert second.tenders_considered == 0

    # Existing state reused: no second document row, no second extraction artifact.
    assert (
        sum(len(_documents(session_factory_gr, tid)) for tid in tender_ids_after_first)
        == documents_after_first
    )
    assert harness.stored_keys() == keys_after_first


def test_unchanged_run_does_not_refetch_attachment_pages(session_factory_gr, tmp_path) -> None:
    """The second run must stop at persistence — no detail-page fetch for unchanged tenders."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    harness.coordinator.run_source(source_id)
    detail_requests = [u for u in harness.site.requests if "/list" in u and "tenders/list" not in u]
    assert detail_requests, "the first run must have fetched detail pages"

    harness.site.requests.clear()
    harness.coordinator.run_source(source_id)

    assert not [
        u for u in harness.site.requests if "/list" in u and "tenders/list" not in u
    ], "an unchanged tender must not be re-fetched (§13)"


# --------------------------------------------------------------------------- §19 K


def test_a_run_is_traceable_from_row_to_stage_to_tender_to_document(
    session_factory_gr, tmp_path
) -> None:
    """§19 K — trace RunHistory → stage → tender → document without relying on timestamps."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    report = harness.coordinator.run_source(source_id)

    # Run → row, by correlation, not by time.
    with session_factory_gr() as session:
        run = session.scalars(
            select(RunHistory).where(RunHistory.correlation_id == report.correlation_id)
        ).one()
    assert run.id == report.run_history_id
    assert run.source_id == source_id

    # Row → stages, via the persisted map.
    assert run.stages is not None
    for stage in STAGE_ORDER:
        expected = (
            StageStatus.SKIPPED_NOT_IMPLEMENTED.value
            if stage is StageNumber.VERDICT
            else StageStatus.COMPLETED.value
        )
        assert run.stages[stage.value] == expected

    # Report → stages, and every stage carries the run's correlation.
    for outcome in report.stages:
        assert outcome.correlation_id == report.correlation_id
        assert outcome.started_at <= outcome.ended_at

    # Tender → its documents → their stored objects.
    for tender in _tenders(session_factory_gr, source_id):
        documents = _documents(session_factory_gr, tender.id)
        assert documents
        for document in documents:
            assert document.tender_id == tender.id
            if document.storage_path is not None:
                assert harness.storage.exists(document.storage_path)

    # A document's identity chain is the tender's, not the run's (§4).
    tender_ids = {t.id for t in _tenders(session_factory_gr, source_id)}
    with session_factory_gr() as session:
        rows = list(session.scalars(select(Document).where(Document.tender_id.in_(tender_ids))))
    assert rows, "the run produced no documents to trace"


# --------------------------------------------------------------------------- §19 B


class _CrashAfterDiscoveryRunner(StageRunner):
    """A ``StageRunner`` that dies with ``SystemExit`` once, right after stage 04.

    ``SystemExit`` is a ``BaseException``, so neither ``StageRunner.run`` nor the
    coordinator's ``except Exception`` boundary catches it: the started run row stays
    RUNNING, no stage is recorded, and the source is never stamped — exactly the crash the
    next run has to recover from (§17).
    """

    def __init__(self) -> None:
        super().__init__()
        self._crashed = False

    def run[T](
        self,
        stage: StageNumber,
        action: Callable[[], tuple[StageReport, T]],
    ) -> StageExecution[T]:
        execution = super().run(stage, action)
        if stage is StageNumber.DISCOVERY and not self._crashed:
            self._crashed = True
            raise SystemExit("simulated crash after 04-discovery")
        return execution


def test_crash_after_discovery_leaves_a_running_row_and_the_next_run_recovers(
    session_factory_gr, tmp_path
) -> None:
    """§19 B — a process crash after discovery leaves RUNNING; a fresh run completes it."""
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        coordinator_overrides={"stage_runner": _CrashAfterDiscoveryRunner()},
    )
    source_id = harness.seed_source()
    harness.ensure_settings()

    with pytest.raises(SystemExit):
        harness.coordinator.run_source(source_id)

    # The crash escaped every `except Exception`: the committed RUNNING row is all that
    # survives, with no stage map, no final tallies and no source stamp.
    rows = _run_rows(session_factory_gr, source_id)
    assert len(rows) == 1
    crashed = rows[0]
    assert derive_run_status(crashed) == "RUNNING"
    assert crashed.ended_at is None
    assert crashed.stages is None
    assert crashed.error_count == 0
    assert crashed.config_version == 1
    assert _tenders(session_factory_gr, source_id) == []
    assert harness.stored_keys() == []
    assert _stored_source(harness, source_id).last_run_at is None, "source not stamped"

    # A fresh worker/process re-runs the same source: nothing is stuck, no duplicate
    # identity is created (only the crashed-run row plus a completed run exist).
    fresh = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    report = fresh.coordinator.run_source(source_id)
    assert report.status is RunStatus.COMPLETED

    rows = _run_rows(session_factory_gr, source_id)
    assert len(rows) == 2
    assert rows[0].id != rows[1].id
    assert [derive_run_status(r) for r in rows] == ["RUNNING", "COMPLETED"]
    assert rows[1].error_count == 0
    tenders = _tenders(session_factory_gr, source_id)
    assert [t.external_id for t in tenders] == ["165", "166", "167"]
    assert fresh.stored_keys(), "the recovery run persisted real artifacts"


# --------------------------------------------------------------------------- §19 C


def test_configuration_change_is_observed_by_the_next_run(session_factory_gr, tmp_path) -> None:
    """§19 C — config is re-read per run; a change between runs is picked up (§3)."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    assert harness.ensure_settings() == 1

    first = harness.coordinator.run_source(source_id)
    assert first.config_version == 1
    assert [r.config_version for r in _run_rows(session_factory_gr, source_id)] == [1]

    assert harness.change_configuration(test_mode=False, reason="prompt 10 §19 C") == 2

    second = harness.coordinator.run_source(source_id)
    assert second.config_version == 2
    assert [r.config_version for r in _run_rows(session_factory_gr, source_id)] == [1, 2]


# --------------------------------------------------------------------------- §19 D


def test_worker_runs_without_an_admin_http_app(session_factory_gr, tmp_path) -> None:
    """§19 D — the worker is admin-independent: persisted rows, persisted settings, no HTTP."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    report = harness.worker.run_once(source_ids=[source_id])

    assert report.ran == 1
    assert report.failed == 0
    run = report.run_for(source_id)
    assert run is not None and run.status is RunStatus.COMPLETED
    assert harness.site.requests, "the run must actually have made requests"
    assert all(url.startswith(BASE_URL) for url in harness.site.requests), (
        "the worker must not contact any admin endpoint"
    )


# --------------------------------------------------------------------------- §19 E


def test_source_isolation_and_mystery_type_failure(
    session_factory_gr, tmp_path,
) -> None:
    """§19 E — an unregistered source_type fails only its own run; the good one completes."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    good_id = harness.seed_source(name="good")
    mystery_id = harness.seed_source(name="mystery", source_type="mystery")
    harness.ensure_settings()

    report = harness.worker.run_once(source_ids=[good_id, mystery_id])

    assert report.ran == 2
    assert report.failed == 1
    assert report.failures == ()
    assert report.skipped == ()

    mystery_run = report.run_for(mystery_id)
    assert mystery_run is not None
    assert mystery_run.status is RunStatus.FAILED
    assert mystery_run.error_code == SOURCE_NOT_RUNNABLE
    assert mystery_run.failed_stage is StageNumber.DISCOVERY

    good_run = report.run_for(good_id)
    assert good_run is not None and good_run.status is RunStatus.COMPLETED
    assert len(_tenders(session_factory_gr, good_id)) == 3

    # The mystery row reconstructs exactly what failed and what stayed pending.
    rows = _run_rows(session_factory_gr, mystery_id)
    assert len(rows) == 1
    row = rows[0]
    assert derive_run_status(row) == "ERRORED"
    assert row.error_count == 1
    assert row.failed_stage == StageNumber.DISCOVERY.value
    assert row.error_code == SOURCE_NOT_RUNNABLE
    assert row.stages is not None
    assert row.stages[StageNumber.DISCOVERY.value] == StageStatus.FAILED.value
    for stage in STAGE_ORDER[1:]:
        assert row.stages[stage.value] == StageStatus.PENDING.value

    assert (
        _stored_source(harness, mystery_id).last_error
        == "04-discovery failed (source_not_runnable)"
    )


# --------------------------------------------------------------------------- §19 F


class _ExplodingRegistry:
    """An ``AdapterRegistry`` stand-in whose ``build`` always raises a code-less error."""

    @staticmethod
    def build(spec: object) -> object:
        raise RuntimeError("injected registry explosion")


def test_code_less_registry_failure_maps_to_stage_failed_and_alerts(
    session_factory_gr, tmp_path,
) -> None:
    """§19 F — no claimant error code ⇒ ``stage_failed``, and the alert seam fires (§16, §22)."""
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        coordinator_overrides={"registry": _ExplodingRegistry()},
    )
    source_id = harness.seed_source()
    harness.ensure_settings()

    report = harness.coordinator.run_source(source_id)

    assert report.status is RunStatus.FAILED
    assert report.error_code == STAGE_FAILED
    assert report.failed_stage is StageNumber.DISCOVERY

    assert harness.alerts.notices, "a run-level failure must reach the alert seam"
    notice = harness.alerts.notices[0]
    assert notice.error_code == STAGE_FAILED
    assert notice.source_id == source_id

    row = _run_rows(session_factory_gr, source_id)[0]
    assert derive_run_status(row) == "ERRORED"
    assert row.error_count == 1
    assert _stored_source(harness, source_id).last_error == "04-discovery failed (stage_failed)"


# --------------------------------------------------------------------------- §19 G


def test_one_failed_document_does_not_fail_the_run(session_factory_gr, tmp_path) -> None:
    """§19 G — a scanned RFP without OCR fails per document; the run stays PARTIAL (§9)."""
    site = OfflineSite(
        detail_by_tender={
            external_id: fixture("detail_en_complete.html") for external_id in LISTING_EXTERNAL_IDS
        },
        attachment_bytes={
            "/uploads/tenders/167/rfp_p-z1-bz0-012_c_en.pdf": mixed_pdf(
                native_pages=1, scanned_pages=1
            )
        },
    )
    harness = build_harness(session_factory_gr, tmp_path, site=site, ocr=None)
    source_id = harness.seed_source()
    harness.ensure_settings()

    report = harness.coordinator.run_source(source_id)

    # Acquisition succeeded end to end; the failure is entirely a document-processing one.
    acquisition = report.stage(StageNumber.ACQUISITION)
    assert acquisition is not None and acquisition.status is StageStatus.COMPLETED
    processing = report.stage(StageNumber.PROCESSING)
    assert processing is not None and processing.status is StageStatus.PARTIAL
    assert processing.failed_items == 3, "one failed document per tender"

    # The run is PARTIAL, never FAILED, so it derives COMPLETED with zero error count.
    assert report.status is RunStatus.PARTIAL
    row = _run_rows(session_factory_gr, source_id)[0]
    assert row.error_count == 0
    assert derive_run_status(row) == "COMPLETED"

    # Every tender keeps exactly one failed document, coded OCR_FAILED.
    failed_docs: list[Document] = []
    for tender in _tenders(session_factory_gr, source_id):
        docs = _documents(session_factory_gr, tender.id)
        tender_failed = [
            d
            for d in docs
            if d.extraction_status == "failed" and d.extraction_error_code is not None
        ]
        assert len(tender_failed) == 1, f"tender {tender.external_id} must have one failed doc"
        failed_docs.extend(tender_failed)
    assert len(failed_docs) == 3
    assert all(d.extraction_error_code == OCR_FAILED for d in failed_docs)  # type: ignore[union-attr]


# --------------------------------------------------------------------------- §19 H


def _flaky_put(real_put, *, fail_all: bool, until_success: int = 0):
    """A ``put`` that fails exactly the first ``until_success`` ``/attachments/`` writes.

    Acquisition keys always contain ``/attachments/``; bundle keys do not, so processing
    writes pass through untouched.
    """
    remaining = [until_success]

    def put(key: str, data: bytes, content_type: str | None = None) -> StoredObject:
        if "/attachments/" in key and (fail_all or remaining[0] > 0):
            remaining[0] -= 1
            raise OSError("injected storage outage")
        return real_put(key, data, content_type=content_type)

    return put


def test_retryable_storage_failure_backs_off_once_and_completes(
    session_factory_gr, tmp_path,
) -> None:
    """§19 H — one transient storage failure is retried once (bounded) with backoff (§11)."""
    delays: list[float] = []
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        ocr=StubOcrEngine(),
        sleeper=delays.append,
        coordinator_overrides={
            "retry_policy": RetryPolicy(
                attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=3.0
            )
        },
    )
    source_id = harness.seed_source()
    harness.ensure_settings()

    real_put = harness.storage.put
    harness.storage.put = _flaky_put(real_put, fail_all=False, until_success=1)  # type: ignore[method-assign]

    report = harness.coordinator.run_source(source_id)

    # The work set is sorted by external_id, so the first tender ("165") hit the outage;
    # its two-attempt re-drive plus one attempt each for the others totals four.
    acquisition = report.stage(StageNumber.ACQUISITION)
    assert acquisition is not None and acquisition.status is StageStatus.COMPLETED
    assert acquisition.failed_items == 0
    assert acquisition.attempts == 4
    assert delays == [1.0], "one bounded backoff of exactly one second"
    assert report.status is RunStatus.COMPLETED
    row = _run_rows(session_factory_gr, source_id)[0]
    assert derive_run_status(row) == "COMPLETED"
    assert all(
        d.download_status == "downloaded"
        for tender in _tenders(session_factory_gr, source_id)
        for d in _documents(session_factory_gr, tender.id)
    )


def test_exhausted_storage_retries_stay_document_level(session_factory_gr, tmp_path) -> None:
    """§19 H — exhausted retries bound attempts and never FAIL the run (§15)."""
    delays: list[float] = []
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        ocr=StubOcrEngine(),
        sleeper=delays.append,
        coordinator_overrides={
            "retry_policy": RetryPolicy(
                attempts=2, backoff_base_seconds=1.0, backoff_max_seconds=3.0
            )
        },
    )
    source_id = harness.seed_source()
    harness.ensure_settings()

    real_put = harness.storage.put
    harness.storage.put = _flaky_put(real_put, fail_all=True)  # type: ignore[method-assign]

    report = harness.coordinator.run_source(source_id)

    acquisition = report.stage(StageNumber.ACQUISITION)
    assert acquisition is not None and acquisition.status is StageStatus.PARTIAL
    assert acquisition.failed_items == 3
    assert acquisition.attempts == 6, "three tenders, two bounded attempts each"
    assert delays == [1.0, 1.0, 1.0], "one bounded backoff per retried tender"

    assert report.status is RunStatus.PARTIAL
    row = _run_rows(session_factory_gr, source_id)[0]
    assert row.error_count == 0
    assert derive_run_status(row) == "COMPLETED"

    for tender in _tenders(session_factory_gr, source_id):
        docs = _documents(session_factory_gr, tender.id)
        assert any(d.download_status == "failed" for d in docs), (
            f"tender {tender.external_id} must keep failed document rows"
        )


# --------------------------------------------------------------------------- §19 I


def test_dry_run_reports_the_plan_and_persists_nothing(session_factory_gr, tmp_path) -> None:
    """§19 I — dry-run discovers and plans without writing or fetching beyond discovery (§14)."""
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()

    report = harness.worker.run_once(source_ids=[source_id], dry_run=True)

    run = report.run_for(source_id)
    assert run is not None
    assert run.status is RunStatus.DRY_RUN
    assert run.run_history_id is None
    assert len(run.planned_actions) == 4
    assert run.planned_actions[-1] == "would evaluate configurable Stage A triage rules"

    # Nothing business-level was persisted anywhere.
    assert _run_rows(session_factory_gr, source_id) == []
    assert _tenders(session_factory_gr, source_id) == []
    assert harness.stored_keys() == []

    # Discovery ran; neither detail pages nor attachments were fetched.
    assert LISTING_URL in harness.site.requests
    assert not [
        u for u in harness.site.requests if "/list" in u and "tenders/list" not in u
    ], "dry-run must not fetch detail pages"

    # The same worker then does the real run.
    live = harness.coordinator.run_source(source_id)
    assert live.status is RunStatus.COMPLETED
    assert len(_tenders(session_factory_gr, source_id)) == 3


# --------------------------------------------------------------------------- §19 L


def test_secret_never_reaches_logs_reports_or_persisted_state(
    session_factory_gr, tmp_path, caplog,
) -> None:
    """§19 L — an injected credential stays out of logs, reports, rows and errors (§25)."""
    secret = "wahoo-injected-token-7d3c9a2f"
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    good_id = harness.seed_source(name="good", parser_config={"auth_token": secret})
    mystery_id = harness.seed_source(
        name="mystery", source_type="mystery", parser_config={"auth_token": secret}
    )
    harness.ensure_settings()

    with caplog.at_level(logging.DEBUG, logger="tender_intelligence"):
        report = harness.worker.run_once(source_ids=[good_id, mystery_id])

    assert not report.skipped
    assert not report.failures
    assert report.ran == 2

    assert secret not in caplog.text
    assert secret not in str(report)
    assert secret not in repr(report)
    for run in report.runs:
        assert secret not in str(run)
        assert secret not in repr(run)
        assert secret not in repr(run.stage_map())

    for source_id in (good_id, mystery_id):
        row = _run_rows(session_factory_gr, source_id)[0]
        assert secret not in str(row.stages)
        assert secret not in (row.error_code or "")
        assert secret not in (row.failed_stage or "")
        assert secret not in (row.correlation_id or "")

    mystery_source = _stored_source(harness, mystery_id)
    assert secret not in (mystery_source.last_error or "")
    assert mystery_source.last_error == "04-discovery failed (source_not_runnable)"

    mystery_run = report.run_for(mystery_id)
    assert mystery_run is not None and mystery_run.error_code == SOURCE_NOT_RUNNABLE

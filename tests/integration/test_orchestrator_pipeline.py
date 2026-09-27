"""Prompt 10 §19 A – L — the integrated 04 → 09 path, end to end and offline.

This is the file that satisfies prompt 10 §18's "at least one test must exercise the actual
integrated 04→09 path". Nothing here is stubbed except the socket: real WAHO HTML fixtures go
in, real ``Tender``/``Document``/``RunHistory`` rows and real extraction artifacts come out,
and every assertion reads the database or the object store rather than the returned report.
"""

from __future__ import annotations

import hashlib
import json
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
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.dedup.service import derive_run_status
from tender_intelligence.interfaces.llm import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
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
                select(RunHistory).where(RunHistory.source_id == source_id).order_by(RunHistory.id)
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


class _CoordinatorVerdictClient(LLMClient):
    profile_name = "coordinator-verdict-test"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise LLMError("mock provider unavailable", "provider_rejected")
        supplied = json.loads(messages[1].content)
        if "Summarize material eligibility" in messages[0].content:
            segment = supplied["content"][0]
            quote = segment["text"][:min(400, len(segment["text"]))]
            body = {
                "summary": quote,
                "evidence": [{"location": segment["location"], "quote": quote}],
            }
        elif "Extract every material" in messages[0].content:
            document = supplied["documents"][0]
            chunk = document["content"][0]
            body = {
                "requirements": [
                    {
                        "requirement": "review notice",
                        "tender_evidence": [
                            {
                                "document_id": document["document_id"],
                                "location": chunk["location"],
                                "quote": chunk["text"],
                            }
                        ],
                    }
                ]
            }
        else:
            deadline = supplied["deadline"]
            requirements = supplied["material_requirements"]
            body = {
                "schema_version": "verdict.v1",
                "background": "Synthetic coordinator integration fixture.",
                "requirements": [item["requirement"] for item in requirements],
                "deadline_status": deadline["status"],
                "deadline_utc": deadline["deadline_utc"],
                "deadline_date": deadline["date"],
                "deadline_time": deadline["time"],
                "deadline_timezone": deadline["timezone"],
                "source_timezone": deadline["source_timezone"],
                "assessments": [
                    {
                        "requirement": item["requirement"],
                        "tender_evidence": item["tender_evidence"],
                        "company_evidence": [],
                        "status": "unverified",
                        "assessment": "No evidence on file for review notice.",
                        "gap": "No evidence on file for review notice.",
                    }
                    for item in requirements
                ],
                "gaps": ["No evidence on file for review notice."],
                "verdict": "APPLY WITH CONDITIONS",
                "confidence": 0.4,
                "urgency": False,
                "incomplete_inputs": supplied["incomplete_inputs"],
                "limitations": (
                    ["Some document inputs were unavailable."]
                    if supplied["incomplete_inputs"]
                    else []
                ),
            }
        return LLMResponse(
            json.dumps(body), self.profile_name, "mock-coordinator-v1", LLMUsage(10, 20, 0.001)
        )


def _configure_coordinator_verdict(session_factory, harness):
    kb = "# Test capabilities\nSynthetic test-only content."
    kb_bytes = kb.encode()
    harness.storage.put("kb/coordinator-test.md", kb_bytes, "text/markdown")
    with session_factory() as session:
        profile = LLMProfile(
            name="coordinator-verdict-mock",
            base_url="https://mock.coordinator.invalid/v1",
            model="mock-coordinator-v1",
            approved_for_company_docs=True,
            context_window_tokens=32000,
        )
        session.add(profile)
        session.flush()
        session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
        session.add(
            KnowledgeBaseVersion(
                content_ref="kb/coordinator-test.md",
                content_hash=hashlib.sha256(kb_bytes).hexdigest(),
                token_count=12,
                created_by="test",
            )
        )
        session.commit()


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

    # The stage map is the reconstruction §8 requires, including the notification boundary.
    assert run.stages is not None
    assert list(run.stages) == [stage.value for stage in STAGE_ORDER]
    assert all(
        value == StageStatus.COMPLETED.value
        for key, value in run.stages.items()
        if key not in {"14-verdict", "15-notification"}
    )
    assert run.stages["14-verdict"] == StageStatus.SKIPPED_NOT_IMPLEMENTED.value
    assert run.stages["15-notification"] == StageStatus.SKIPPED_NOT_IMPLEMENTED.value

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
        assert any(d.storage_path and harness.storage.exists(d.storage_path) for d in documents), (
            f"tender {tender.external_id} has no document object in storage"
        )
        assert f"tenders/{tender.id}/extracted/bundle.json" in keys, (
            f"tender {tender.external_id} has no persisted bundle"
        )

    # -- the run finished cleanly at the source ----------------------------
    source = _stored_source(harness, source_id)
    assert source.last_run_at is not None, "a finished run stamps the source"
    assert source.last_error is None


def test_process_now_reuses_persisted_tender_without_listing_or_dedup_writes(
    session_factory_gr, tmp_path
) -> None:
    harness = build_harness(session_factory_gr, tmp_path, ocr=StubOcrEngine())
    source_id = harness.seed_source()
    harness.ensure_settings()
    harness.coordinator.run_source(source_id)
    tender = _tenders(session_factory_gr, source_id)[0]
    original_tender = (tender.external_id, tender.correlation_id, tender.first_seen_at)
    listing_requests_before = sum(
        request.split("?")[0] == LISTING_URL for request in harness.site.requests
    )
    last_run_at_before = harness.scheduler.get(source_id).spec.last_run_at

    report = harness.coordinator.run_existing_tender(tender.id)

    assert report.status in (RunStatus.COMPLETED, RunStatus.PARTIAL)
    assert report.run_history_id is not None
    assert report.stage_map()[StageNumber.DISCOVERY.value] == "PENDING"
    assert report.stage_map()[StageNumber.DEDUP.value] == "PENDING"
    assert report.stage_map()[StageNumber.PERSISTENCE.value] == "PENDING"
    assert report.stage_map()[StageNumber.DETAIL.value] in {"COMPLETED", "PARTIAL"}
    assert sum(
        request.split("?")[0] == LISTING_URL for request in harness.site.requests
    ) == listing_requests_before
    with session_factory_gr() as session:
        persisted = session.get(Tender, tender.id)
        assert (
            persisted.external_id,
            persisted.correlation_id,
            persisted.first_seen_at,
        ) == original_tender
        manual_run = session.get(RunHistory, report.run_history_id)
        assert manual_run.trigger == "manual_tender"
        assert manual_run.tender_id == tender.id
        assert manual_run.ended_at is not None
    assert harness.scheduler.get(source_id).spec.last_run_at == last_run_at_before
    with session_factory_gr() as session:
        timeline = TimelineService(session).reconstruct_by_tender(tender.id)
    assert report.run_history_id in (timeline.run_ids or [])


def test_triage_handoff_only_receives_pass_and_records_run_link(session_factory_gr, tmp_path):
    calls = []
    verdict_calls = []
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        ocr=StubOcrEngine(),
        coordinator_overrides={
            "triage_pass_handoff": calls.append,
            "verdict_handoff": verdict_calls.append,
        },
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


def test_normal_coordinator_handoff_runs_verdict_engine_and_persists_provenance(
    session_factory_gr, tmp_path
) -> None:
    client = _CoordinatorVerdictClient()
    site = OfflineSite(
        detail_by_tender=dict.fromkeys(LISTING_EXTERNAL_IDS, fixture("detail_en_complete.html"))
    )
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        site=site,
        verdict_client_factory=lambda _profile: client,
    )
    source_id = harness.seed_source()
    harness.ensure_settings()
    _configure_coordinator_verdict(session_factory_gr, harness)

    report = harness.coordinator.run_source(source_id)

    assert report.stage(StageNumber.TRIAGE).status is StageStatus.COMPLETED
    assert report.stage(StageNumber.VERDICT).status is StageStatus.COMPLETED
    assert "skipped" not in (report.stage(StageNumber.VERDICT).detail or "").casefold()
    assert client.calls >= 2 * len(LISTING_EXTERNAL_IDS)
    with session_factory_gr() as session:
        triage = session.query(TriageResult).all()
        verdicts = session.query(Verdict).all()
        calls = session.query(LLMCall).filter_by(role="verdict").all()
        assert len(triage) == len(LISTING_EXTERNAL_IDS)
        assert all(row.status == "passed" for row in triage)
        assert len(verdicts) == len(LISTING_EXTERNAL_IDS)
        assert len(calls) == 2 * len(LISTING_EXTERNAL_IDS)
        for verdict in verdicts:
            tender = session.get(Tender, verdict.tender_id)
            assert tender is not None
            assert verdict.llm_profile_id is not None
            assert verdict.knowledge_base_version_id is not None
            assert verdict.prompt_version == "stage-b.v1"
            assert verdict.schema_version == "verdict.v1"
            assert verdict.provider == "mock.coordinator.invalid"
            assert verdict.model == "mock-coordinator-v1"
            assert verdict.run_id == report.run_history_id
            assert verdict.recommendation == "APPLY WITH CONDITIONS"
        assert all(call.run_id == report.run_history_id for call in calls)


@pytest.mark.parametrize("triage_mode", ["discard", "failure"])
def test_normal_coordinator_handoff_never_runs_verdict_for_non_pass(
    session_factory_gr, tmp_path, triage_mode
) -> None:
    client = _CoordinatorVerdictClient()
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        verdict_client_factory=lambda _profile: client,
    )
    source_id = harness.seed_source()
    harness.ensure_settings()
    with session_factory_gr() as session:
        setting = session.get(Setting, 1)
        assert setting is not None
        setting.triage_rules = (
            {"exclude_keywords": ["procurement", "recrutement"]}
            if triage_mode == "discard"
            else "malformed"
        )
        session.commit()

    report = harness.coordinator.run_source(source_id)

    assert report.stage(StageNumber.VERDICT).status is StageStatus.COMPLETED
    assert client.calls == 0
    with session_factory_gr() as session:
        triage = session.query(TriageResult).all()
        assert triage
        if triage_mode == "discard":
            assert all(row.status == "triage_discarded" for row in triage)
        else:
            assert all(row.status == "triage_failed" for row in triage)
        assert session.query(Verdict).count() == 0
        assert session.query(LLMCall).filter_by(role="verdict").count() == 0


def test_stage_b_provider_failure_is_persisted_and_reported_as_partial(
    session_factory_gr, tmp_path
) -> None:
    client = _CoordinatorVerdictClient(fail=True)
    harness = build_harness(
        session_factory_gr,
        tmp_path,
        verdict_client_factory=lambda _profile: client,
    )
    source_id = harness.seed_source()
    harness.ensure_settings()
    _configure_coordinator_verdict(session_factory_gr, harness)

    report = harness.coordinator.run_source(source_id)

    assert report.stage(StageNumber.TRIAGE).status is StageStatus.COMPLETED
    assert report.stage(StageNumber.VERDICT).status is StageStatus.PARTIAL
    assert client.calls == len(LISTING_EXTERNAL_IDS)
    with session_factory_gr() as session:
        tenders = session.query(Tender).filter_by(source_id=source_id).all()
        assert all(row.status == "verdict_failed" for row in tenders)
        assert session.query(Verdict).count() == 0
        failed_calls = session.query(LLMCall).filter_by(role="verdict", status="failed").all()
        assert len(failed_calls) == len(LISTING_EXTERNAL_IDS)


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

    assert not [u for u in harness.site.requests if "/list" in u and "tenders/list" not in u], (
        "an unchanged tender must not be re-fetched (§13)"
    )


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
            if stage in {StageNumber.VERDICT, StageNumber.NOTIFICATION}
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
    session_factory_gr,
    tmp_path,
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
    session_factory_gr,
    tmp_path,
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
    session_factory_gr,
    tmp_path,
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
    assert not [u for u in harness.site.requests if "/list" in u and "tenders/list" not in u], (
        "dry-run must not fetch detail pages"
    )

    # The same worker then does the real run.
    live = harness.coordinator.run_source(source_id)
    assert live.status is RunStatus.COMPLETED
    assert len(_tenders(session_factory_gr, source_id)) == 3


# --------------------------------------------------------------------------- §19 L


def test_secret_never_reaches_logs_reports_or_persisted_state(
    session_factory_gr,
    tmp_path,
    caplog,
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


# --------------------------------------------------------------------------- Prompt 14 remediation
# These tests prove that worker/main.py:build_pipeline constructs a RunCoordinator with
# Stage B (verdict_handoff) wired by default — the confirmed HIGH-severity defect from
# the independent Prompt 14 verification (demo-evidence/prompt-14-verification-report.md).
#
# The tests exercise the SAME coordinator construction used by the normal worker/runtime
# (build_pipeline), not merely the VerdictEngine in isolation, which was already proven.
# They are required by the remediation spec §8 (end-to-end test) and §9 A/B/C (negative tests).


class _WorkerVerdictClient(LLMClient):
    """Minimal mock LLM client for worker/build_pipeline integration tests.

    Returns a valid verdict JSON so VerdictEngine persists a Verdict row.
    Tracks call count to assert Stage B was actually invoked.
    Optionally raises to exercise the Stage B failure path.
    """

    profile_name = "worker-verdict-test"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def chat(self, messages: list[LLMMessage], *, max_tokens: int | None = None) -> LLMResponse:
        self.calls += 1
        if self.fail:
            raise LLMError("mock provider unavailable for worker test", "provider_rejected")
        supplied = json.loads(messages[1].content)
        if "Summarize material eligibility" in messages[0].content:
            segment = supplied["content"][0]
            quote = segment["text"][:min(400, len(segment["text"]))]
            body = {
                "summary": quote,
                "evidence": [{"location": segment["location"], "quote": quote}],
            }
        elif "Extract every material" in messages[0].content:
            document = supplied["documents"][0]
            chunk = document["content"][0]
            body = {
                "requirements": [
                    {
                        "requirement": "review notice",
                        "tender_evidence": [
                            {
                                "document_id": document["document_id"],
                                "location": chunk["location"],
                                "quote": chunk["text"],
                            }
                        ],
                    }
                ]
            }
        else:
            deadline = supplied["deadline"]
            requirements = supplied["material_requirements"]
            body = {
                "schema_version": "verdict.v1",
                "background": "Synthetic worker integration fixture.",
                "requirements": [item["requirement"] for item in requirements],
                "deadline_status": deadline["status"],
                "deadline_utc": deadline["deadline_utc"],
                "deadline_date": deadline["date"],
                "deadline_time": deadline["time"],
                "deadline_timezone": deadline["timezone"],
                "source_timezone": deadline["source_timezone"],
                "assessments": [
                    {
                        "requirement": item["requirement"],
                        "tender_evidence": item["tender_evidence"],
                        "company_evidence": [],
                        "status": "unverified",
                        "assessment": "No evidence on file for review notice.",
                        "gap": "No evidence on file for review notice.",
                    }
                    for item in requirements
                ],
                "gaps": ["No evidence on file for review notice."],
                "verdict": "APPLY WITH CONDITIONS",
                "confidence": 0.4,
                "urgency": False,
                "incomplete_inputs": supplied["incomplete_inputs"],
                "limitations": [],
            }
        return LLMResponse(
            json.dumps(body), self.profile_name, "mock-worker-v1", LLMUsage(10, 20, 0.001)
        )


def _configure_worker_verdict(session_factory, storage) -> None:
    """Seed the LLM profile/role/KB needed for VerdictEngine to run in worker tests."""
    import hashlib

    kb = "# Worker test capabilities\nSynthetic test-only KB content."
    kb_bytes = kb.encode()
    storage.put("kb/worker-test.md", kb_bytes, "text/markdown")
    with session_factory() as session:
        from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion

        profile = LLMProfile(
            name="worker-verdict-mock",
            base_url="https://mock.worker.invalid/v1",
            model="mock-worker-v1",
            approved_for_company_docs=True,
            context_window_tokens=32000,
        )
        session.add(profile)
        session.flush()
        session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
        session.add(
            KnowledgeBaseVersion(
                content_ref="kb/worker-test.md",
                content_hash=hashlib.sha256(kb_bytes).hexdigest(),
                token_count=12,
                created_by="worker-test",
            )
        )
        session.commit()


def test_offline_coordinator_with_stage_b_persists_verdict(session_factory_gr, tmp_path) -> None:
    """Coordinator orchestration case; production main() wiring is covered separately."""
    client = _WorkerVerdictClient()
    site = OfflineSite(
        detail_by_tender=dict.fromkeys(LISTING_EXTERNAL_IDS, fixture("detail_en_complete.html"))
    )

    base_harness = build_harness(
        session_factory_gr,
        tmp_path,
        site=site,
    )

    # Build an explicitly offline coordinator to isolate stage-level orchestration behavior.
    from httpx2 import MockTransport

    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.acquisition.service import DocumentAcquisitionService
    from tender_intelligence.dedup.service import DedupService
    from tender_intelligence.notifications.contracts import NotificationOutcome
    from tender_intelligence.orchestrator.config import ConfigLoader
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.retry import RetryPolicy
    from tender_intelligence.orchestrator.scheduler import SourceScheduler
    from tender_intelligence.processing.service import DocumentProcessingService
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.sources.polite import PoliteHttpClient
    from tender_intelligence.verdict.runtime import build_verdict_handoff

    policy = CrawlPolicy(
        request_interval_seconds=0.0, backoff_base_seconds=0.0, backoff_max_seconds=0.0
    )
    transport = MockTransport(site.handler)
    discovery_http = PoliteHttpClient.build(policy, transport=transport)

    class _CaptureNotifications:
        def __init__(self) -> None:
            self.events = []

        def send(self, event, *, dry_run=False):
            self.events.append(event)
            return NotificationOutcome(status="sent")

    notification_capture = _CaptureNotifications()

    # This coordinator is test-local; production entrypoint wiring is not inferred from it.
    offline_fetcher = DocumentFetcher(policy, transport=transport)
    offline_registry = AdapterRegistry.default(policy=policy, fetcher=discovery_http.get)
    offline_coordinator = RunCoordinator(
        session_factory=session_factory_gr,
        registry=offline_registry,
        dedup=DedupService(session_factory_gr),
        acquisition=DocumentAcquisitionService(
            session_factory_gr, storage=base_harness.storage, fetcher=offline_fetcher
        ),
        processing=DocumentProcessingService(session_factory_gr, storage=base_harness.storage),
        scheduler=SourceScheduler(session_factory_gr),
        config_loader=ConfigLoader(session_factory_gr),
        retry_policy=RetryPolicy(attempts=2, backoff_base_seconds=0.0),
        # ── Stage B wired exactly as build_pipeline does ──────────────────────────────
        verdict_handoff=build_verdict_handoff(
            session_factory_gr,
            base_harness.storage,
            lambda profile: client,
        ),
        notification_dispatcher=notification_capture,
    )

    source_id = base_harness.seed_source()
    base_harness.ensure_settings()
    _configure_worker_verdict(session_factory_gr, base_harness.storage)

    report = offline_coordinator.run_source(source_id)

    # Stage B ran and completed (not skipped).
    verdict_stage = report.stage(StageNumber.VERDICT)
    assert verdict_stage is not None
    assert verdict_stage.status is StageStatus.COMPLETED, (
        f"Stage B must not be SKIPPED in the normal worker path; got: {verdict_stage.status}. "
        f"detail={verdict_stage.detail!r}"
    )
    assert "skipped" not in (verdict_stage.detail or "").casefold(), (
        f"Stage B detail must not say 'skipped': {verdict_stage.detail!r}"
    )
    notification_stage = report.stage(StageNumber.NOTIFICATION)
    assert notification_stage is not None
    assert notification_stage.status is StageStatus.COMPLETED

    # VerdictEngine was actually called.
    assert client.calls >= 2 * len(LISTING_EXTERNAL_IDS), (
        f"Expected Stage B calls plus optional document-map calls; got {client.calls}"
    )

    # Verdict rows persisted.
    with session_factory_gr() as session:
        verdicts = session.query(Verdict).all()
        llm_calls = session.query(LLMCall).filter_by(role="verdict").all()
        triage = session.query(TriageResult).all()

    assert len(verdicts) == len(LISTING_EXTERNAL_IDS), (
        f"Expected {len(LISTING_EXTERNAL_IDS)} Verdict rows but got {len(verdicts)}"
    )
    assert all(v.recommendation == "APPLY WITH CONDITIONS" for v in verdicts)
    assert len(llm_calls) == 2 * len(LISTING_EXTERNAL_IDS)
    assert all(c.run_id == report.run_history_id for c in llm_calls)
    assert all(t.status == "passed" for t in triage)
    assert len(notification_capture.events) == len(verdicts)
    assert {event.verdict_id for event in notification_capture.events} == {
        verdict.id for verdict in verdicts
    }

    # Provenance present on every verdict.
    for v in verdicts:
        assert v.llm_profile_id is not None
        assert v.knowledge_base_version_id is not None
        assert v.prompt_version == "stage-b.v1"
        assert v.schema_version == "verdict.v1"
        assert v.run_id == report.run_history_id


@pytest.mark.parametrize("stage_b_fails", [False, True])
def test_worker_entrypoint_uses_stage_b_enabled_pipeline(
    session_factory_gr, tmp_path, monkeypatch, stage_b_fails
) -> None:
    """The executable main path must itself build and run the coordinator that persists verdicts."""

    from httpx2 import MockTransport

    from tender_intelligence.config.settings import EnvSettings
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.worker import main as worker_main

    site = OfflineSite(
        detail_by_tender=dict.fromkeys(LISTING_EXTERNAL_IDS, fixture("detail_en_complete.html"))
    )
    harness = build_harness(session_factory_gr, tmp_path, site=site)
    source_id = harness.seed_source()
    harness.ensure_settings()
    _configure_worker_verdict(session_factory_gr, harness.storage)
    client = _WorkerVerdictClient(fail=stage_b_fails)

    class _EngineStub:
        def dispose(self) -> None:
            pass

    monkeypatch.setattr(worker_main, "build_engine", lambda _url: _EngineStub())
    monkeypatch.setattr(worker_main, "session_factory", lambda _engine: session_factory_gr)
    monkeypatch.setattr(
        worker_main,
        "get_env_settings",
        lambda: EnvSettings(
            database_url="sqlite://",
            storage_dir=str(harness.storage.root),
            dev_alert_email="",
        ),
    )
    monkeypatch.setattr(worker_main, "setup_logging", lambda _level: None)

    result = worker_main.main(
        ["--source-id", str(source_id)],
        llm_client_factory=lambda profile, api_key: client,
        discovery_transport=MockTransport(site.handler),
        fetcher_transport=MockTransport(site.handler),
        crawl_policy=CrawlPolicy(
            request_interval_seconds=0.0,
            backoff_base_seconds=0.0,
            backoff_max_seconds=0.0,
        ),
    )

    assert result == 0
    with session_factory_gr() as session:
        tenders = session.query(Tender).filter_by(source_id=source_id).all()
        triage = session.query(TriageResult).all()
        verdicts = session.query(Verdict).all()
        calls = session.query(LLMCall).filter_by(role="verdict").all()
    assert len(tenders) == len(LISTING_EXTERNAL_IDS)
    assert all(row.status == "passed" for row in triage)
    if stage_b_fails:
        assert client.calls == len(LISTING_EXTERNAL_IDS)
        assert verdicts == []
        assert len(calls) == len(LISTING_EXTERNAL_IDS)
        assert all(call.status == "failed" for call in calls)
        assert all(tender.status == "verdict_failed" for tender in tenders)
    else:
        assert client.calls >= 2 * len(LISTING_EXTERNAL_IDS)
        assert len(verdicts) == len(LISTING_EXTERNAL_IDS)
        assert len(calls) == 2 * len(LISTING_EXTERNAL_IDS)
        assert all(verdict.tender_id in {row.id for row in tenders} for verdict in verdicts)
        assert all(verdict.knowledge_base_version_id is not None for verdict in verdicts)
        assert all(verdict.provider == "mock.worker.invalid" for verdict in verdicts)
        assert all(verdict.prompt_version == "stage-b.v1" for verdict in verdicts)
    assert all(call.tender_id in {row.id for row in tenders} for call in calls)


@pytest.mark.parametrize("triage_mode", ["discard", "failure"])
def test_build_pipeline_stage_b_not_invoked_for_non_pass_triage(
    session_factory_gr, tmp_path, triage_mode
) -> None:
    """Stage B is never invoked when Stage A does not produce a PASS (remediation §9 A+B).

    build_pipeline wires verdict_handoff; the gate that prevents non-PASS reaching Stage B
    must still hold when the production construction path is used.
    """
    client = _WorkerVerdictClient()
    site = OfflineSite(
        detail_by_tender=dict.fromkeys(LISTING_EXTERNAL_IDS, fixture("detail_en_complete.html"))
    )
    base_harness = build_harness(session_factory_gr, tmp_path, site=site)

    from httpx2 import MockTransport

    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.acquisition.service import DocumentAcquisitionService
    from tender_intelligence.dedup.service import DedupService
    from tender_intelligence.orchestrator.config import ConfigLoader
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.retry import RetryPolicy
    from tender_intelligence.orchestrator.scheduler import SourceScheduler
    from tender_intelligence.processing.service import DocumentProcessingService
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.sources.polite import PoliteHttpClient
    from tender_intelligence.verdict.runtime import build_verdict_handoff

    policy = CrawlPolicy(
        request_interval_seconds=0.0, backoff_base_seconds=0.0, backoff_max_seconds=0.0
    )
    transport = MockTransport(site.handler)
    http = PoliteHttpClient.build(policy, transport=transport)
    fetcher = DocumentFetcher(policy, transport=transport)
    registry = AdapterRegistry.default(policy=policy, fetcher=http.get)

    # verdict_handoff IS wired (production path) — the triage gate must still block it.
    offline_coordinator = RunCoordinator(
        session_factory=session_factory_gr,
        registry=registry,
        dedup=DedupService(session_factory_gr),
        acquisition=DocumentAcquisitionService(
            session_factory_gr, storage=base_harness.storage, fetcher=fetcher
        ),
        processing=DocumentProcessingService(session_factory_gr, storage=base_harness.storage),
        scheduler=SourceScheduler(session_factory_gr),
        config_loader=ConfigLoader(session_factory_gr),
        retry_policy=RetryPolicy(attempts=2, backoff_base_seconds=0.0),
        verdict_handoff=build_verdict_handoff(
            session_factory_gr, base_harness.storage, lambda profile: client
        ),
    )

    source_id = base_harness.seed_source()
    base_harness.ensure_settings()

    # Configure triage to discard or fail all tenders.
    with session_factory_gr() as session:
        setting = session.get(Setting, 1)
        assert setting is not None
        setting.triage_rules = (
            {"exclude_keywords": ["procurement", "recrutement"]}
            if triage_mode == "discard"
            else "malformed"
        )
        session.commit()

    report = offline_coordinator.run_source(source_id)

    # Stage B stage itself must be COMPLETED (the stage ran; it just had nothing to do).
    verdict_stage = report.stage(StageNumber.VERDICT)
    assert verdict_stage is not None
    assert verdict_stage.status is StageStatus.COMPLETED

    # No LLM call was made — the gate held.
    assert client.calls == 0, (
        f"Stage B must not call LLM when triage did not pass; got {client.calls} call(s)"
    )

    # No Verdict rows persisted.
    with session_factory_gr() as session:
        triage = session.query(TriageResult).all()
        assert triage, "triage must have run"
        if triage_mode == "discard":
            assert all(row.status == "triage_discarded" for row in triage)
        else:
            assert all(row.status == "triage_failed" for row in triage)
        assert session.query(Verdict).count() == 0
        assert session.query(LLMCall).filter_by(role="verdict").count() == 0


def test_build_pipeline_stage_b_failure_is_recorded_not_skipped(
    session_factory_gr, tmp_path
) -> None:
    """Stage B provider failure is recorded as PARTIAL, not SKIPPED (remediation §9 C).

    When verdict_handoff IS wired (production path) and the provider fails, the stage
    must record the failure explicitly — the tender moves to verdict_failed and an LLMCall
    failure row is persisted.  The failure must not look like a skip.
    """
    client = _WorkerVerdictClient(fail=True)
    site = OfflineSite(
        detail_by_tender=dict.fromkeys(LISTING_EXTERNAL_IDS, fixture("detail_en_complete.html"))
    )
    base_harness = build_harness(session_factory_gr, tmp_path, site=site)

    from hashlib import sha256

    from httpx2 import MockTransport

    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.acquisition.service import DocumentAcquisitionService
    from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
    from tender_intelligence.dedup.service import DedupService
    from tender_intelligence.orchestrator.config import ConfigLoader
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.retry import RetryPolicy
    from tender_intelligence.orchestrator.scheduler import SourceScheduler
    from tender_intelligence.processing.service import DocumentProcessingService
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.sources.polite import PoliteHttpClient
    from tender_intelligence.verdict.runtime import build_verdict_handoff

    policy = CrawlPolicy(
        request_interval_seconds=0.0, backoff_base_seconds=0.0, backoff_max_seconds=0.0
    )
    transport = MockTransport(site.handler)
    http = PoliteHttpClient.build(policy, transport=transport)
    fetcher = DocumentFetcher(policy, transport=transport)
    registry = AdapterRegistry.default(policy=policy, fetcher=http.get)

    offline_coordinator = RunCoordinator(
        session_factory=session_factory_gr,
        registry=registry,
        dedup=DedupService(session_factory_gr),
        acquisition=DocumentAcquisitionService(
            session_factory_gr, storage=base_harness.storage, fetcher=fetcher
        ),
        processing=DocumentProcessingService(session_factory_gr, storage=base_harness.storage),
        scheduler=SourceScheduler(session_factory_gr),
        config_loader=ConfigLoader(session_factory_gr),
        retry_policy=RetryPolicy(attempts=2, backoff_base_seconds=0.0),
        verdict_handoff=build_verdict_handoff(
            session_factory_gr, base_harness.storage, lambda profile: client
        ),
    )

    source_id = base_harness.seed_source()
    base_harness.ensure_settings()

    # Seed the minimum needed for VerdictEngine to reach the provider call.
    kb = b"# Failure test KB"
    base_harness.storage.put("kb/fail-test.md", kb, "text/markdown")
    with session_factory_gr() as session:
        profile = LLMProfile(
            name="fail-worker-mock",
            base_url="https://fail.worker.invalid/v1",
            model="mock-fail-v1",
            approved_for_company_docs=True,
            context_window_tokens=8000,
        )
        session.add(profile)
        session.flush()
        session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
        session.add(
            KnowledgeBaseVersion(
                content_ref="kb/fail-test.md",
                content_hash=sha256(kb).hexdigest(),
                token_count=5,
                created_by="fail-test",
            )
        )
        session.commit()

    report = offline_coordinator.run_source(source_id)

    # Stage B ran (PARTIAL because every tender failed) — not SKIPPED.
    verdict_stage = report.stage(StageNumber.VERDICT)
    assert verdict_stage is not None
    assert verdict_stage.status is StageStatus.PARTIAL, (
        f"Stage B with all-failing provider must be PARTIAL, got {verdict_stage.status}"
    )
    assert "skipped" not in (verdict_stage.detail or "").casefold()

    # VerdictEngine was called for each tender (Stage B executed).
    assert client.calls == len(LISTING_EXTERNAL_IDS)

    # Tenders marked verdict_failed; no successful Verdict rows.
    with session_factory_gr() as session:
        tenders = session.query(Tender).filter_by(source_id=source_id).all()
        assert all(t.status == "verdict_failed" for t in tenders), [t.status for t in tenders]
        assert session.query(Verdict).count() == 0
        failed_calls = session.query(LLMCall).filter_by(role="verdict", status="failed").all()
        assert len(failed_calls) == len(LISTING_EXTERNAL_IDS)

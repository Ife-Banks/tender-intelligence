"""Prompt 14 Stage B integration at the persisted model/storage boundary."""

from __future__ import annotations

import hashlib
import json
import uuid

from tender_intelligence.db.models.knowledge import KnowledgeBaseVersion
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.deadline import TenderDeadlineResolution
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.interfaces.llm import LLMClient, LLMError, LLMMessage, LLMResponse, LLMUsage
from tender_intelligence.processing.representation import TenderDocumentBundle
from tender_intelligence.processing.store import ExtractionStore
from tender_intelligence.storage.local import LocalFileSystemStorage
from tender_intelligence.verdict.service import OutcomeStatus, VerdictEngine, format_verdict


class _AssessmentClient(LLMClient):
    profile_name = "test-assessment"

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        supplied = json.loads(messages[1].content)
        deadline = supplied["deadline"]
        content = json.dumps(
            {
                "schema_version": "verdict.v1",
                "background": "Synthetic tender assessment.",
                "requirements": ["technical proposal"],
                "deadline_status": deadline["status"],
                "deadline_utc": deadline["deadline_utc"],
                "deadline_date": deadline["date"],
                "deadline_time": deadline["time"],
                "deadline_timezone": deadline["timezone"],
                "source_timezone": deadline["source_timezone"],
                "assessments": [
                    {
                        "requirement": "technical proposal",
                        "tender_evidence": [],
                        "company_evidence": [],
                        "status": "unverified",
                        "assessment": "No evidence on file for technical proposal.",
                        "gap": "No evidence on file for technical proposal.",
                    }
                ],
                "gaps": ["No evidence on file for technical proposal."],
                "verdict": "APPLY WITH CONDITIONS",
                "confidence": 0.4,
                "urgency": False,
                "incomplete_inputs": supplied["incomplete_inputs"],
                "limitations": [],
            }
        )
        return LLMResponse(content, self.profile_name, "mock-v1", LLMUsage(10, 20, 0.001))


def test_stage_b_persists_verdict_provenance_and_formats_without_sending(
    db_session, tmp_path
) -> None:
    source = Source(
        name="verdict-test-source", source_type="test", base_url="https://example.test", active=True
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id="v-test",
        url="https://example.test/v-test",
        title="Synthetic procurement",
        correlation_id=str(uuid.uuid4()),
        raw_metadata={},
    )
    db_session.add(tender)
    db_session.flush()
    db_session.add(
        TriageResult(
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            status="passed",
            mode="rules",
            reasons=["no_rule_disqualifier"],
        )
    )
    profile = LLMProfile(
        name="verdict-mock",
        base_url="https://mock.example/api",
        model="mock-v1",
        approved_for_company_docs=True,
        context_window_tokens=8000,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
    kb_text = "# Company capabilities\nSynthetic fixture only."
    kb_bytes = kb_text.encode()
    storage = LocalFileSystemStorage(tmp_path)
    storage.put("kb/test.md", kb_bytes, "text/markdown")
    db_session.add(
        KnowledgeBaseVersion(
            content_ref="kb/test.md",
            content_hash=hashlib.sha256(kb_bytes).hexdigest(),
            token_count=20,
            created_by="test",
        )
    )
    ExtractionStore(storage).write_bundle(TenderDocumentBundle(tender_id=tender.id))
    db_session.flush()

    outcome = VerdictEngine(
        db_session, storage, lambda _: _AssessmentClient(), sleeper=lambda _: None
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    persisted = db_session.get(Verdict, outcome.verdict_id)
    assert persisted is not None
    assert persisted.schema_version == "verdict.v1"
    assert persisted.prompt_version == "stage-b.v1"
    assert persisted.knowledge_base_version_id is not None
    assert persisted.map_reduce_used is False
    assert persisted.provider == "mock.example"
    assert db_session.query(LLMCall).filter_by(role="verdict", tender_id=tender.id).count() == 1
    content = format_verdict(persisted, outcome)
    assert content.verdict == "APPLY WITH CONDITIONS"
    assert content.assessment_unavailable is False
    assert "No evidence on file" in content.applicability


def test_unapproved_profile_never_invoked_with_kb(db_session, tmp_path) -> None:
    source = Source(
        name="policy-source", source_type="test", base_url="https://example.test", active=True
    )
    db_session.add(source)
    db_session.flush()
    tender = Tender(
        source_id=source.id,
        external_id="policy",
        url="https://example.test/policy",
        title="Policy fixture",
        correlation_id=str(uuid.uuid4()),
        raw_metadata={},
    )
    db_session.add(tender)
    db_session.flush()
    db_session.add(
        TriageResult(
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            status="passed",
            mode="rules",
            reasons=[],
        )
    )
    profile = LLMProfile(
        name="unapproved-verdict",
        base_url="https://mock.example",
        model="mock",
        approved_for_company_docs=False,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=profile.id))
    calls = []
    outcome = VerdictEngine(
        db_session,
        LocalFileSystemStorage(tmp_path),
        lambda p: calls.append(p) or _AssessmentClient(),
    ).generate(tender.id)
    assert outcome.status is OutcomeStatus.APPROVAL
    assert calls == []
    assert db_session.query(LLMCall).filter_by(role="verdict").count() == 0


class _ScriptedClient(LLMClient):
    profile_name = "scripted"

    def __init__(self, script, observed, profile_id):
        self.script = list(script)
        self.observed = observed
        self.profile_id = profile_id

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        self.observed.append((self.profile_id, messages, max_tokens))
        next_result = self.script.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        if next_result == "VALID":
            next_result = json.dumps(_valid_payload(json.loads(messages[1].content)))
        return LLMResponse(next_result, self.profile_name, f"actual-model-{self.profile_id}", LLMUsage(3, 4, 0.01))


def _valid_payload(supplied):
    deadline = supplied["deadline"]
    limitations = ["One or more tender inputs were unavailable."] if supplied["incomplete_inputs"] else []
    return {
        "schema_version": "verdict.v1", "background": "Synthetic assessment.",
        "requirements": ["technical proposal"], "deadline_status": deadline["status"],
        "deadline_utc": deadline["deadline_utc"], "deadline_date": deadline["date"],
        "deadline_time": deadline["time"], "deadline_timezone": deadline["timezone"],
        "source_timezone": deadline["source_timezone"],
        "assessments": [{"requirement": "technical proposal", "tender_evidence": [],
            "company_evidence": [], "status": "unverified",
            "assessment": "No evidence on file for technical proposal.",
            "gap": "No evidence on file for technical proposal."}],
        "gaps": ["No evidence on file for technical proposal."],
        "verdict": "APPLY WITH CONDITIONS", "confidence": 0.4, "urgency": False,
        "incomplete_inputs": supplied["incomplete_inputs"], "limitations": limitations,
    }


def _environment(db_session, tmp_path, *, triage_status="passed", approved=True,
                 fallback_approved=None, bundle_incomplete=False):
    source = Source(name=f"source-{uuid.uuid4().hex}", source_type="test",
                    base_url="https://source.test", active=True)
    db_session.add(source)
    db_session.flush()
    tender = Tender(source_id=source.id, external_id=uuid.uuid4().hex,
        url="https://source.test/tender", title="Synthetic tender",
        correlation_id=str(uuid.uuid4()), raw_metadata={})
    db_session.add(tender)
    db_session.flush()
    db_session.add(TriageResult(tender_id=tender.id, correlation_id=tender.correlation_id,
        status=triage_status, mode="rules", reasons=[]))
    primary = LLMProfile(name=f"primary-{uuid.uuid4().hex}", base_url="https://primary.test/api",
        model="configured-primary", approved_for_company_docs=approved, context_window_tokens=8000)
    db_session.add(primary)
    db_session.flush()
    fallback = None
    if fallback_approved is not None:
        fallback = LLMProfile(name=f"fallback-{uuid.uuid4().hex}", base_url="https://fallback.test/api",
            model="configured-fallback", approved_for_company_docs=fallback_approved,
            context_window_tokens=8000)
        db_session.add(fallback)
        db_session.flush()
    db_session.add(LLMRoleAssignment(role="verdict", profile_id=primary.id,
        fallback_profile_id=fallback.id if fallback else None))
    kb = f"# Synthetic section\nKB-{uuid.uuid4().hex} capability record."
    raw = kb.encode()
    storage = LocalFileSystemStorage(tmp_path)
    storage.put("kb/current.md", raw, "text/markdown")
    db_session.add(KnowledgeBaseVersion(content_ref="kb/current.md",
        content_hash=hashlib.sha256(raw).hexdigest(), token_count=20, created_by="test"))
    ExtractionStore(storage).write_bundle(TenderDocumentBundle(
        tender_id=tender.id, incomplete_inputs=bundle_incomplete))
    db_session.flush()
    return tender, primary, fallback, storage, kb


def test_triage_discard_and_failure_cannot_enter_stage_b(db_session, tmp_path) -> None:
    for state in ("triage_discarded", "triage_failed"):
        tender, _, _, storage, _ = _environment(db_session, tmp_path, triage_status=state)
        invoked = []
        outcome = VerdictEngine(db_session, storage,
            lambda profile: invoked.append(profile) or _AssessmentClient()).generate(tender.id)
        assert outcome.error_code == "triage_not_passed"
        assert invoked == []
        assert db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").count() == 0
        db_session.rollback()


def test_validation_retry_is_exactly_once_and_records_both_actual_calls(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    observed = []
    client = lambda: _ScriptedClient(["not-json", "not-json"], observed, profile.id)
    outcome = VerdictEngine(db_session, storage, lambda _: client(), sleeper=lambda _: None).generate(tender.id)
    assert outcome.status is OutcomeStatus.FAILED
    assert outcome.error_code == "verdict_invalid_output"
    assert len(observed) == 2
    rows = db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").all()
    assert len(rows) == 2 and all(row.status == "success" for row in rows)
    assert db_session.query(Verdict).filter_by(tender_id=tender.id).count() == 0


def test_transport_retries_are_bounded_then_fallback_and_record_actual_model(db_session, tmp_path) -> None:
    tender, primary, fallback, storage, _ = _environment(
        db_session, tmp_path, approved=True, fallback_approved=True
    )
    observed = []
    scripts = {primary.id: [LLMError("timeout", "timeout")] * 3,
               fallback.id: ["VALID"]}
    def factory(profile):
        return _ScriptedClient(scripts[profile.id], observed, profile.id)
    outcome = VerdictEngine(db_session, storage, factory, sleeper=lambda _: None).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert [call[0] for call in observed] == [primary.id] * 3 + [fallback.id]
    rows = db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").order_by(LLMCall.id).all()
    assert len(rows) == 4
    assert [row.status for row in rows] == ["failed", "failed", "failed", "success"]
    verdict = db_session.get(Verdict, outcome.verdict_id)
    assert verdict is not None and verdict.llm_profile_id == fallback.id
    assert verdict.model == f"actual-model-{fallback.id}"
    assert rows[-1].model == f"actual-model-{fallback.id}"


def test_unapproved_primary_routes_only_to_approved_fallback(db_session, tmp_path) -> None:
    tender, primary, fallback, storage, kb = _environment(
        db_session, tmp_path, approved=False, fallback_approved=True
    )
    observed = []
    outcome = VerdictEngine(db_session, storage,
        lambda profile: _ScriptedClient(["VALID"], observed, profile.id),
        sleeper=lambda _: None).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert [call[0] for call in observed] == [fallback.id]
    sent = json.loads(observed[0][1][1].content)
    assert sent["knowledge_base"] == kb
    assert db_session.query(LLMCall).filter_by(tender_id=tender.id, profile_id=primary.id).count() == 0


def test_budget_gate_prevents_model_call_and_preserves_waiting_status(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path)
    settings = db_session.get(Setting, 1)
    if settings is None:
        settings = Setting(id=1)
        db_session.add(settings)
    settings.monthly_ai_budget = 0.01
    db_session.add(LLMCall(correlation_id=tender.correlation_id, role="triage",
        profile_id=profile.id, est_cost=0.01, status="success"))
    db_session.flush()
    invoked = []
    outcome = VerdictEngine(db_session, storage,
        lambda candidate: invoked.append(candidate), sleeper=lambda _: None).generate(tender.id)
    assert outcome.status is OutcomeStatus.BUDGET
    assert tender.status == "awaiting_budget"
    assert invoked == []
    assert db_session.query(LLMCall).filter_by(tender_id=tender.id, role="verdict").count() == 0


def test_incomplete_bundle_flag_is_forwarded_and_persisted(db_session, tmp_path) -> None:
    tender, profile, _, storage, _ = _environment(db_session, tmp_path, bundle_incomplete=True)
    observed = []
    outcome = VerdictEngine(db_session, storage,
        lambda _: _ScriptedClient(["VALID"], observed, profile.id), sleeper=lambda _: None).generate(tender.id)
    assert outcome.status is OutcomeStatus.AVAILABLE
    assert outcome.incomplete_inputs is True
    sent = json.loads(observed[0][1][1].content)
    assert sent["incomplete_inputs"] is True
    assert db_session.get(Verdict, outcome.verdict_id).incomplete_inputs is True

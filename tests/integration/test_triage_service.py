"""Stage A triage behavior against persisted Settings, LLM roles, and timeline facts."""

from __future__ import annotations

import json
import uuid

import pytest

from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.llm import LLMCall, LLMProfile, LLMRoleAssignment
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.interfaces.llm import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
from tender_intelligence.triage.service import DISCARDED, FAILED, PASS, TriageService


def _tender(session, *, title: str = "Digital health platform", metadata=None) -> Tender:
    source = Source(
        name=f"source-{title}",
        source_type="paginated_html_list",
        base_url="https://source.example",
        active=True,
    )
    session.add(source)
    session.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"notice-{uuid.uuid4().hex}",
        url=f"https://source.example/notice-{uuid.uuid4().hex}",
        title=title,
        raw_metadata=metadata or {},
        correlation_id=str(uuid.uuid4()),
    )
    session.add(tender)
    session.flush()
    return tender


def _settings(session, *, rules=None, threshold=None) -> Setting:
    settings = session.get(Setting, 1)
    assert settings is not None
    settings.triage_rules = rules
    settings.triage_threshold = threshold
    return settings


def test_rule_include_exclude_and_blank_config_pass_through(db_session) -> None:
    tender = _tender(db_session, title="Health data platform")
    _settings(db_session, rules={"include_keywords": ["health"], "exclude_keywords": ["boots"]})
    assert TriageService(db_session).evaluate(tender.id).status == PASS

    excluded = _tender(db_session, title="Army boots procurement")
    assert TriageService(db_session).evaluate(excluded.id).status == DISCARDED

    _settings(db_session, rules=None, threshold=None)
    neutral = _tender(db_session, title="Unclassified public notice")
    decision = TriageService(db_session).evaluate(neutral.id)
    assert decision.status == PASS
    assert decision.reasons == ("no_rule_disqualifier",)
    assert db_session.query(LLMCall).count() == 0


def test_sector_region_and_value_only_filter_when_configured(db_session) -> None:
    tender = _tender(
        db_session,
        metadata={"sector": "health", "region": "West Africa", "contract_value": 120000},
    )
    _settings(
        db_session,
        rules={
            "target_sectors": ["health"],
            "target_regions": ["west africa"],
            "minimum_contract_value": 100000,
        },
    )
    assert TriageService(db_session).evaluate(tender.id).status == PASS
    _settings(db_session, rules={"target_sectors": ["fintech"]})
    assert TriageService(db_session).evaluate(tender.id).status == DISCARDED
    _settings(db_session, rules={})
    assert TriageService(db_session).evaluate(tender.id).status == PASS


def test_rule_pass_does_not_fabricate_a_relevance_score(db_session) -> None:
    tender = _tender(db_session, title="Solar Energy procurement")
    _settings(db_session, rules={"include_keywords": ["solar"]}, threshold=0.75)
    result = TriageService(db_session).evaluate(tender.id)
    assert result.status == PASS
    assert result.score is None


@pytest.mark.parametrize(
    ("score", "expected"), [(0.49, DISCARDED), (0.50, PASS), (0.51, PASS)]
)
def test_model_threshold_boundaries(db_session, score, expected) -> None:
    tender = _tender(db_session)
    _settings(db_session, rules={}, threshold=0.50)
    profile = LLMProfile(name=f"score-{score}", base_url="https://llm.example", model="triage")
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="triage", profile_id=profile.id))

    class ScoreClient(_Client):
        def chat(self, messages, *, max_tokens=None):
            return LLMResponse(
                content=json.dumps({"decision": "pass", "score": score}),
                profile_name=self.profile_name,
                model="triage",
                usage=LLMUsage(),
            )

    assert TriageService(db_session, client_factory=lambda _: ScoreClient()).evaluate(
        tender.id
    ).status == expected


class _Client(LLMClient):
    profile_name = "cheap-model"

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        assert "knowledge" not in messages[1].content.lower()
        return LLMResponse(
            content='{"decision":"pass","score":0.8}',
            profile_name=self.profile_name,
            model="cheap-1",
            usage=LLMUsage(prompt_tokens=12, completion_tokens=4, estimated_cost_usd=0.001),
        )


class _FailingClient(LLMClient):
    profile_name = "broken-model"

    def chat(self, messages: list[LLMMessage], *, max_tokens=None) -> LLMResponse:
        raise RuntimeError("provider unavailable")


def test_model_assignment_records_llm_call_and_timeline_status(db_session) -> None:
    tender = _tender(
        db_session,
        metadata={"sector": "public", "internal_strategy": "never send this"},
    )
    profile = LLMProfile(name="cheap", base_url="https://llm.example", model="cheap-1")
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="triage", profile_id=profile.id))
    client = _Client()
    # Spy on the exact payload crossing the provider boundary.
    original_chat = client.chat

    def checked_chat(messages, *, max_tokens=None):
        assert "internal_strategy" not in messages[1].content
        assert "never send this" not in messages[1].content
        return original_chat(messages, max_tokens=max_tokens)

    client.chat = checked_chat
    decision = TriageService(db_session, client_factory=lambda _: client).evaluate(tender.id)
    db_session.flush()

    assert decision.status == PASS
    call = db_session.query(LLMCall).one()
    assert call.role == "triage"
    assert call.profile_id == profile.id
    assert call.tokens_in == 12
    timeline = TimelineService(db_session).reconstruct_by_tender(tender.id)
    assert timeline is not None
    assert any(event.stage == "triage" and event.status == PASS for event in timeline.events)


def test_model_failure_is_not_recorded_as_discard(db_session) -> None:
    tender = _tender(db_session)
    profile = LLMProfile(name="broken", base_url="https://llm.example", model="cheap-1")
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="triage", profile_id=profile.id))
    service = TriageService(db_session, client_factory=lambda _: _FailingClient())
    decision = service.evaluate(tender.id)
    db_session.flush()

    assert decision.status == FAILED
    assert decision.status != DISCARDED
    assert db_session.query(LLMCall).one().status == "failed"


def test_malformed_rules_persist_triage_failed(db_session) -> None:
    tender = _tender(db_session)
    _settings(db_session, rules={"exclude_keywords": "not-a-list"})
    result = TriageService(db_session).evaluate(tender.id)
    assert result.status == FAILED
    assert result.error_code == "triage_invalid_config"
    assert db_session.query(LLMCall).count() == 0


def test_triage_timeout_retries_then_uses_assigned_fallback(db_session) -> None:
    tender = _tender(db_session)
    _settings(db_session, rules={})
    primary = LLMProfile(name="primary", base_url="https://llm.example", model="primary-model")
    fallback = LLMProfile(name="fallback", base_url="https://llm.example", model="fallback-model")
    db_session.add_all([primary, fallback])
    db_session.flush()
    db_session.add(
        LLMRoleAssignment(
            role="triage", profile_id=primary.id, fallback_profile_id=fallback.id
        )
    )

    class TimeoutClient(_Client):
        def chat(self, messages, *, max_tokens=None):
            raise LLMError("provider timeout", "ai_call_timeout")

    client_calls = []

    def factory(profile):
        client_calls.append(profile.id)
        return TimeoutClient() if profile.id == primary.id else _Client()

    result = TriageService(
        db_session, client_factory=factory, sleeper=lambda _: None
    ).evaluate(tender.id)
    calls = db_session.query(LLMCall).order_by(LLMCall.id).all()
    assert result.status == PASS
    assert result.llm_profile_id == fallback.id
    assert client_calls == [primary.id] * 3 + [fallback.id]
    assert [call.status for call in calls] == ["failed", "failed", "failed", "success"]


def test_threshold_requires_model_score_and_records_failure(db_session) -> None:
    tender = _tender(db_session)
    _settings(db_session, rules={}, threshold=0.5)
    profile = LLMProfile(name="scoreless", base_url="https://llm.example", model="triage")
    db_session.add(profile)
    db_session.flush()
    db_session.add(LLMRoleAssignment(role="triage", profile_id=profile.id))

    class ScorelessClient(_Client):
        def chat(self, messages, *, max_tokens=None):
            return LLMResponse(
                content='{"decision":"pass"}',
                profile_name=self.profile_name,
                model="triage",
                usage=LLMUsage(),
            )

    result = TriageService(db_session, client_factory=lambda _: ScorelessClient()).evaluate(
        tender.id
    )
    assert result.status == FAILED
    assert result.error_code == "triage_missing_score"
    assert db_session.query(LLMCall).one().status == "failed"

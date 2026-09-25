"""Offline behavioral tests for the Prompt 15 Admin API surface."""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from support.pipeline import LISTING_EXTERNAL_IDS, build_harness
from tender_intelligence.admin.auth import Actor
from tender_intelligence.admin.main import app as unconfigured_app
from tender_intelligence.admin.main import create_app
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db.models import (
    ConfigChangeLog,
    LLMCall,
    LLMProfile,
    MailProvider,
    NotificationAttempt,
    NotificationLog,
    RunHistory,
    Setting,
    Source,
    Tender,
    Verdict,
)
from tender_intelligence.interfaces.llm import LLMResponse, LLMUsage
from tender_intelligence.interfaces.source import TenderListing
from tender_intelligence.orchestrator.status import (
    RunStatus,
    StageNumber,
    StageOutcome,
    StageStatus,
)
from tender_intelligence.storage.local import LocalFileSystemStorage


class _Adapter:
    def list_new_tenders(self):
        return [TenderListing("test-1", "Synthetic result", "https://source.test/1")]


class _Registry:
    source_types = ("test_adapter",)

    def build(self, _spec):
        return _Adapter()


class _DryRunCoordinator:
    def __init__(self):
        self.calls = []

    def run_source(self, source_id, *, dry_run=False):
        self.calls.append((source_id, dry_run))
        now = datetime.now(UTC)
        return SimpleNamespace(
            source_id=source_id,
            source_name="synthetic",
            correlation_id="dry-run-correlation",
            status=RunStatus.DRY_RUN,
            dry_run=dry_run,
            run_history_id=None,
            config_version=1,
            stages=(
                StageOutcome(
                    StageNumber.DISCOVERY,
                    StageStatus.COMPLETED,
                    "dry-run-correlation",
                    now,
                    now,
                    item_count=1,
                ),
            ),
            tenders_considered=0,
            planned_actions=("would inspect one candidate",),
            error_code=None,
        )


class _LLMClient:
    def chat(self, messages, *, max_tokens=None):
        assert "KB" not in " ".join(message.content for message in messages)
        assert max_tokens == 16
        return LLMResponse("OK", "offline", "mock-model", LLMUsage(1, 1, None))


@pytest.fixture()
def client(sqlite_engine, session_factory_gr, tmp_path):
    def resolver(_request, authorization):
        if authorization == "Bearer admin-test":
            return Actor("admin@example.test", "admin")
        if authorization == "Bearer viewer-test":
            return Actor("viewer@example.test", "viewer")
        raise HTTPException(401, detail={"code": "authentication_required"})

    app = create_app(
        engine=sqlite_engine,
        sessions=session_factory_gr,
        actor_resolver=resolver,
        object_storage=LocalFileSystemStorage(tmp_path / "objects"),
        adapter_registry=_Registry(),
        run_coordinator=_DryRunCoordinator(),
    )
    with TestClient(app) as test_client:
        yield test_client


def admin(client: TestClient):
    return {"Authorization": "Bearer admin-test"}


def viewer(client: TestClient):
    return {"Authorization": "Bearer viewer-test"}


def test_default_app_fails_closed_and_settings_read_does_not_write(client, session_factory_gr):
    response = unconfigured_app
    with TestClient(response) as unconfigured:
        assert unconfigured.get("/api/v1/settings").status_code == 401
    with session_factory_gr() as session:
        before = session.query(Setting).count()
    settings = client.get("/api/v1/settings", headers=viewer(client))
    assert settings.status_code == 200
    assert settings.json()["test_mode"] is True
    with session_factory_gr() as session:
        assert session.query(Setting).count() == before


def test_test_header_auth_is_local_opt_in_and_disabled_in_production(
    sqlite_engine, session_factory_gr, monkeypatch
):
    monkeypatch.setenv("TI_ADMIN_ENABLE_TEST_AUTH", "true")
    monkeypatch.setenv("TI_ENV", "development")
    get_env_settings.cache_clear()
    app = create_app(engine=sqlite_engine, sessions=session_factory_gr)
    with TestClient(app) as test_client:
        assert test_client.get("/api/v1/sources/supported-types").status_code == 401
        allowed = test_client.get(
            "/api/v1/sources/supported-types",
            headers={"X-Test-Actor": "local-operator", "X-Test-Role": "admin"},
        )
        assert allowed.status_code == 200
    monkeypatch.setenv("TI_ENV", "production")
    get_env_settings.cache_clear()
    app = create_app(engine=sqlite_engine, sessions=session_factory_gr)
    with TestClient(app) as test_client:
        denied = test_client.get(
            "/api/v1/sources/supported-types",
            headers={"X-Test-Actor": "local-operator", "X-Test-Role": "admin"},
        )
        assert denied.status_code == 401
    get_env_settings.cache_clear()


def test_env_alias_precedence_is_explicit(monkeypatch):
    monkeypatch.setenv("TI_ENV", "development")
    monkeypatch.setenv("TI_ENVIRONMENT", "production")
    get_env_settings.cache_clear()
    assert get_env_settings().environment == "development"
    get_env_settings.cache_clear()


@pytest.mark.parametrize("actor", [Actor("", "admin"), Actor("name", "superuser")])
def test_invalid_resolved_actor_is_rejected(client, actor):
    client.app.state.actor_resolver = lambda *_args: actor
    response = client.get("/api/v1/settings")
    assert response.status_code == 401


def test_source_crud_and_dry_run_have_no_pipeline_writes(client, session_factory_gr):
    payload = {
        "name": "synthetic",
        "source_type": "test_adapter",
        "base_url": "https://source.test",
        "listing_url": "https://source.test/list",
    }
    created = client.post("/api/v1/sources", json=payload, headers=admin(client))
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    assert created.json()["auth_configured"] is False
    dry_run = client.post(f"/api/v1/sources/{source_id}/test", headers=admin(client))
    assert dry_run.status_code == 200, dry_run.text
    assert dry_run.json()["dry_run"] is True
    assert dry_run.json()["candidate_count"] == 1
    assert dry_run.json()["persisted"] is False
    assert client.app.state.run_coordinator.calls == [(source_id, True)]
    with session_factory_gr() as session:
        assert session.query(Tender).count() == 0
        assert session.query(RunHistory).count() == 0
        assert session.get(Source, source_id).last_run_at is None
        assert session.query(ConfigChangeLog).count() == 1


def test_real_coordinator_source_dry_run_has_no_persisted_side_effects(
    sqlite_engine, session_factory_gr, tmp_path
):
    harness = build_harness(session_factory_gr, tmp_path)
    source_id = harness.seed_source()
    harness.ensure_settings()
    try:
        direct_report = harness.coordinator.run_source(source_id, dry_run=True)
    except Exception as exc:
        pytest.fail(f"coordinator raised {type(exc).__name__}")
    assert direct_report.status is RunStatus.DRY_RUN

    def resolver(_request, authorization):
        if authorization == "Bearer admin-test":
            return Actor("admin@example.test", "admin")
        raise HTTPException(401, detail={"code": "authentication_required"})

    class NotificationSpy:
        def __init__(self):
            self.calls = 0

        def send_test_email(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("source dry-run must not notify")

    notification_spy = NotificationSpy()

    app = create_app(
        engine=sqlite_engine,
        sessions=session_factory_gr,
        actor_resolver=resolver,
        run_coordinator=harness.coordinator,
        notification_service=notification_spy,
    )
    before = _pipeline_counts(session_factory_gr)
    with TestClient(app) as test_client:
        response = test_client.post(
            f"/api/v1/sources/{source_id}/test", headers={"Authorization": "Bearer admin-test"}
        )
    after = _pipeline_counts(session_factory_gr)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "DRY_RUN"
    assert response.json()["candidate_count"] == len(LISTING_EXTERNAL_IDS)
    assert response.json()["persisted"] is False
    assert response.json()["email_sent"] is False
    assert before == after
    assert harness.site.requests
    assert notification_spy.calls == 0


def _pipeline_counts(session_factory):
    with session_factory() as session:
        return {
            "tenders": session.query(Tender).count(),
            "runs": session.query(RunHistory).count(),
            "verdicts": session.query(Verdict).count(),
            "notification_logs": session.query(NotificationLog).count(),
            "notification_attempts": session.query(NotificationAttempt).count(),
        }


def test_viewer_cannot_write_and_admin_test_mode_off_is_audited(client, session_factory_gr):
    denied = client.put(
        "/api/v1/settings",
        headers=viewer(client),
        json={"test_mode": False, "test_mode_reason": "test"},
    )
    assert denied.status_code == 403
    changed = client.put(
        "/api/v1/settings",
        headers=admin(client),
        json={"test_mode": False, "test_mode_reason": "operator test"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["test_mode"] is False
    with session_factory_gr() as session:
        row = session.get(Setting, 1)
        assert row.test_mode is False
        assert row.test_mode_enabled_by == "admin@example.test"
        audit = session.query(ConfigChangeLog).one()
        assert audit.actor == "admin@example.test"
        assert audit.changed_fields["test_mode"] is False


def test_settings_reject_unknown_and_nested_secret_fields(client, session_factory_gr):
    baseline = client.get("/api/v1/settings", headers=admin(client)).json()
    for payload in (
        {"provider_secret": "TEST_SECRET_DO_NOT_LEAK_938271"},
        {"triage_rules": {"provider_secret": "TEST_SECRET_DO_NOT_LEAK_938271"}},
        {"alert_thresholds": {"provider_secret": "TEST_SECRET_DO_NOT_LEAK_938271"}},
    ):
        response = client.put("/api/v1/settings", headers=admin(client), json=payload)
        assert response.status_code == 422
        assert "TEST_SECRET_DO_NOT_LEAK_938271" not in response.text
    with session_factory_gr() as session:
        assert session.get(Setting, 1).test_mode is baseline["test_mode"]
        assert "TEST_SECRET_DO_NOT_LEAK_938271" not in str(session.query(ConfigChangeLog).all())


def test_viewer_cannot_call_privileged_endpoints(client):
    viewer_only_requests = [
        (
            "post",
            "/api/v1/sources",
            {"name": "x", "source_type": "test_adapter", "base_url": "https://source.test"},
        ),
        ("post", "/api/v1/sources/1/test", None),
        ("put", "/api/v1/triage", {"include_keywords": ["solar"]}),
        (
            "post",
            "/api/v1/knowledge-base/versions",
            {"filename": "x.txt", "content_base64": "eA=="},
        ),
        (
            "post",
            "/api/v1/llm/profiles",
            {"name": "x", "base_url": "https://llm.test", "model": "x"},
        ),
        ("put", "/api/v1/llm/roles/verdict", {"role": "verdict", "profile_id": 1}),
        ("post", "/api/v1/recipients", {"email": "a@example.test", "list_type": "tender"}),
        (
            "post",
            "/api/v1/mail/providers",
            {"name": "x", "provider_type": "sendlib", "from_address": "sender@example.test"},
        ),
        ("post", "/api/v1/mail/test", {"recipient_id": 1}),
        ("get", "/api/v1/audit", None),
    ]
    for method, path, payload in viewer_only_requests:
        call = getattr(client, method)
        response = (
            call(path, headers=viewer(client))
            if payload is None
            else call(path, headers=viewer(client), json=payload)
        )
        assert response.status_code == 403, (method, path, response.text)


def test_profile_secrets_write_only_and_validation_errors_redacted(
    client, session_factory_gr, monkeypatch
):
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    get_env_settings.cache_clear()
    secret = "never-return-this-token"
    payload = {
        "name": "mock-profile",
        "base_url": "https://llm.example.test/v1",
        "model": "mock",
        "api_key": secret,
        "approved_for_company_docs": False,
    }
    response = client.post("/api/v1/llm/profiles", headers=admin(client), json=payload)
    assert response.status_code == 201, response.text
    assert secret not in response.text
    profile_id = response.json()["id"]
    assert response.json()["api_key_configured"] is True
    assert "api_key_encrypted" not in response.json()
    detail = client.get(f"/api/v1/llm/profiles/{profile_id}", headers=admin(client))
    assert secret not in detail.text
    viewer_detail = client.get(f"/api/v1/llm/profiles/{profile_id}", headers=viewer(client))
    assert viewer_detail.status_code == 200
    assert "base_url" not in viewer_detail.json()
    invalid = client.post(
        "/api/v1/llm/profiles",
        headers=admin(client),
        json={**payload, "name": "invalid", "context_window_tokens": 0},
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    with session_factory_gr() as session:
        profile = session.get(LLMProfile, profile_id)
        assert profile.api_key_encrypted != secret
    assert "never-return-this-token" not in str(session.query(ConfigChangeLog).all())


def test_profile_approval_toggle_is_shared_persisted_and_audited(client, session_factory_gr):
    profile = LLMProfile(name="approval-profile", base_url="https://llm.test/v1", model="test")
    with session_factory_gr() as session:
        session.add(profile)
        session.commit()
        profile_id = profile.id
    for approved in (True, False):
        response = client.put(
            f"/api/v1/llm/profiles/{profile_id}",
            headers=admin(client),
            json={
                "name": "approval-profile",
                "base_url": "https://llm.test/v1",
                "model": "test",
                "approved_for_company_docs": approved,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["approved_for_company_docs"] is approved
        with session_factory_gr() as session:
            assert session.get(LLMProfile, profile_id).approved_for_company_docs is approved
    assert (
        client.put(
            f"/api/v1/llm/profiles/{profile_id}",
            headers=viewer(client),
            json={"name": "approval-profile", "base_url": "https://llm.test/v1", "model": "test"},
        ).status_code
        == 403
    )
    with session_factory_gr() as session:
        records = session.query(ConfigChangeLog).filter_by(entity="LLMProfile").all()
        assert len(records) == 2
        assert all(row.actor == "admin@example.test" for row in records)


def test_llm_connection_uses_mock_and_records_actual_invocation(
    client, session_factory_gr, monkeypatch
):
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(b"m" * 32).decode())
    get_env_settings.cache_clear()
    profile = LLMProfile(
        name="connection-profile", base_url="https://llm.test/v1", model="mock", active=True
    )
    with session_factory_gr() as session:
        session.add(profile)
        session.commit()
        profile_id = profile.id
    # create_app has no implicit network client; test harness explicitly injects the fake.
    client.app.state.llm_client_factory = lambda **_kwargs: _LLMClient()
    result = client.post(f"/api/v1/llm/profiles/{profile_id}/test", headers=admin(client))
    assert result.status_code == 200, result.text
    assert "api_key" not in result.text and "authorization" not in result.text.lower()
    with session_factory_gr() as session:
        call = session.query(LLMCall).one()
        assert call.role == "admin_test" and call.status == "success"
        assert call.tokens_in == 1 and call.est_cost is None


def test_llm_connection_factory_absent_fails_truthfully_without_llmcall(client, session_factory_gr):
    profile = LLMProfile(name="no-factory", base_url="https://llm.test/v1", model="test")
    with session_factory_gr() as session:
        session.add(profile)
        session.commit()
        profile_id = profile.id
    response = client.post(f"/api/v1/llm/profiles/{profile_id}/test", headers=admin(client))
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "llm_test_unavailable"
    with session_factory_gr() as session:
        assert session.query(LLMCall).count() == 0


def test_kb_viewer_restriction_and_recipient_last_active_guard(client, session_factory_gr):
    content = b"Synthetic KB section."
    upload = client.post(
        "/api/v1/knowledge-base/versions",
        headers=admin(client),
        json={
            "filename": "capabilities.md",
            "content_base64": base64.b64encode(content).decode(),
            "note": "fixture",
        },
    )
    assert upload.status_code == 201, upload.text
    version_id = upload.json()["id"]
    assert client.get("/api/v1/knowledge-base/versions", headers=viewer(client)).status_code == 403
    assert (
        client.get(
            f"/api/v1/knowledge-base/versions/{version_id}", headers=viewer(client)
        ).status_code
        == 403
    )
    assert (
        client.get(f"/api/v1/knowledge-base/versions/{version_id}", headers=admin(client)).json()[
            "content"
        ]
        == content.decode()
    )
    first = client.post(
        "/api/v1/recipients",
        headers=admin(client),
        json={"email": "dev1@example.test", "list_type": "dev_alert"},
    )
    assert first.status_code == 201
    last_delete = client.delete(f"/api/v1/recipients/{first.json()['id']}", headers=admin(client))
    assert last_delete.status_code == 409
    assert (
        client.post(
            "/api/v1/recipients",
            headers=viewer(client),
            json={"email": "dev2@example.test", "list_type": "dev_alert"},
        ).status_code
        == 403
    )
    invalid_email = client.post(
        "/api/v1/recipients",
        headers=admin(client),
        json={"email": "not an email", "list_type": "tender"},
    )
    assert invalid_email.status_code == 422


def test_adversarial_secret_redaction_across_config_audit_and_errors(
    client, session_factory_gr, monkeypatch, caplog
):
    monkeypatch.setenv("TI_MASTER_KEY", base64.b64encode(b"s" * 32).decode())
    get_env_settings.cache_clear()
    secret = "TEST_SECRET_DO_NOT_LEAK_938271"
    source = client.post(
        "/api/v1/sources",
        headers=admin(client),
        json={
            "name": "secret-source",
            "source_type": "test_adapter",
            "base_url": "https://source.test",
            "auth": {"password": secret},
        },
    )
    assert source.status_code == 201
    assert secret not in source.text
    provider = client.post(
        "/api/v1/mail/providers",
        headers=admin(client),
        json={
            "name": "secret-mail",
            "provider_type": "sendlib",
            "credentials": {"api_key": secret},
            "from_address": "verified@example.test",
        },
    )
    assert provider.status_code == 201, provider.text
    assert secret not in provider.text
    responses = [
        client.get("/api/v1/sources", headers=admin(client)),
        client.get("/api/v1/mail/providers", headers=admin(client)),
        client.get("/api/v1/audit", headers=admin(client)),
        client.get("/health"),
    ]
    assert all(secret not in response.text for response in responses)
    with session_factory_gr() as session:
        stored_source = session.get(Source, source.json()["id"])
        stored_provider = session.get(MailProvider, provider.json()["id"])
        assert secret not in stored_source.auth_encrypted
        assert secret not in stored_provider.credentials_encrypted
        assert secret not in str(session.query(ConfigChangeLog).all())
    assert secret not in caplog.text
    get_env_settings.cache_clear()


def test_test_email_is_dev_only_test_mode_gated_and_audited(client, session_factory_gr):
    recipient = client.post(
        "/api/v1/recipients",
        headers=admin(client),
        json={"email": "devmail@example.test", "list_type": "dev_alert"},
    )
    recipient_id = recipient.json()["id"]

    class FakeNotificationService:
        def send_test_email(
            self, email, subject, text, *, correlation_id, preferred_provider_id=None
        ):
            assert email == "devmail@example.test"
            assert subject.startswith("[TEST]")
            return type(
                "Outcome",
                (),
                {"status": "sent", "provider_used": "fake", "notification_log_id": 99},
            )()

    client.app.state.notification_service = FakeNotificationService()
    client.put(
        "/api/v1/settings",
        headers=admin(client),
        json={"test_mode": False, "test_mode_reason": "exercise test-mail guard"},
    )
    denied = client.post(
        "/api/v1/mail/test", headers=admin(client), json={"recipient_id": recipient_id}
    )
    assert denied.status_code == 409
    client.put("/api/v1/settings", headers=admin(client), json={"test_mode": True})
    sent = client.post(
        "/api/v1/mail/test", headers=admin(client), json={"recipient_id": recipient_id}
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    with session_factory_gr() as session:
        audit = session.query(ConfigChangeLog).filter_by(entity="TestEmail").one()
        assert audit.changed_fields["recipient_id"] == recipient_id


def test_failed_test_email_is_audited_and_returns_safe_gateway_error(client, session_factory_gr):
    recipient = client.post(
        "/api/v1/recipients",
        headers=admin(client),
        json={"email": "devfail@example.test", "list_type": "dev_alert"},
    )

    class FailedNotificationService:
        def send_test_email(self, *_args, **_kwargs):
            return type(
                "Outcome",
                (),
                {
                    "status": "failed",
                    "provider_used": None,
                    "notification_log_id": 101,
                    "error_code": "provider_unavailable",
                },
            )()

    client.app.state.notification_service = FailedNotificationService()
    response = client.post(
        "/api/v1/mail/test", headers=admin(client), json={"recipient_id": recipient.json()["id"]}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "test_email_failed"
    assert "secret" not in response.text.lower()
    with session_factory_gr() as session:
        audit = session.query(ConfigChangeLog).filter_by(entity="TestEmail").one()
        assert audit.changed_fields["status"] == "failed"


def test_timeline_reads_existing_persisted_correlation_events(client, session_factory_gr):
    with session_factory_gr() as session:
        source = Source(
            name="timeline-source",
            source_type="test_adapter",
            base_url="https://source.test",
            active=True,
        )
        session.add(source)
        session.flush()
        tender = Tender(
            source_id=source.id,
            external_id="timeline-1",
            url="https://source.test/1",
            title="Timeline fixture",
            correlation_id=str(uuid.uuid4()),
            raw_metadata={},
        )
        session.add(tender)
        session.commit()
        tender_id = tender.id
        correlation_id = tender.correlation_id
    response = client.get(f"/api/v1/tenders/{tender_id}/timeline", headers=viewer(client))
    assert response.status_code == 200
    assert response.json()["correlation_id"] == correlation_id
    assert [event["stage"] for event in response.json()["events"]] == ["discovery"]


def test_health_dashboard_excludes_admin_test_email_failures(client, session_factory_gr):
    with session_factory_gr() as session:
        session.add_all(
            [
                NotificationLog(
                    dedupe_key="test-mail-failure",
                    notification_kind="test",
                    status="failed",
                ),
                NotificationLog(
                    dedupe_key="tender-mail-failure",
                    notification_kind="new",
                    status="failed",
                ),
                NotificationLog(
                    dedupe_key="test-mail-stuck",
                    notification_kind="test",
                    status="sending",
                    claim_until=None,
                ),
            ]
        )
        session.commit()

    response = client.get("/api/v1/health/dashboard", headers=viewer(client))

    assert response.status_code == 200, response.text
    assert response.json()["notification_failures"]["7d"] == 1
    assert response.json()["stuck_notification_count"] == 0


def test_admin_ui_assets_are_served_while_api_remains_fail_closed(client):
    page = client.get("/admin/")
    assert page.status_code == 200
    assert "Tender Intelligence" in page.text
    assert 'src="/admin/app.mjs"' in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"
    assert client.get("/admin/app.mjs").status_code == 200
    assert client.get("/admin/api.mjs").status_code == 200
    assert client.get("/admin/styles.css").status_code == 200
    protected = client.get("/api/v1/settings")
    assert protected.status_code == 401

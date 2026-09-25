"""Prompt 16B UI-shaped payloads against the real Admin API and migrated test DB."""

from __future__ import annotations

from fastapi import HTTPException
from fastapi.testclient import TestClient

from tender_intelligence.admin.auth import Actor
from tender_intelligence.admin.main import create_app
from tender_intelligence.db.models import ConfigChangeLog, MailProvider, Recipient, Setting, Source
from tender_intelligence.storage.local import LocalFileSystemStorage


class _SourceTypes:
    source_types = ("test_adapter",)

    def build(self, _spec):
        raise AssertionError("this test does not invoke a source adapter")


def test_ui_payload_shapes_persist_config_and_audit_without_external_services(
    sqlite_engine, session_factory_gr, tmp_path
):
    def resolver(_request, authorization):
        if authorization == "Bearer ui-admin-test":
            return Actor("ui-admin@example.test", "admin")
        if authorization == "Bearer ui-viewer-test":
            return Actor("ui-viewer@example.test", "viewer")
        raise HTTPException(401, detail={"code": "authentication_required"})

    app = create_app(
        engine=sqlite_engine,
        sessions=session_factory_gr,
        actor_resolver=resolver,
        object_storage=LocalFileSystemStorage(tmp_path / "objects"),
        adapter_registry=_SourceTypes(),
    )
    headers = {"Authorization": "Bearer ui-admin-test"}
    with TestClient(app) as client:
        # Mirrors Settings screen: server response, not local optimistic state, is authoritative.
        initial = client.get("/api/v1/settings", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["test_mode"] is True
        saved_settings = client.put(
            "/api/v1/settings",
            headers=headers,
            json={"monthly_ai_budget": 0, "retention_months": 12, "link_expiry_days": 30},
        )
        assert saved_settings.status_code == 200, saved_settings.text
        assert saved_settings.json()["monthly_ai_budget"] == 0

        # Mirrors Triage screen with unset/empty/zero values preserved as distinct values.
        triage = client.put(
            "/api/v1/triage",
            headers=headers,
            json={
                "include_keywords": [],
                "exclude_keywords": ["excluded synthetic term"],
                "sectors": [],
                "regions": None,
                "minimum_contract_value": 0,
                "relevance_threshold": 0,
                "urgency_window_days": 0,
            },
        )
        assert triage.status_code == 200, triage.text
        assert triage.json()["include_keywords"] == []
        assert triage.json()["regions"] is None
        assert triage.json()["minimum_contract_value"] == 0
        assert triage.json()["urgency_window_days"] == 0

        # Mirrors Add Source and source editor writable DTO (no response-only fields).
        source_payload = {
            "name": "Prompt 16B UI fixture",
            "source_type": "test_adapter",
            "base_url": "https://source.test",
            "listing_url": "https://source.test/list",
            "expected_languages": ["en"],
            "recipient_scope": [],
            "parser_config": {"fixture": True},
            "active": True,
        }
        source = client.post("/api/v1/sources", headers=headers, json=source_payload)
        assert source.status_code == 201, source.text
        source_id = source.json()["id"]
        edited_source = client.put(
            f"/api/v1/sources/{source_id}",
            headers=headers,
            json={**source_payload, "name": "Prompt 16B UI fixture edited"},
        )
        assert edited_source.status_code == 200, edited_source.text
        active = client.patch(
            f"/api/v1/sources/{source_id}/active", headers=headers, json={"active": False}
        )
        assert active.status_code == 200 and active.json()["active"] is False

        # Mirrors recipient editor form, including only writable fields.
        recipient_payload = {
            "email": "ui-test@example.test",
            "name": "UI test recipient",
            "role": None,
            "list_type": "dev_alert",
            "delivery": "to",
            "source_scope": None,
            "receives_filter": "all",
            "alert_types": None,
            "min_severity": "critical",
            "active": True,
        }
        recipient = client.post("/api/v1/recipients", headers=headers, json=recipient_payload)
        assert recipient.status_code == 201, recipient.text
        changed_recipient = client.put(
            f"/api/v1/recipients/{recipient.json()['id']}",
            headers=headers,
            json={**recipient_payload, "name": "Updated UI recipient"},
        )
        assert changed_recipient.status_code == 200, changed_recipient.text
        assert changed_recipient.json()["name"] == "Updated UI recipient"

        # Mirrors provider form. Never contacts the configured provider.
        provider = client.post(
            "/api/v1/mail/providers",
            headers=headers,
            json={
                "name": "Prompt 16B fixture provider",
                "provider_type": "sendlib",
                "from_address": "verified@example.test",
                "from_name": "Test sender",
                "reply_to": None,
                "priority": 1,
                "active": True,
                "capabilities": {"html": True},
            },
        )
        assert provider.status_code == 201, provider.text
        assert provider.json()["id"]

    with session_factory_gr() as session:
        assert session.get(Setting, 1).monthly_ai_budget == 0
        assert session.get(Source, source_id).active is False
        saved_recipient = session.query(Recipient).filter_by(email="ui-test@example.test").one()
        assert saved_recipient.name == "Updated UI recipient"
        saved_provider = session.query(MailProvider).filter_by(
            name="Prompt 16B fixture provider"
        ).one()
        assert saved_provider.provider_type == "sendlib"
        audited_entities = {row.entity for row in session.query(ConfigChangeLog).all()}
        assert {"Setting", "Source", "Recipient", "MailProvider"} <= audited_entities

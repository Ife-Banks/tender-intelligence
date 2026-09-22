"""Integration tests for the Phase 0 admin API (src/tender_intelligence/admin)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tender_intelligence.admin.main import app
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.health import APP_NAME


@pytest.fixture()
def admin_env(monkeypatch, tmp_path):
    """Point the admin API at a reachable in-memory SQLite DB (default is Postgres)."""
    get_env_settings.cache_clear()
    monkeypatch.setenv("TI_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("TI_STORAGE_DIR", str(tmp_path / "docs"))
    yield


def test_health_ok_with_db(admin_env):
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app"] == APP_NAME
    assert body["status"] == "ok"
    assert body["database"] == "ok"

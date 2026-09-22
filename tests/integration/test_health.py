"""Unit + integration tests for the health module (src/tender_intelligence/health.py)."""

from __future__ import annotations

from sqlalchemy import text

from tender_intelligence.db.engine import build_engine
from tender_intelligence.health import build_health, check_database


class TestHealth:
    def test_database_ok(self, sqlite_engine):
        assert check_database(sqlite_engine) == "ok"

    def test_database_unreachable(self):
        engine = build_engine(
            "sqlite:///C:/definitely/nonexistent/dir/tender_health_check.db"
        )
        assert check_database(engine) == "unreachable"

    def test_build_health_db_ok(self, sqlite_engine):
        health = build_health(sqlite_engine)
        d = health.to_dict()
        assert d["status"] == "ok"
        assert d["database"] == "ok"
        assert d["app"] == "tender-intelligence"

    def test_build_health_no_engine(self):
        health = build_health(None)
        assert health.database == "degraded"
        assert health.status == "degraded"

    def test_query_is_harmless(self, sqlite_engine):
        with sqlite_engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1

"""Integration tests: Alembic migration applies cleanly on SQLite and the seeded row
matches the model shapes (docs/03)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect, text


class TestMigration:
    def test_all_confirmed_entities_created(self, sqlite_engine):
        tables = set(inspect(sqlite_engine).get_table_names())
        expected = {
            "sources",
            "tenders",
            "documents",
            "knowledge_base_versions",
            "verdicts",
            "llm_profiles",
            "llm_role_assignments",
            "llm_calls",
            "mail_providers",
            "notification_logs",
            "notification_attempts",
            "recipients",
            "alert_events",
            "run_history",
            "settings_singleton",
            "config_change_log",
            "admin_users",
        }
        assert expected <= tables

    def test_settings_singleton_seeded_row(self, sqlite_engine):
        with sqlite_engine.begin() as conn:
            row = conn.execute(
                text("select id, test_mode, link_expiry_days from settings_singleton")
            ).fetchone()
        assert row is not None
        assert row.id == 1
        assert row.test_mode == 1  # Test Mode ON by default
        assert row.link_expiry_days == 14

    def test_downgrade_upgrade_roundtrip(self, tmp_path):
        import os

        db_path = tmp_path / "mig.db"
        cfg = AlembicConfig()
        cfg.set_main_option(
            "script_location", os.path.join(os.path.dirname(__file__), "..", "..", "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")
        assert db_path.exists()
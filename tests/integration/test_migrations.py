"""Integration tests: Alembic migration applies cleanly on SQLite and the seeded row
matches the model shapes (docs/03)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text


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
        assert "mail_provider_usage" in tables
        assert "triage_results" in tables
        columns = {
            column["name"] for column in inspect(sqlite_engine).get_columns("triage_results")
        }
        assert "run_id" in columns

    def test_settings_singleton_seeded_row(self, sqlite_engine):
        with sqlite_engine.begin() as conn:
            row = conn.execute(
                text("select id, test_mode, link_expiry_days from settings_singleton")
            ).fetchone()
        assert row is not None
        assert row.id == 1
        assert row.test_mode == 1  # Test Mode ON by default
        assert row.link_expiry_days == 14

    def test_legacy_duplicate_dedupe_rows_are_archived_before_unique_index(self, tmp_path):
        import os

        db_path = tmp_path / "duplicate-dedupe.db"
        cfg = AlembicConfig()
        cfg.set_main_option(
            "script_location", os.path.join(os.path.dirname(__file__), "..", "..", "migrations")
        )
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "0004_run_history_stage_state")
        engine = create_engine(f"sqlite:///{db_path}")
        now = "2026-09-24T00:00:00+00:00"
        with engine.begin() as conn:
            for _ in range(2):
                conn.execute(
                    text(
                        "INSERT INTO notification_logs "
                        "(status, dedupe_key, possible_duplicate, created_at, updated_at) "
                        "VALUES ('sent', :key, 0, :now, :now)"
                    ),
                    {"key": "legacy-dedupe", "now": now},
                )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT dedupe_key FROM notification_logs ORDER BY id")
            ).scalars().all()
            indexes = inspect(engine).get_indexes("notification_logs")
        engine.dispose()
        assert len(rows) == 2
        assert len(set(rows)) == 2
        assert "legacy-dedupe" in rows
        assert any("legacy-duplicate:" in key for key in rows)
        assert any(index.get("unique") for index in indexes)

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

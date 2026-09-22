"""Integration tests for the runtime-config loader (docs/04 §4.1, PROJECT_RULES #20)."""

from __future__ import annotations

from sqlalchemy import select

from tender_intelligence.config.loader import load_runtime_config
from tender_intelligence.config.models import RuntimeConfig
from tender_intelligence.config.seed import ensure_settings_row
from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.config import ConfigChangeLog, Setting


class TestRuntimeConfig:
    def test_defaults_when_no_row(self, db_session):
        config = load_runtime_config(db_session)
        assert isinstance(config, RuntimeConfig)
        assert config.test_mode is True

    def test_reads_row_and_mirrors_changes(self, db_session):
        ensure_settings_row(db_session)
        db_session.commit()
        first = load_runtime_config(db_session)
        assert first.test_mode is True

        row = db_session.get(Setting, 1)
        row.test_mode = False
        row.link_expiry_days = 30 if hasattr(row, "link_expiry_days") else None
        db_session.commit()

        second = load_runtime_config(db_session)
        assert second.test_mode is False
        assert second.row_version >= first.row_version

    def test_change_log_never_carries_secrets(self, db_session):
        """log_config_change must redact secret-bearing keys before writing."""
        ensure_settings_row(db_session)
        log_config_change(
            db_session,
            actor="admin",
            entity="Setting",
            entity_id=1,
            changed_fields={"test_mode": True, "api_key": "SHOULD-NOT-SURVIVE"},
        )
        db_session.commit()
        log = db_session.scalar(select(ConfigChangeLog).order_by(ConfigChangeLog.id.desc()))
        assert log is not None
        assert log.changed_fields == {"test_mode": True, "api_key": "[REDACTED]"}

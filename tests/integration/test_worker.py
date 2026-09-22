"""Integration/test for the worker startup spine and lifecycle.

Phase 0 worker = bootstrap spine only (docs/04 pipeline stages arrive later). ``main`` is
driven the way an operator invokes it: an empty migrated DB, an optional seed YAML and a
dev-alert email from env. It must bootstrap cleanly, seed recipients/settings, and exit 0.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import select
from sqlalchemy.pool import StaticPool

from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.db.models.recipients import Recipient
from tender_intelligence.worker.main import main


@pytest.fixture()
def migrated_db(tmp_path):
    """File-backed SQLite with the 0001 migration applied (matches conftest approach)."""
    db_file = tmp_path / "worker.db"
    url = f"sqlite:///{db_file.as_posix()}"
    cfg = AlembicConfig()
    cfg.set_main_option(
        "script_location",
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "migrations",
        ),
    )
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    return url


def test_worker_bootstraps_empty_db(migrated_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TI_DATABASE_URL", migrated_db)
    monkeypatch.setenv("TI_DEV_ALERT_EMAIL", "dev@opex.example")
    monkeypatch.setenv("TI_MASTER_KEY", "AA==")  # placeholder, unused by bootstrap
    monkeypatch.setenv("TI_STORAGE_DIR", str(tmp_path / "docs"))
    from tender_intelligence.config.settings import get_env_settings

    get_env_settings.cache_clear()

    yaml_file = tmp_path / "seed.yml"
    yaml_file.write_text(
        "dev_alert_email: dev@opex.example\n"
        "sources:\n"
        "  - name: WAHO\n"
        "    type: html\n"
        "    base_url: https://afro.who.int/programmes\n",
        encoding="utf-8",
    )

    assert main([str(yaml_file)]) == 0

    engine = build_engine(migrated_db, poolclass=StaticPool)
    try:
        maker = session_factory(engine)
        with maker() as session:
            rows = session.scalars(select(Recipient)).all()
            assert any(r.email == "dev@opex.example" for r in rows)
    finally:
        engine.dispose()

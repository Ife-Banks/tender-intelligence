""":mod:`tender_intelligence.config.loader` — build a fresh RuntimeConfig from the DB."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Connection

from tender_intelligence.config.models import RuntimeConfig
from tender_intelligence.db.models.config import Setting


def load_runtime_config(conn: Connection) -> RuntimeConfig:
    """Load the runtime config from the ``settings`` singleton row.

    The singleton row always exists with ``id == 1`` (seeded by migration). If it is absent
    (e.g. empty database), the configuration defaults are returned — never fail the worker
    start on a missing row.
    """
    row = conn.execute(select(Setting)).scalar_one_or_none()
    if row is None:
        return RuntimeConfig()

    return RuntimeConfig(
        test_mode=bool(row.test_mode),
        test_mode_reason=row.test_mode_reason or "",
        test_mode_enabled_at=row.test_mode_enabled_at.isoformat() if row.test_mode_enabled_at else None,
        test_mode_enabled_by=row.test_mode_enabled_by,
        config_changed_at=row.config_changed_at.isoformat() if row.config_changed_at else None,
        row_version=int(row.version or 0),
    )
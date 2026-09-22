""":mod:`tender_intelligence.health` — health checks for the admin API and worker.

Distinguishes the process/app level (always available when the process runs) from data
dependency level (database reachability). Follows docs/02 §2.3 (health dashboard) without
over-building; worker operation is independent of the admin API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

APP_NAME = "tender-intelligence"


@dataclass(frozen=True)
class HealthStatus:
    """Computed health payload for the ``/health`` endpoint."""

    status: str  # "ok" | "degraded" | "unreachable"
    app: str = APP_NAME
    database: str = "ok"  # "ok" | "degraded" | "unreachable"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: str = "0.1.0"

    def to_dict(self) -> dict:
        return asdict(self)


def check_database(engine: Engine) -> str:
    """Return ``"ok"`` when the DB answers ``SELECT 1``, else ``"unreachable"``."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return "ok"
    except SQLAlchemyError:
        return "unreachable"


def build_health(engine: Engine | None) -> HealthStatus:
    """Assemble the health payload.

    With no engine (admin API not wired to a DB yet) the database is reported as
    ``"degraded"`` so the dashboard shows the app as not fully healthy rather than
    falsely green.
    """
    database = check_database(engine) if engine is not None else "degraded"
    statuses = {
        "ok": "ok",
        "degraded": "degraded",
        "unreachable": "unreachable",
    }
    status = "ok" if database == "ok" else statuses[database]
    return HealthStatus(status=status, database=database)

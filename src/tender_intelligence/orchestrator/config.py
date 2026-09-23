"""Per-run configuration snapshot (prompt 10 §3).

Prompt 10 §3 requires the worker to **re-read** configuration at the start of every source
run, never to hold a process-wide cached copy, and to record which configuration version a
run executed under. A run that started before an operator flipped a setting must finish
under the settings it started with — not under a mix.

:class:`ConfigLoader` therefore opens its own short-lived session per call and returns an
immutable snapshot. There is no cache here by design: a cached loader is precisely the
defect §3 names.

Only non-secret values are captured. ``Setting`` holds no secrets today, and the snapshot is
built from :class:`~tender_intelligence.config.models.RuntimeConfig`, whose fields are all
operational flags. If that model ever gains a secret field, it must not be added here — the
snapshot is persisted onto ``RunHistory`` and returned in :class:`RunReport` (prompt 10 §3).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.config.loader import load_runtime_config
from tender_intelligence.config.models import RuntimeConfig
from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.orchestrator.errors import ConfigurationLoadError

#: Recorded instead of a version when the settings row does not exist yet.
UNSEEDED_CONFIG_VERSION = 0


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """Immutable view of the configuration a single run executes under (prompt 10 §3)."""

    version: int
    test_mode: bool
    test_mode_reason: str
    changed_at: datetime | None

    @property
    def is_seeded(self) -> bool:
        return self.version > UNSEEDED_CONFIG_VERSION


class ConfigLoader:
    """Reads the current configuration, afresh, once per run (prompt 10 §3)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._maker = session_factory

    def load(self) -> RuntimeConfigSnapshot:
        """Return the configuration as of *now*.

        Raises :class:`ConfigurationLoadError` rather than returning a default: a run whose
        configuration could not be read must fail visibly, never proceed on invented values
        (prompt 10 §9).
        """
        try:
            with self._maker() as session:
                runtime = load_runtime_config(session)
        except SQLAlchemyError as exc:
            raise ConfigurationLoadError(
                f"configuration could not be read: {type(exc).__name__}",
                context={"correlation_id": get_correlation_id()},
            ) from exc
        return snapshot_of(runtime)


def snapshot_of(runtime: RuntimeConfig) -> RuntimeConfigSnapshot:
    """Project a :class:`RuntimeConfig` onto the snapshot the run records."""
    return RuntimeConfigSnapshot(
        version=int(runtime.row_version or UNSEEDED_CONFIG_VERSION),
        test_mode=bool(runtime.test_mode),
        test_mode_reason=str(runtime.test_mode_reason or ""),
        changed_at=_parse_iso(runtime.config_changed_at),
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

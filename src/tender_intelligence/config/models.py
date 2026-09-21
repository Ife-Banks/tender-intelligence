""":mod:`tender_intelligence.config.models` — runtime-config value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tender_intelligence.core.errors import is_valid_error_code  # noqa: F401


@dataclass(frozen=True)
class RuntimeConfig:
    """Point-in-time view of the runtime-managed configuration.

    Built fresh from the database at the start of every worker run (PROJECT_RULES #20).
    Mirrors the ``Settings`` row semantics without leaking model internals.
    """

    test_mode: bool = True
    test_mode_reason: str = "seeded default: test mode on"
    test_mode_enabled_at: str | None = None
    test_mode_enabled_by: str | None = None
    config_changed_at: str | None = None
    row_version: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def with_value(self, **changes: Any) -> "RuntimeConfig":
        """Return a copy applied with ``changes`` (mutations are not allowed on frozen)."""
        return RuntimeConfig(**{**self.__dict__, **changes})  # type: ignore[arg-type]
"""Configuration-change audit trail with recursive secret redaction (docs/10 §10.1)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tender_intelligence.db.models.config import ConfigChangeLog

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "auth",
        "credential",
        "credentials",
        "private_key",
        "access_token",
        "refresh_token",
    }
)
_REDACTED = "[REDACTED]"


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_")
    return normalized in _SECRET_KEYS or any(
        normalized.endswith(f"_{name}") or normalized.startswith(f"{name}_")
        for name in _SECRET_KEYS
    )


def _scrub_changes(changed: Any) -> Any:
    """Recursively copy *changed*, replacing secret-bearing values with a marker."""

    if isinstance(changed, dict):
        return {
            key: (_REDACTED if _is_secret_key(key) else _scrub_changes(value))
            for key, value in changed.items()
        }
    if isinstance(changed, list):
        return [_scrub_changes(value) for value in changed]
    if isinstance(changed, tuple):
        return tuple(_scrub_changes(value) for value in changed)
    return changed


def log_config_change(
    session: Session,
    *,
    actor: str,
    entity: str,
    entity_id: int | None = None,
    changed_fields: dict[str, Any] | None = None,
) -> ConfigChangeLog:
    """Record a configuration change without ever copying a secret value."""

    entry = ConfigChangeLog(
        actor=actor,
        entity=entity,
        entity_id=entity_id,
        changed_fields=_scrub_changes(changed_fields),
    )
    session.add(entry)
    session.flush()
    return entry


__all__ = ["log_config_change"]

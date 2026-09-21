""":mod:`tender_intelligence.db.audit` — configuration-change audit trail (docs/03 §3.2).

ConfigChangeLog must NEVER store secret values (docs/10 security-spec). This helper scrubs
known secret-like keys before writing, so a buggy caller cannot leak a key into the audit
trail by accident.
"""

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
    }
)
_REDACTED = "[REDACTED]"


def _scrub_changes(changed: dict[str, Any] | None) -> dict[str, Any] | None:
    if not changed:
        return changed
    return {
        k: (_REDACTED if k.lower() in _SECRET_KEYS else v) for k, v in changed.items()
    }


def log_config_change(
    session: Session,
    *,
    actor: str,
    entity: str,
    entity_id: int | None = None,
    changed_fields: dict[str, Any] | None = None,
) -> ConfigChangeLog:
    """Record a configuration change with secret-bearing fields redacted."""
    entry = ConfigChangeLog(
        actor=actor,
        entity=entity,
        entity_id=entity_id,
        changed_fields=_scrub_changes(changed_fields),
    )
    session.add(entry)
    session.flush()
    return entry
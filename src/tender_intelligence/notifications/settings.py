"""Guarded Test Mode configuration changes.

The database setting remains ON by default.  A caller that wants to turn it off must provide
an actor and a reason; this is a small safety seam for the later admin API, not a production
go-live switch.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.config import Setting


class TestModeChangeError(ValueError):
    """A Test Mode transition lacked required audit context or was invalid."""


class TestModeSettings:
    """Audited setter for the singleton Test Mode switch."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def set(
        self,
        enabled: bool,
        *,
        actor: str,
        reason: str,
    ) -> Setting:
        actor = actor.strip()
        reason = reason.strip()
        if not actor or not reason:
            raise TestModeChangeError("actor and reason are required for a Test Mode change")
        row = self.session.get(Setting, 1)
        if row is None:
            row = Setting.seed_default()
            self.session.add(row)
            self.session.flush()
        previous = bool(row.test_mode)
        if previous == bool(enabled):
            return row
        row.test_mode = bool(enabled)
        row.test_mode_reason = reason
        row.test_mode_enabled_at = datetime.now(UTC)
        row.test_mode_enabled_by = actor
        row.version = int(row.version or 0) + 1
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="Setting",
            entity_id=1,
            changed_fields={
                "test_mode": bool(enabled),
                "reason": reason,
                "version": row.version,
            },
        )
        return row


__all__ = ["TestModeChangeError", "TestModeSettings"]

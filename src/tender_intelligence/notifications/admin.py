""":mod:`tender_intelligence.notifications.admin` — recipient configuration invariants.

Prompt 11 §13: the system must refuse to deactivate **or** delete the last active dev
recipient — a silent alert path is the failure this system exists to prevent (docs/08 §8.5).
The guard already exists as a query helper; this module makes it a service-layer rule any
future admin API (prompts 15/16) must go through, so the invariant cannot be bypassed by a
call that forgets to check.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.recipients import (
    RECIPIENT_DELIVERY,
    RECIPIENT_LIST_TYPES,
    Recipient,
    is_valid_email,
)
from tender_intelligence.notifications.recipients import RecipientGuard


class RecipientAdminError(Exception):
    """A configuration operation was refused because it would break a recipient invariant."""


class RecipientAdmin:
    """Recipient add/deactivate/delete with the last-active-dev-recipient rule enforced.

    The audit trail is written for every change (v1.1 §5.10.1); secret-bearing fields are
    scrubbed by :func:`log_config_change`, though recipients never carry secrets.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.guard = RecipientGuard(session)

    def add(
        self,
        *,
        email: str,
        list_type: str,
        delivery: str = "to",
        name: str | None = None,
        role: str | None = None,
        source_scope: list[str] | None = None,
        receives_filter: str = "all",
        alert_types: list[str] | None = None,
        min_severity: str = "critical",
        active: bool = True,
        recipient_scope: list[str] | None = None,
        receives: str | None = None,
        actor: str = "admin",
    ) -> Recipient:
        """Add a recipient to either list; email validity is checked at entry (§13)."""
        if not is_valid_email(email):
            raise RecipientAdminError(f"invalid email address: {email!r}")
        if list_type not in RECIPIENT_LIST_TYPES:
            raise RecipientAdminError(f"unknown recipient list: {list_type!r}")
        if delivery not in RECIPIENT_DELIVERY:
            raise RecipientAdminError(f"unknown delivery: {delivery!r}")
        if receives is not None:
            receives_filter = receives
        if receives_filter not in {"all", "apply", "urgent"}:
            raise RecipientAdminError(f"unknown receives filter: {receives_filter!r}")
        if recipient_scope is not None:
            source_scope = recipient_scope
        if list_type == "dev_alert" and not active and self.guard.active_dev_count() == 0:
            raise RecipientAdminError(
                "the first development recipient must be active"
            )
        recipient = Recipient(
            email=email,
            name=name,
            role=role,
            list_type=list_type,
            delivery=delivery,
            source_scope=source_scope,
            receives_filter=receives_filter,
            alert_types=alert_types,
            min_severity=min_severity,
            active=bool(active),
        )
        self.session.add(recipient)
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="recipient",
            entity_id=recipient.id,
            changed_fields={"action": "added", "email": email, "list_type": list_type},
        )
        return recipient

    def deactivate(self, recipient_id: int, *, actor: str = "admin") -> Recipient:
        """Deactivate a recipient; refused when it is the last active dev recipient."""
        recipient = self._get(recipient_id)
        if self._would_silence_dev_alerts(recipient):
            raise RecipientAdminError(
                "cannot deactivate the last active dev recipient — add another dev recipient first"
            )
        recipient.active = False
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="recipient",
            entity_id=recipient.id,
            changed_fields={"action": "deactivated", "email": recipient.email},
        )
        return recipient

    def delete(self, recipient_id: int, *, actor: str = "admin") -> None:
        """Delete a recipient; refused when it is the last active dev recipient."""
        recipient = self._get(recipient_id)
        if self._would_silence_dev_alerts(recipient):
            raise RecipientAdminError(
                "cannot delete the last active dev recipient — add another dev recipient first"
            )
        recipient_id = recipient.id
        snapshot: dict[str, Any] = {
            "action": "deleted",
            "email": recipient.email,
            "list_type": recipient.list_type,
        }
        self.session.delete(recipient)
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="recipient",
            entity_id=recipient_id,
            changed_fields=snapshot,
        )

    def _get(self, recipient_id: int) -> Recipient:
        recipient = self.session.get(Recipient, recipient_id)
        if recipient is None:
            raise RecipientAdminError(f"recipient not found: {recipient_id}")
        return recipient

    def _would_silence_dev_alerts(self, recipient: Recipient) -> bool:
        """True only for an operation that would leave the dev alert path with nobody on it."""
        if recipient.list_type != "dev_alert":
            return False
        return self.guard.refuse_last_dev_removal()


def active_recipients(session: Session, *, list_type: str) -> list[Recipient]:
    """Read one configured list (dev_alert or tender), ordered by id (docs/08 §8.5, §8.6)."""
    return list(
        session.scalars(
            select(Recipient)
            .where(Recipient.list_type == list_type, Recipient.active.is_(True))
            .order_by(Recipient.id)
        ).all()
    )


__all__ = [
    "RecipientAdmin",
    "RecipientAdminError",
    "active_recipients",
]

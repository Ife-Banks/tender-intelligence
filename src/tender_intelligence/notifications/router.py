"""Recipient resolution and the Test Mode safety boundary (docs/08 §8.4–§8.6).

Routing is deliberately separate from persistence and transport.  The service calls this
module before composing a message and calls the safety assertion again before every send,
including an outbox flush.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.recipients import Recipient

APPLY_VERDICTS = frozenset({"APPLY", "APPLY WITH CONDITIONS"})
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


class RoutingError(Exception):
    """Raised when a safe recipient set cannot be resolved."""


@dataclass(frozen=True)
class RecipientSelection:
    """Resolved addresses, split by delivery, plus the send-time snapshot."""

    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    snapshot: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def addresses(self) -> tuple[str, ...]:
        return (*self.to, *self.cc, *self.bcc)

    @property
    def list_types(self) -> frozenset[str]:
        return frozenset(str(item.get("list_type", "")) for item in self.snapshot)

    @property
    def recipient_set_hash(self) -> str:
        """Stable hash of the actual recipient set, independent of database row order."""

        canonical = sorted(
            (
                str(item.get("email", "")).strip().lower(),
                str(item.get("delivery", "to")).lower(),
                str(item.get("list_type", "")),
            )
            for item in self.snapshot
        )
        raw = "\n".join("|".join(item) for item in canonical)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RecipientRouter:
    """Resolve one of the two configured recipient lists.

    Test Mode ON selects **only** ``dev_alert`` recipients.  Test Mode OFF selects only the
    configured business ``tender`` list.  The lists are never mixed; the dev list is a safety
    route for tender mail while Test Mode is on, not an additional business subscriber.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(
        self,
        *,
        source_name: str,
        recommendation: str,
        urgency_flag: bool,
        test_mode: bool,
        source_scope: Iterable[str] | None = None,
    ) -> RecipientSelection:
        source_names = {source_name, *(str(item) for item in (source_scope or ()))}
        list_type: str | Callable[[Recipient], str]
        if test_mode:
            recipients = self._active(list_type="dev_alert")
            list_type = "dev_alert"
        else:
            recipients = [
                recipient
                for recipient in self._active(list_type="tender")
                if self._in_scope(recipient, source_names)
                and self._receives(recipient, recommendation, urgency_flag)
            ]
            list_type = "tender"
        return self._selection(recipients, list_type=list_type)

    def resolve_alert(
        self,
        *,
        alert_type: str,
        severity: str = "critical",
        source_name: str = "",
    ) -> RecipientSelection:
        """Resolve dev recipients for a system alert using their configured filters."""

        threshold = SEVERITY_ORDER.get(severity, SEVERITY_ORDER["critical"])
        selected: list[Recipient] = []
        for recipient in self._active(list_type="dev_alert"):
            allowed_types = recipient.alert_types or []
            if allowed_types and alert_type not in allowed_types:
                continue
            recipient_severity = SEVERITY_ORDER.get(recipient.min_severity or "info", 0)
            if recipient_severity < threshold:
                continue
            selected.append(recipient)
        return self._selection(selected, list_type="dev_alert")

    def _selection(
        self,
        recipients: list[Recipient],
        *,
        list_type: str | Callable[[Recipient], str],
    ) -> RecipientSelection:
        by_delivery: dict[str, list[str]] = {"to": [], "cc": [], "bcc": []}
        snapshot: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for recipient in recipients:
            delivery = recipient.delivery if recipient.delivery in by_delivery else "to"
            identity = (recipient.email.strip().lower(), delivery)
            if identity in seen:
                continue
            seen.add(identity)
            by_delivery[delivery].append(recipient.email)
            actual_list_type = list_type(recipient) if callable(list_type) else list_type
            snapshot.append(self._snapshot(recipient, list_type=actual_list_type))
        if not any(by_delivery.values()):
            raise RoutingError("no active recipient can receive this notification")
        return RecipientSelection(
            to=tuple(by_delivery["to"]),
            cc=tuple(by_delivery["cc"]),
            bcc=tuple(by_delivery["bcc"]),
            snapshot=tuple(snapshot),
        )

    def _active(self, *, list_type: str) -> list[Recipient]:
        return list(
            self.session.scalars(
                select(Recipient)
                .where(Recipient.list_type == list_type, Recipient.active.is_(True))
                .order_by(Recipient.id)
            ).all()
        )

    @staticmethod
    def _snapshot(recipient: Recipient, *, list_type: str) -> dict:
        return {
            "email": recipient.email,
            "name": recipient.name or "",
            "role": recipient.role or "",
            "delivery": recipient.delivery,
            "list_type": list_type,
        }

    @staticmethod
    def _in_scope(recipient: Recipient, source_names: set[str]) -> bool:
        scope = recipient.source_scope or []
        return not scope or bool(scope and source_names.intersection({str(item) for item in scope}))

    @staticmethod
    def _receives(recipient: Recipient, recommendation: str, urgency_flag: bool) -> bool:
        receives = recipient.receives_filter or "all"
        if receives == "all":
            return True
        if receives == "apply":
            return recommendation in APPLY_VERDICTS
        if receives == "urgent":
            return urgency_flag
        return False


__all__ = [
    "APPLY_VERDICTS",
    "RecipientRouter",
    "RecipientSelection",
    "RoutingError",
    "SEVERITY_ORDER",
]

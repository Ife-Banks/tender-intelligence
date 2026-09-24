""":mod:`tender_intelligence.notifications.banner` — persisted red-banner signal (docs/08 §8.10).

When every provider in the chain has failed, the notification stays a durable ``pending_retry``
outbox entry and the health view must show a red banner *even if alert email also fails* — so
the banner is a persisted ``AlertEvent`` (open/recovered), never something held only in memory.
Re-raising is idempotent (one open banner per outage), and clearing resolves every open banner
(prompt 11 §10).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.alerts import AlertEvent

#: Alert-event type used for the email-chain red banner. Prompt 12 owns the full alert-event
#: lifecycle; this module only guarantees the persisted fallback signal prompt 11 §10 needs.
RED_BANNER_TYPE = "email_chain_down"

_MAX_CORRELATION_CHARS = 36


def raise_email_chain_down_banner(
    session: Session,
    *,
    correlation_id: str | None = None,
    source_id: int | None = None,
    message: str = "",
) -> AlertEvent:
    """Ensure an open red-banner alert event exists; returns it (create-or-update)."""
    existing = session.scalars(
        select(AlertEvent).where(AlertEvent.type == RED_BANNER_TYPE, AlertEvent.state == "open")
    ).first()
    if existing is not None:
        return existing
    event = AlertEvent(
        type=RED_BANNER_TYPE,
        severity="critical",
        source_id=source_id,
        correlation_id=(correlation_id or "")[:_MAX_CORRELATION_CHARS] or None,
        state="open",
        message=message[:1024] or "email provider chain is down; notifications queued",
    )
    session.add(event)
    session.flush()
    return event


def clear_email_chain_down_banner(session: Session) -> int:
    """Resolve every open email-chain banner; returns how many were resolved.

    Called when a durable ``pending_retry`` flush succeeds, so the health view clears once
    delivery is possible again.
    """
    rows = list(
        session.scalars(
            select(AlertEvent).where(AlertEvent.type == RED_BANNER_TYPE, AlertEvent.state == "open")
        ).all()
    )
    now = datetime.now(UTC)
    for event in rows:
        event.state = "recovered"
        event.resolved_at = now
    session.flush()
    return len(rows)


def email_chain_banner_active(session: Session) -> bool:
    """Return True when the red-banner alert event is currently open (health view input)."""
    return (
        session.scalars(
            select(AlertEvent)
            .where(AlertEvent.type == RED_BANNER_TYPE, AlertEvent.state == "open")
            .limit(1)
        ).first()
        is not None
    )

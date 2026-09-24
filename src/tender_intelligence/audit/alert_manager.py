"""Alert manager with triggers, throttling, recovery, and reminders (Prompt 12 §13–§21).

The alert manager accepts normalized alert events from upstream stages, evaluates trigger
conditions, handles throttling/deduplication, recovery tracking, and reminder scheduling.

It does NOT contain business logic for determining source/AI/provider health beyond
documented trigger conditions. Its responsibilities:

    alert trigger → normalize → correlate → throttle/dedupe → persist AlertEvent
              → route to developer alert list → recovery/reminder handling
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.alerts import AlertEvent

# Alert trigger types (Prompt 12 §14)
ALERT_TYPES = frozenset(
    {
        "source_failed",
        "parser_mismatch",
        "ai_failure",
        "invalid_ai_output",
        "budget_80_percent",
        "budget_100_percent",
        "email_all_providers_failed",
        "provider_circuit_breaker_opened",
        "daily_health_digest",
    }
)


@dataclass(frozen=True)
class AlertTrigger:
    """Normalized alert trigger event (Prompt 12 §13, §15).

    Carries sufficient information to reconstruct why an alert occurred without secrets
    or raw document text.
    """

    alert_type: str
    severity: str  # info, warning, critical
    source_id: int | None = None
    correlation_id: str | None = None
    run_id: int | None = None
    tender_id: int | None = None
    message: str = ""
    metadata: dict[str, str | int | float | bool] | None = None


@runtime_checkable
class AlertDelivery(Protocol):
    """Alert delivery boundary (Prompt 12 §19).

    Alert manager calls this protocol to deliver alerts through the notification layer
    (Prompt 11). It must NOT directly call provider adapters.
    """

    def send_alert(
        self, alert_type: str, severity: str, message: str, deep_link: str | None = None
    ) -> None:
        """Deliver alert to developer alert list via Prompt 11 notification seam."""


class NullAlertDelivery:
    """Default no-op implementation for testing."""

    def __init__(self) -> None:
        self.sent_alerts: list[dict] = []

    def send_alert(
        self, alert_type: str, severity: str, message: str, deep_link: str | None = None
    ) -> None:
        self.sent_alerts.append(
            {
                "alert_type": alert_type,
                "severity": severity,
                "message": message,
                "deep_link": deep_link,
            }
        )


class AlertManager:
    """Alert orchestration with throttling, recovery, and reminders (Prompt 12 §13–§21)."""

    def __init__(
        self,
        session: Session,
        delivery: AlertDelivery | None = None,
        reminder_interval_hours: int = 24,
    ) -> None:
        self.session = session
        self.delivery = delivery or NullAlertDelivery()
        self.reminder_interval = timedelta(hours=reminder_interval_hours)

    def trigger(self, trigger_event: AlertTrigger) -> AlertEvent | None:
        """Process alert trigger: throttle, dedupe, persist, deliver (Prompt 12 §13–§16).

        Returns:
            Created or existing throttled AlertEvent, or None if suppressed.
        """
        # Validate alert type
        if trigger_event.alert_type not in ALERT_TYPES:
            raise ValueError(
                f"Unknown alert type: {trigger_event.alert_type}. "
                f"Must be one of: {', '.join(sorted(ALERT_TYPES))}"
            )

        # Build alert identity for deduplication (Prompt 12 §16)
        identity_key = self._build_identity_key(trigger_event)

        # Find existing open alert with same identity
        existing = self._find_open_alert(identity_key, trigger_event)

        if existing:
            # Check if reminder is due (Prompt 12 §18)
            if self._should_send_reminder(existing):
                existing.last_reminded_at = datetime.now()
                self.session.flush()
                self._deliver_alert(trigger_event, existing, is_reminder=True)
                return existing
            # Otherwise throttled - no new alert
            return existing

        # Create new alert event
        alert = AlertEvent(
            type=trigger_event.alert_type,
            severity=trigger_event.severity,
            source_id=trigger_event.source_id,
            correlation_id=trigger_event.correlation_id,
            state="open",
            message=trigger_event.message,
            first_raised_at=datetime.now(),
        )
        self.session.add(alert)
        self.session.flush()

        # Deliver alert
        self._deliver_alert(trigger_event, alert, is_reminder=False)

        return alert

    def mark_recovered(
        self, alert_type: str, source_id: int | None = None, correlation_id: str | None = None
    ) -> AlertEvent | None:
        """Mark condition as recovered and generate recovery alert (Prompt 12 §17).

        Returns:
            Recovery AlertEvent if state transition occurred, None if already healthy.
        """
        # Find open alert with matching identity
        query = select(AlertEvent).where(
            AlertEvent.type == alert_type, AlertEvent.state == "open"
        )

        if source_id is not None:
            query = query.where(AlertEvent.source_id == source_id)
        if correlation_id is not None:
            query = query.where(AlertEvent.correlation_id == correlation_id)

        existing = self.session.scalars(query).first()

        if not existing:
            # Already healthy or never was unhealthy - no recovery alert needed
            return None

        # Transition to recovered
        existing.state = "recovered"
        existing.resolved_at = datetime.now()
        self.session.flush()

        # Create recovery alert (Prompt 12 §17)
        recovery = AlertEvent(
            type=f"{alert_type}_recovered",
            severity="info",
            source_id=source_id,
            correlation_id=correlation_id,
            state="open",  # Recovery alert is itself "open" but won't re-trigger
            message=f"Recovered: {existing.message}",
            first_raised_at=datetime.now(),
        )
        self.session.add(recovery)
        self.session.flush()

        # Deliver recovery notification
        recovery_trigger = AlertTrigger(
            alert_type=recovery.type,
            severity="info",
            source_id=source_id,
            correlation_id=correlation_id,
            message=recovery.message,
        )
        self._deliver_alert(recovery_trigger, recovery, is_reminder=False)

        return recovery

    def _build_identity_key(self, trigger: AlertTrigger) -> str:
        """Build deduplication identity key (Prompt 12 §16).

        Identity distinguishes: alert type, source/component, failure condition, scope.
        """
        parts = [trigger.alert_type]

        if trigger.source_id is not None:
            parts.append(f"source:{trigger.source_id}")
        if trigger.correlation_id is not None:
            parts.append(f"corr:{trigger.correlation_id}")
        if trigger.tender_id is not None:
            parts.append(f"tender:{trigger.tender_id}")

        return ":".join(parts)

    def _find_open_alert(self, identity_key: str, trigger: AlertTrigger) -> AlertEvent | None:
        """Find existing open alert matching identity (Prompt 12 §16)."""
        query = select(AlertEvent).where(
            AlertEvent.type == trigger.alert_type, AlertEvent.state == "open"
        )

        if trigger.source_id is not None:
            query = query.where(AlertEvent.source_id == trigger.source_id)
        if trigger.correlation_id is not None:
            query = query.where(AlertEvent.correlation_id == trigger.correlation_id)

        return self.session.scalars(query).first()

    def _should_send_reminder(self, alert: AlertEvent) -> bool:
        """Check if reminder is due (Prompt 12 §18)."""
        if alert.last_reminded_at is None:
            # First reminder due after interval from first raise
            return datetime.now() >= alert.first_raised_at + self.reminder_interval

        # Subsequent reminders
        return datetime.now() >= alert.last_reminded_at + self.reminder_interval

    def _deliver_alert(
        self, trigger: AlertTrigger, alert: AlertEvent, is_reminder: bool
    ) -> None:
        """Deliver alert via notification boundary (Prompt 12 §19–§21)."""
        # Generate deep link (Prompt 12 §21)
        deep_link = None
        if trigger.correlation_id:
            deep_link = f"/admin/timeline/{trigger.correlation_id}"
        elif trigger.tender_id:
            deep_link = f"/admin/tenders/{trigger.tender_id}/timeline"
        elif trigger.run_id:
            deep_link = f"/admin/runs/{trigger.run_id}"

        # Build message
        message = trigger.message
        if is_reminder:
            message = f"[REMINDER] {message}"

        # Deliver via Prompt 11 notification seam (Prompt 12 §19)
        self.delivery.send_alert(
            alert_type=trigger.alert_type,
            severity=trigger.severity,
            message=message,
            deep_link=deep_link,
        )

"""Alert delivery adapter that bridges AlertManager to NotificationService (Prompt 12 §19–§20).

This adapter implements the AlertDelivery protocol and routes alerts to the developer
alert list via Prompt 11's notification infrastructure. It ensures:
- Alerts go to dev list only (not business recipients)
- Test Mode safety applies
- No direct provider calls from alert manager
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.audit.alert_manager import AlertDelivery
from tender_intelligence.db.models.mail import NotificationLog
from tender_intelligence.db.models.recipients import Recipient

if TYPE_CHECKING:
    pass  # Avoid circular import with NotificationService


class AlertDeliveryAdapter(AlertDelivery):
    """Routes alerts to developer alert list via notification layer (Prompt 12 §19).

    This adapter creates NotificationLog entries for alerts that will be delivered
    through the existing provider chain infrastructure.
    """

    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def send_alert(
        self, alert_type: str, severity: str, message: str, deep_link: str | None = None
    ) -> None:
        """Deliver alert to developer alert list (Prompt 12 §19–§20).

        Creates a NotificationLog entry with notification_kind='alert' that will be
        processed by the notification service's outbox flusher.
        """
        with self.session_factory() as session:
            # Get developer recipients (alert list)
            dev_recipients = session.query(Recipient).filter(
                Recipient.list_type == "dev_alert", Recipient.active == True  # noqa: E712
            ).all()

            if not dev_recipients:
                # No dev recipients configured - can't deliver alert
                # This is a critical configuration issue but we can't alert about it
                return

            # Build alert message body
            body_lines = [
                f"Alert Type: {alert_type}",
                f"Severity: {severity.upper()}",
                "",
                message,
            ]

            if deep_link:
                body_lines.extend(["", f"View details: {deep_link}"])

            body = "\n".join(body_lines)

            # Build recipients snapshot
            recipients_snapshot = [
                {
                    "email": r.email,
                    "name": r.name or "",
                    "delivery": r.delivery or "to",
                }
                for r in dev_recipients
            ]

            # Create dedupe key for this alert
            dedupe_key = f"alert:{alert_type}:{hash(message)}"

            # Check if already sent
            existing = session.query(NotificationLog).filter(
                NotificationLog.dedupe_key == dedupe_key
            ).first()

            if existing:
                # Already sent this exact alert
                return

            # Create notification log entry for alert
            alert_notification = NotificationLog(
                tender_id=None,  # Alerts are not tender-specific
                verdict_id=None,
                correlation_id=None,
                recipients_snapshot=recipients_snapshot,
                sent_at=None,  # Will be set when actually sent
                attachments=[],
                links=[],
                status="pending",
                provider_used=None,
                dedupe_key=dedupe_key,
                possible_duplicate=False,
                notification_kind="alert",
                priority=10 if severity == "critical" else 50,  # Higher priority for critical
                message_payload={
                    "subject": f"[ALERT] {alert_type}",
                    "body": body,
                    "alert_type": alert_type,
                    "severity": severity,
                    "deep_link": deep_link,
                },
            )

            session.add(alert_notification)
            session.commit()

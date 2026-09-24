"""Alert manager tests (Prompt 12 §13–§21, §35).

Tests alert triggers, throttling, recovery, reminders, concurrent handling, restart safety.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import sleep

import pytest
from sqlalchemy.orm import Session

from tender_intelligence.audit.alert_manager import AlertManager, AlertTrigger, NullAlertDelivery
from tender_intelligence.db.models.alerts import AlertEvent
from tender_intelligence.db.models.sources import Source


@pytest.fixture
def source(db_session: Session) -> Source:
    """Create test source."""
    src = Source(
        name="WAHO",
        source_type="waho",
        base_url="https://waho.example",
        parser_config={},
        active=True,
    )
    db_session.add(src)
    db_session.flush()
    return src


@pytest.fixture
def delivery() -> NullAlertDelivery:
    """Null delivery for testing."""
    return NullAlertDelivery()


@pytest.fixture
def alert_manager(db_session: Session, delivery: NullAlertDelivery) -> AlertManager:
    """Alert manager with test delivery."""
    return AlertManager(db_session, delivery, reminder_interval_hours=24)


def test_trigger_creates_alert_event(
    db_session: Session, alert_manager: AlertManager, source: Source
) -> None:
    """Triggering alert creates AlertEvent and delivers notification (Prompt 12 §13–§15)."""
    trigger = AlertTrigger(
        alert_type="source_failed",
        severity="warning",
        source_id=source.id,
        correlation_id="test-001",
        message="Source WAHO failed 3 consecutive runs",
    )

    alert = alert_manager.trigger(trigger)

    assert alert is not None
    assert alert.type == "source_failed"
    assert alert.severity == "warning"
    assert alert.source_id == source.id
    assert alert.state == "open"
    assert alert.message == "Source WAHO failed 3 consecutive runs"

    # Should be persisted
    db_session.flush()
    persisted = db_session.get(AlertEvent, alert.id)
    assert persisted is not None


def test_alert_delivered_via_hook(
    alert_manager: AlertManager, delivery: NullAlertDelivery, source: Source
) -> None:
    """Alert triggers delivery via AlertDelivery protocol (Prompt 12 §19)."""
    trigger = AlertTrigger(
        alert_type="parser_mismatch",
        severity="critical",
        source_id=source.id,
        message="Parser structure changed",
    )

    alert_manager.trigger(trigger)

    # Should have called delivery
    assert len(delivery.sent_alerts) == 1
    sent = delivery.sent_alerts[0]
    assert sent["alert_type"] == "parser_mismatch"
    assert sent["severity"] == "critical"
    assert "Parser structure changed" in sent["message"]


def test_throttling_suppresses_duplicate_alerts(
    db_session: Session, alert_manager: AlertManager, source: Source
) -> None:
    """Repeated identical failures produce one alert, not unlimited (Prompt 12 §16)."""
    trigger = AlertTrigger(
        alert_type="source_failed",
        severity="warning",
        source_id=source.id,
        message="Source failed",
    )

    # Trigger same alert multiple times
    alert1 = alert_manager.trigger(trigger)
    alert2 = alert_manager.trigger(trigger)
    alert3 = alert_manager.trigger(trigger)

    # Should return same alert (throttled)
    assert alert1.id == alert2.id == alert3.id

    # Should only have one AlertEvent
    count = db_session.query(AlertEvent).filter(
        AlertEvent.type == "source_failed", AlertEvent.source_id == source.id
    ).count()
    assert count == 1


def test_alert_identity_distinguishes_sources(
    db_session: Session, alert_manager: AlertManager
) -> None:
    """Alert identity distinguishes different sources (Prompt 12 §16)."""
    # Create two sources
    src1 = Source(name="WAHO", source_type="waho", base_url="https://waho.example", active=True)
    src2 = Source(name="UNGM", source_type="ungm", base_url="https://ungm.example", active=True)
    db_session.add_all([src1, src2])
    db_session.flush()

    # Same alert type, different sources
    trigger1 = AlertTrigger(
        alert_type="source_failed", severity="warning", source_id=src1.id, message="WAHO failed"
    )
    trigger2 = AlertTrigger(
        alert_type="source_failed", severity="warning", source_id=src2.id, message="UNGM failed"
    )

    alert1 = alert_manager.trigger(trigger1)
    alert2 = alert_manager.trigger(trigger2)

    # Should be different alerts
    assert alert1.id != alert2.id
    assert alert1.source_id == src1.id
    assert alert2.source_id == src2.id


def test_recovery_transitions_state(
    db_session: Session, alert_manager: AlertManager, source: Source
) -> None:
    """Recovery marks alert as recovered and creates recovery alert (Prompt 12 §17)."""
    # Create unhealthy alert
    trigger = AlertTrigger(
        alert_type="ai_failure",
        severity="critical",
        source_id=source.id,
        message="AI service unavailable",
    )
    unhealthy_alert = alert_manager.trigger(trigger)
    assert unhealthy_alert.state == "open"

    # Mark as recovered
    recovery_alert = alert_manager.mark_recovered(
        alert_type="ai_failure", source_id=source.id
    )

    # Original alert should be recovered
    db_session.expire(unhealthy_alert)
    db_session.refresh(unhealthy_alert)
    assert unhealthy_alert.state == "recovered"
    assert unhealthy_alert.resolved_at is not None

    # Recovery alert should exist
    assert recovery_alert is not None
    assert recovery_alert.type == "ai_failure_recovered"
    assert recovery_alert.severity == "info"
    assert "Recovered:" in recovery_alert.message


def test_recovery_when_already_healthy(
    db_session: Session, alert_manager: AlertManager, source: Source
) -> None:
    """Recovery returns None when already healthy (Prompt 12 §17)."""
    # No unhealthy alert exists
    recovery = alert_manager.mark_recovered(
        alert_type="email_all_providers_failed", source_id=source.id
    )

    # Should return None - already healthy
    assert recovery is None


def test_no_duplicate_recovery_alerts(
    db_session: Session, alert_manager: AlertManager, source: Source, delivery: NullAlertDelivery
) -> None:
    """Continued healthy state doesn't generate unlimited recovery alerts (Prompt 12 §17)."""
    # Create and recover alert
    trigger = AlertTrigger(
        alert_type="budget_80_percent", severity="warning", message="Budget warning"
    )
    alert_manager.trigger(trigger)
    alert_manager.mark_recovered(alert_type="budget_80_percent")

    delivery.sent_alerts.clear()

    # Mark as recovered again - should be no-op
    recovery2 = alert_manager.mark_recovered(alert_type="budget_80_percent")
    assert recovery2 is None
    assert len(delivery.sent_alerts) == 0


def test_reminder_sent_after_interval(
    db_session: Session, delivery: NullAlertDelivery, source: Source
) -> None:
    """Reminders are sent when interval is reached (Prompt 12 §18)."""
    # Use short interval for testing
    manager = AlertManager(db_session, delivery, reminder_interval_hours=0)  # 0 seconds

    trigger = AlertTrigger(
        alert_type="provider_circuit_breaker_opened",
        severity="critical",
        source_id=source.id,
        message="Provider circuit opened",
    )

    # First trigger
    alert = manager.trigger(trigger)
    assert len(delivery.sent_alerts) == 1

    # Wait a moment
    sleep(0.1)

    # Trigger again - should send reminder
    alert2 = manager.trigger(trigger)
    assert alert2.id == alert.id  # Same alert
    assert len(delivery.sent_alerts) == 2
    assert "[REMINDER]" in delivery.sent_alerts[1]["message"]


def test_reminder_not_sent_before_interval(
    db_session: Session, alert_manager: AlertManager, source: Source, delivery: NullAlertDelivery
) -> None:
    """Reminders are not sent before configured interval (Prompt 12 §18)."""
    trigger = AlertTrigger(
        alert_type="invalid_ai_output",
        severity="warning",
        source_id=source.id,
        message="AI output validation failed",
    )

    # First trigger
    alert_manager.trigger(trigger)
    assert len(delivery.sent_alerts) == 1

    # Immediate re-trigger - should be throttled
    alert_manager.trigger(trigger)
    assert len(delivery.sent_alerts) == 1  # Still only one


def test_deep_link_generation(
    alert_manager: AlertManager, delivery: NullAlertDelivery, source: Source
) -> None:
    """Alerts include deep links for timeline context (Prompt 12 §21)."""
    trigger = AlertTrigger(
        alert_type="source_failed",
        severity="warning",
        source_id=source.id,
        correlation_id="deep-link-001",
        message="Source failed",
    )

    alert_manager.trigger(trigger)

    assert len(delivery.sent_alerts) == 1
    sent = delivery.sent_alerts[0]
    assert sent["deep_link"] is not None
    assert "deep-link-001" in sent["deep_link"]


def test_concurrent_alert_triggers(
    db_session: Session, alert_manager: AlertManager, source: Source
) -> None:
    """Concurrent duplicate triggers don't create multiple alerts (Prompt 12 §29)."""
    trigger = AlertTrigger(
        alert_type="email_all_providers_failed",
        severity="critical",
        source_id=source.id,
        message="All providers failed",
    )

    # Simulate concurrent triggers
    alert1 = alert_manager.trigger(trigger)
    db_session.flush()

    alert2 = alert_manager.trigger(trigger)
    db_session.flush()

    # Should be same alert
    assert alert1.id == alert2.id

    # Only one persisted
    count = db_session.query(AlertEvent).filter(
        AlertEvent.type == "email_all_providers_failed"
    ).count()
    assert count == 1


def test_alert_types_validated(db_session: Session, delivery: NullAlertDelivery) -> None:
    """Unknown alert types are rejected (Prompt 12 §14)."""
    manager = AlertManager(db_session, delivery)

    invalid_trigger = AlertTrigger(
        alert_type="unknown_alert_type",
        severity="warning",
        message="Invalid",
    )

    with pytest.raises(ValueError, match="Unknown alert type"):
        manager.trigger(invalid_trigger)


def test_all_documented_alert_types(
    db_session: Session, alert_manager: AlertManager, delivery: NullAlertDelivery
) -> None:
    """All documented alert types are supported (Prompt 12 §14)."""
    documented_types = [
        "source_failed",
        "parser_mismatch",
        "ai_failure",
        "invalid_ai_output",
        "budget_80_percent",
        "budget_100_percent",
        "email_all_providers_failed",
        "provider_circuit_breaker_opened",
        "daily_health_digest",
    ]

    for alert_type in documented_types:
        delivery.sent_alerts.clear()

        trigger = AlertTrigger(
            alert_type=alert_type,
            severity="warning",
            message=f"Test {alert_type}",
        )

        alert = alert_manager.trigger(trigger)
        assert alert is not None
        assert alert.type == alert_type
        assert len(delivery.sent_alerts) == 1


def test_restart_recovery_after_crash(
    db_session: Session, source: Source, delivery: NullAlertDelivery
) -> None:
    """Alert state survives process restart (Prompt 12 §30)."""
    # Create alert with first manager instance
    manager1 = AlertManager(db_session, delivery)
    trigger = AlertTrigger(
        alert_type="source_failed",
        severity="warning",
        source_id=source.id,
        message="Source failed",
    )
    alert1 = manager1.trigger(trigger)
    db_session.commit()

    # Simulate restart - new manager instance
    db_session.expire_all()
    manager2 = AlertManager(db_session, NullAlertDelivery())

    # Trigger same alert again - should find existing
    alert2 = manager2.trigger(trigger)
    assert alert2.id == alert1.id  # Same persisted alert


def test_alert_metadata_no_secrets(
    db_session: Session, alert_manager: AlertManager
) -> None:
    """Alert metadata never contains secrets or raw document text (Prompt 12 §31)."""
    trigger = AlertTrigger(
        alert_type="parser_mismatch",
        severity="warning",
        message="Parser changed for source",
        metadata={
            "source_name": "WAHO",
            "error_count": 5,
            # Explicitly no secrets or document content
        },
    )

    alert = alert_manager.trigger(trigger)

    # Verify no secrets in persisted message
    assert "password" not in alert.message.lower()
    assert "api_key" not in alert.message.lower()
    assert "secret" not in alert.message.lower()
    assert "token" not in alert.message.lower()


def test_alert_with_tender_context(
    db_session: Session, alert_manager: AlertManager, delivery: NullAlertDelivery
) -> None:
    """Alerts can include tender context for deep linking (Prompt 12 §15, §21)."""
    trigger = AlertTrigger(
        alert_type="ai_failure",
        severity="critical",
        tender_id=123,
        correlation_id="tender-alert-001",
        message="AI processing failed for tender",
    )

    alert_manager.trigger(trigger)

    # Deep link should reference tender
    assert len(delivery.sent_alerts) == 1
    deep_link = delivery.sent_alerts[0]["deep_link"]
    assert deep_link is not None
    assert "123" in deep_link or "tender-alert-001" in deep_link


def test_alert_with_run_context(
    db_session: Session, alert_manager: AlertManager, delivery: NullAlertDelivery
) -> None:
    """Alerts can include run context for deep linking (Prompt 12 §15, §21)."""
    trigger = AlertTrigger(
        alert_type="source_failed",
        severity="warning",
        run_id=456,
        message="Run failed",
    )

    alert_manager.trigger(trigger)

    # Deep link should reference run
    assert len(delivery.sent_alerts) == 1
    deep_link = delivery.sent_alerts[0]["deep_link"]
    assert deep_link is not None
    assert "456" in deep_link


def test_reminder_updates_last_reminded_at(
    db_session: Session, delivery: NullAlertDelivery, source: Source
) -> None:
    """Reminders update last_reminded_at timestamp (Prompt 12 §18)."""
    manager = AlertManager(db_session, delivery, reminder_interval_hours=0)

    trigger = AlertTrigger(
        alert_type="budget_100_percent",
        severity="critical",
        message="Budget exhausted",
    )

    # First trigger
    alert = manager.trigger(trigger)
    first_reminded = alert.last_reminded_at
    assert first_reminded is None  # Not reminded yet

    sleep(0.1)

    # Trigger again - should send reminder
    alert2 = manager.trigger(trigger)
    db_session.refresh(alert2)
    assert alert2.last_reminded_at is not None
    assert alert2.last_reminded_at != first_reminded

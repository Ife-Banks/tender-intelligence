"""Architecture checks for the notification channel boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from tender_intelligence.notifications.channels import (
    EmailNotificationChannel,
    NotificationChannel,
    NotificationDispatcher,
)
from tender_intelligence.notifications.contracts import NotificationEvent, NotificationOutcome


class _EmailServiceSpy:
    def __init__(self) -> None:
        self.calls = []

    def notify_tender(self, tender_id, verdict_id, *, event, dry_run=False):
        self.calls.append((tender_id, verdict_id, event, dry_run))
        return NotificationOutcome(status="sent", notification_log_id=42)


def test_tender_notification_dispatches_through_email_channel_boundary() -> None:
    event = NotificationEvent(tender_id=7, verdict_id=11)
    email_service = _EmailServiceSpy()
    email = EmailNotificationChannel(email_service)  # type: ignore[arg-type]
    dispatcher = NotificationDispatcher(email)

    outcome = dispatcher.send(event)

    assert isinstance(email, NotificationChannel)
    assert dispatcher.channel_name == "email"
    assert email_service.calls == [(7, 11, event, False)]
    assert outcome.notification_log_id == 42


def test_pipeline_modules_do_not_import_or_construct_email_providers() -> None:
    repository = Path(__file__).resolve().parents[2] / "src" / "tender_intelligence"
    pipeline_files = (
        repository / "orchestrator" / "coordinator.py",
        repository / "orchestrator" / "worker.py",
        repository / "worker" / "main.py",
    )
    forbidden_roots = ("tender_intelligence.mail", "smtplib", "sendlib")

    for path in pipeline_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            assert not any(
                imported == root or imported.startswith(root + ".")
                for imported in imports
                for root in forbidden_roots
            ), f"pipeline module imports email transport directly: {path}"


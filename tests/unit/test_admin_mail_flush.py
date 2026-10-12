"""Admin scheduler outbox-drain wiring (C5, prompt 11 §36).

``_scheduled_source_loop`` calls ``_flush_mail_outbox`` every tick so ``pending_retry`` rows
recover on the same cadence as scheduled crawls. These tests pin the drain's three contracts:
it runs when a notification service exists, is skipped when none is configured, and never
propagates a flush failure into the scheduler loop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tender_intelligence.admin.main import _flush_mail_outbox


class _FakeNotificationService:
    def __init__(self) -> None:
        self.calls = 0

    def flush_pending_retry(self):
        self.calls += 1
        return SimpleNamespace(attempted=1, flushed=1, still_pending=0, banner_cleared=False)


def test_admin_loop_flushes_pending_retry_each_tick() -> None:
    service = _FakeNotificationService()
    app = SimpleNamespace(state=SimpleNamespace(notification_service=service))

    asyncio.run(_flush_mail_outbox(app))

    assert service.calls == 1


def test_admin_loop_skips_flush_when_no_notification_service() -> None:
    app = SimpleNamespace(state=SimpleNamespace(notification_service=None))

    asyncio.run(_flush_mail_outbox(app))  # must not raise


def test_admin_loop_survives_flush_failure() -> None:
    class _BrokenService:
        def flush_pending_retry(self):
            raise RuntimeError("provider unreachable")

    app = SimpleNamespace(state=SimpleNamespace(notification_service=_BrokenService()))

    asyncio.run(_flush_mail_outbox(app))  # failure is logged, the loop continues
"""Channel-neutral boundary for delivering an already-created tender notification.

The worker's post-verdict stage uses :class:`NotificationDispatcher`; the v1 email
implementation delegates to the existing audited notification service and its mail provider
chain. This module intentionally defines no credentials or configuration for future channels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from tender_intelligence.notifications.contracts import NotificationEvent, NotificationOutcome

if TYPE_CHECKING:
    from tender_intelligence.notifications.service import NotificationService


@runtime_checkable
class NotificationChannel(Protocol):
    """A transport-neutral destination for a tender notification event."""

    name: str

    def send(
        self, notification: NotificationEvent, *, dry_run: bool = False
    ) -> NotificationOutcome:
        """Deliver or safely simulate the notification using this channel."""
        ...


class EmailNotificationChannel:
    """Email-only v1 channel backed by the existing notification/mail service."""

    name = "email"

    def __init__(self, service: NotificationService) -> None:
        self._service = service

    def send(
        self, notification: NotificationEvent, *, dry_run: bool = False
    ) -> NotificationOutcome:
        return self._service.notify_tender(
            notification.tender_id,
            notification.verdict_id,
            event=notification,
            dry_run=dry_run,
        )


class NotificationDispatcher:
    """Pipeline-facing dependency that delegates to the configured channel instance.

    v1 composition supplies only :class:`EmailNotificationChannel`. A future composition can
    inject a different ``NotificationChannel`` without changing discovery or assessment code.
    Channel selection is deliberately dependency injection, not an operational setting.
    """

    def __init__(self, channel: NotificationChannel) -> None:
        self._channel = channel

    @property
    def channel_name(self) -> str:
        return self._channel.name

    def send(
        self, notification: NotificationEvent, *, dry_run: bool = False
    ) -> NotificationOutcome:
        return self._channel.send(notification, dry_run=dry_run)


__all__ = ["EmailNotificationChannel", "NotificationChannel", "NotificationDispatcher"]

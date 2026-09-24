"""Durable notification state vocabulary.

The confirmed data model exposes ``sent``/``failed``/``pending_retry``.  The outbox adds
observable in-flight and ambiguity states so a restart can distinguish a reservation from a
send already in progress.
"""

from enum import StrEnum


class NotificationState(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    PENDING_RETRY = "pending_retry"
    POSSIBLE_DUPLICATE = "possible_duplicate"


__all__ = ["NotificationState"]

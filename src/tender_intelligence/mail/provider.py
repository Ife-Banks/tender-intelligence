""":mod:`tender_intelligence.mail.provider` — MailProvider abstraction + capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tender_intelligence.mail.message import EmailMessage


@dataclass(frozen=True)
class Capabilities:
    """Provider attachment/message limits (docs/03 MailProvider.capabilities).

    ``None`` on any field means "no explicit limit" (planner treats it as unbounded).
    """

    max_attachments: int | None = None
    max_attachment_mb: int | None = None
    max_message_mb: int | None = None
    daily_limit: int | None = None
    rate_limit_per_min: int | None = None
    needs_verified_domain: bool = False


@dataclass(frozen=True)
class SendResult:
    """Outcome of one provider attempt (mirrors docs/03 NotificationAttempt)."""

    provider_name: str
    ok: bool
    error_code: str | None = None
    provider_message_id: str | None = None
    duration_ms: int | None = None


class SendError(Exception):
    """A concrete provider failed to send; carries a machine-readable error code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class MailProvider(ABC):
    """Platform-neutral mail provider seam (v1.1 §5.10.2).

    Concrete adapters (Sendlib first, Phase 1) implement :meth:`send` and expose
    :attr:`capabilities`. Business logic never branches on provider identity.
    """

    name: str = "abstract"

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Provider limits used by the attachment planner."""

    @abstractmethod
    def send(self, message: EmailMessage) -> SendResult:
        """Deliver ``message``. Raise :class:`SendError` on provider failure so the caller
        can fail over or mark the attempt failed with the right error code."""

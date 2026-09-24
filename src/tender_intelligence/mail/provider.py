""":mod:`tender_intelligence.mail.provider` — MailProvider abstraction + capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from tender_intelligence.mail.message import EmailMessage


@dataclass(frozen=True)
class Capabilities:
    """Provider capability limits from v1.1 §5.10.2 / docs/08 §8.2.

    ``None`` means that the provider has not declared a limit.  The planner treats it as
    unbounded, while a value of zero is rejected: a zero limit is almost always a
    configuration mistake and would otherwise make a message silently disappear.
    """

    max_attachments: int | None = None
    max_attachment_mb: int | None = None
    max_message_mb: int | None = None
    daily_limit: int | None = None
    rate_limit_per_min: int | None = None
    needs_verified_domain: bool = False

    def __post_init__(self) -> None:
        for name in (
            "max_attachments",
            "max_attachment_mb",
            "max_message_mb",
            "daily_limit",
            "rate_limit_per_min",
        ):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise TypeError(f"{name} must be an integer or None")
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be > 0")


@dataclass(frozen=True)
class SendResult:
    """Provider-neutral result of one send attempt.

    ``provider_message_id`` is safe metadata only.  Provider response bodies and credentials
    deliberately have no field here, so an adapter cannot accidentally persist them.
    """

    provider_name: str
    ok: bool
    error_code: str | None = None
    provider_message_id: str | None = None
    duration_ms: int | None = None


class SendError(Exception):
    """Backward-compatible name for a provider send failure.

    New adapters should raise :class:`tender_intelligence.mail.errors.MailError`, which adds
    retry/permanent/possible-duplicate classification.  Keeping this small compatibility
    exception avoids breaking the pre-Prompt-11 provider seam.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class MailProvider(ABC):
    """Platform-neutral mail provider seam (v1.1 §5.10.2).

    The interface intentionally contains only provider-independent operations.  Sender and
    credential material belongs to the provider configuration/adapter boundary; the chain
    never needs to inspect it.
    """

    name: str = "abstract"
    provider_type: str = "abstract"

    @property
    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Provider limits used by the attachment planner."""

    @abstractmethod
    def send(self, message: EmailMessage) -> SendResult:
        """Deliver ``message`` or raise a classified :class:`MailError`.

        A returned ``SendResult(ok=False)`` is also treated as a failed attempt by the chain.
        """

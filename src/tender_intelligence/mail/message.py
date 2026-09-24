""":mod:`tender_intelligence.mail.message` — platform-neutral email message."""

from __future__ import annotations

from dataclasses import dataclass, field

from tender_intelligence.db.models.recipients import is_valid_email


class InvalidAddress(ValueError):
    """Raised when an address is not a plausible email."""


@dataclass(frozen=True)
class MailAttachment:
    """One file to attach to a message.

    Attributes:
        filename: Original filename (safe to send; no path separators).
        content: Raw bytes of the document.
        content_type: MIME type, when known.
    """

    filename: str
    content: bytes
    content_type: str | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class EmailMessage:
    """A notification-ready message. No provider-specific fields leak in here."""

    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str = ""
    text_body: str = ""
    html_body: str | None = None
    attachments: tuple[MailAttachment, ...] = field(default_factory=tuple)
    secure_links: tuple[str, ...] = field(default_factory=tuple)
    dedupe_key: str = ""
    correlation_id: str | None = None

    def validate(self) -> None:
        """Validate addresses and attachment filenames before constructing a message."""
        for address in (*self.to, *self.cc, *self.bcc):
            if not is_valid_email(address):
                raise InvalidAddress(address)
        for attachment in self.attachments:
            if "/" in attachment.filename or "\\" in attachment.filename:
                raise InvalidAddress(f"unsafe attachment filename: {attachment.filename!r}")

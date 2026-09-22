""":mod:`tender_intelligence.mail.planner` — attachment-vs-link planner (v1.1 §5.7)."""

from __future__ import annotations

from dataclasses import dataclass, field

from tender_intelligence.mail.message import MailAttachment
from tender_intelligence.mail.provider import Capabilities

MB = 1024 * 1024


@dataclass(frozen=True)
class PlanResult:
    """What a provider can carry directly vs. what must become secure links.

    Attributes:
        attach: Attachments to send inline with the message.
        link_filenames: Names of files that must be served as expiring secure links instead.
        total_attached_bytes: Sum of inline attachment bytes.
        over_message_limit: True when even the chosen inline set exceeds the message cap
            (caller should fall back to a provider with higher limits or send links only).
    """

    attach: tuple[MailAttachment, ...] = field(default_factory=tuple)
    link_filenames: tuple[str, ...] = field(default_factory=tuple)
    total_attached_bytes: int = 0
    over_message_limit: bool = False


class AttachmentPlanner:
    """Decide which documents attach inline vs. go as secure links.

    Order of precedence (v1.1 §5.7): if the provider can carry everything, attach all; else
    attach what fits, linked remainder. If the provider has no limits, everything attaches.
    """

    def __init__(self, capabilities: Capabilities) -> None:
        self.capabilities = capabilities

    def plan(self, attachments: list[MailAttachment]) -> PlanResult:
        max_count = self.capabilities.max_attachments
        max_each = (
            self.capabilities.max_attachment_mb * MB
            if self.capabilities.max_attachment_mb is not None
            else None
        )
        max_message = (
            self.capabilities.max_message_mb * MB
            if self.capabilities.max_message_mb is not None
            else None
        )

        attach: list[MailAttachment] = []
        linked: list[str] = []
        total = 0

        for attachment in attachments:
            if max_each is not None and attachment.size_bytes > max_each:
                linked.append(attachment.filename)
                continue
            if max_count is not None and len(attach) >= max_count:
                linked.append(attachment.filename)
                continue
            if max_message is not None and total + attachment.size_bytes > max_message:
                linked.append(attachment.filename)
                continue
            attach.append(attachment)
            total += attachment.size_bytes

        over = bool(max_message is not None and total > max_message)
        return PlanResult(
            attach=tuple(attach),
            link_filenames=tuple(linked),
            total_attached_bytes=total,
            over_message_limit=over,
        )

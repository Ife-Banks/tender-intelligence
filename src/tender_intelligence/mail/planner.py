"""Capability-aware attachment planning (v1.1 §5.7, docs/08 §8.8).

The planner separates three different measurements:

* ``max_attachment_mb`` is a limit on a document's raw bytes;
* ``max_message_mb`` is a limit on the final encoded mail payload, so base64 inflation
  (four encoded bytes for every three raw bytes) is included; and
* ``message_overhead_bytes`` lets the dispatcher account for headers, MIME boundaries and
  the rendered body before choosing what to attach.

Keeping those bases separate prevents a provider from receiving a message that looks small
when measured as raw files but is over its wire limit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from tender_intelligence.mail.message import MailAttachment
from tender_intelligence.mail.provider import Capabilities

MB = 1024 * 1024
BASE64_INFLATION = 4.0 / 3.0


def encoded_message_bytes(attachment: MailAttachment) -> int:
    """Return the exact base64/MIME attachment contribution for *attachment*."""

    # Base64 emits four characters per complete three-byte group, including padding.  Using
    # the group calculation avoids under-counting by one byte for some input sizes.
    return 4 * math.ceil(attachment.size_bytes / 3)


@dataclass(frozen=True)
class PlanResult:
    """The exact inline-vs-secure-link decision for one provider.

    ``attach`` and ``link_filenames`` are deliberately separate.  A caller must persist the
    resulting two collections independently; merely mentioning a link in the email is not a
    record of what was delivered.
    """

    attach: tuple[MailAttachment, ...] = field(default_factory=tuple)
    link_filenames: tuple[str, ...] = field(default_factory=tuple)
    total_attached_bytes: int = 0
    total_encoded_bytes: int = 0
    total_message_bytes: int = 0
    over_message_limit: bool = False

    @property
    def fits_whole_set(self) -> bool:
        """Whether all supplied documents can be carried inline.

        ``over_message_limit`` is true when even the base body/overhead cannot fit.  An empty
        set is a whole-set fit when the base payload also fits.
        """

        return not self.link_filenames and not self.over_message_limit

    # ``links`` is a useful, explicit alias for callers that use the specification's wording.
    @property
    def links(self) -> tuple[str, ...]:
        return self.link_filenames


class AttachmentPlanner:
    """Decide which documents fit a provider and which require secure links.

    The input order is retained.  If a document does not fit, the planner continues looking
    for smaller documents that do fit; this is the specified ``attach what fits`` behaviour and
    avoids wasting provider capacity merely because one large addendum appears first.
    """

    def __init__(self, capabilities: Capabilities) -> None:
        self.capabilities = capabilities

    def plan(
        self,
        attachments: Sequence[MailAttachment],
        *,
        message_overhead_bytes: int = 0,
    ) -> PlanResult:
        """Plan *attachments* against the configured capabilities.

        ``message_overhead_bytes`` includes the rendered text/HTML and an estimate for MIME
        headers/boundaries.  It defaults to zero for backwards compatibility with the original
        planner API; production callers should pass a non-zero estimate.
        """

        if message_overhead_bytes < 0:
            raise ValueError("message_overhead_bytes must be >= 0")
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
        raw_total = 0
        encoded_total = 0
        individual_over = False
        base_fits = max_message is None or message_overhead_bytes <= max_message

        for attachment in attachments:
            if max_each is not None and attachment.size_bytes > max_each:
                linked.append(attachment.filename)
                continue
            if max_count is not None and len(attach) >= max_count:
                linked.append(attachment.filename)
                continue

            encoded = encoded_message_bytes(attachment)
            if max_message is not None and (
                message_overhead_bytes + encoded_total + encoded > max_message
            ):
                individual_over = True
                linked.append(attachment.filename)
                continue

            attach.append(attachment)
            raw_total += attachment.size_bytes
            encoded_total += encoded

        total_message = message_overhead_bytes + encoded_total
        over = individual_over or (max_message is not None and total_message > max_message)
        # A body that is already over the cap is a configuration/message-size failure even if
        # there are no documents to attach.
        if not base_fits:
            over = True
        return PlanResult(
            attach=tuple(attach),
            link_filenames=tuple(linked),
            total_attached_bytes=raw_total,
            total_encoded_bytes=encoded_total,
            total_message_bytes=total_message,
            over_message_limit=over,
        )

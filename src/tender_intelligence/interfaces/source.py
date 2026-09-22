""":mod:`tender_intelligence.interfaces.source` — SourceAdapter seam (docs/05, docs/02 §2.9).

One adapter per ``source_type``. Adapters are stateless with respect to seen state; the
pipeline owns deduplication against ``(source_id, external_id)``. No source-specific logic
of any kind may leak beyond an adapter (PROJECT_RULES #10).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from tender_intelligence.core.errors import SOURCE_UNREACHABLE


@dataclass(frozen=True)
class TenderAttachment:
    """One document link on a tender's detail page, awaiting the fetcher (docs/05 §5.2).

    ``is_zip`` marks a bundle/archive candidate (prompt 07 §3); a ZIP is still one
    top-level attachment — inner files are never enumerated here. ``advertised_size_bytes``
    is the size the source page announces, if any; never a measured byte count.
    ``checksum`` is always unknown until bytes are acquired (prompt 08) and is therefore
    not part of this metadata-only DTO (prompt 07 §2).
    """

    source_url: str
    filename: str
    mime_type: str | None = None
    advertised_size_bytes: int | None = None
    is_zip: bool = False


@dataclass(frozen=True)
class TenderListing:
    """Minimal per-listing row extracted during discovery (docs/05 §5.2, DM Tender)."""

    external_id: str
    title: str
    url: str
    published_at: datetime | None = None
    deadline_at: datetime | None = None
    deadline_timezone: str | None = None
    raw_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TenderDetail:
    """Full detail-page payload (docs/05 §5.2, DM Tender.raw_metadata)."""

    listing: TenderListing
    procuring_body: str | None = None
    reference_numbers: list[str] = field(default_factory=list)
    scope: str | None = None
    requirements_hints: list[str] = field(default_factory=list)
    attachments: list[TenderAttachment] = field(default_factory=list)
    raw_html: str | None = None


class SourceError(Exception):
    """Adapter-level failure carrying a structured error code (docs/05 §5.2 Error handling)."""

    def __init__(
        self,
        message: str,
        error_code: str = SOURCE_UNREACHABLE,
        *,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.context = context or {}


class SourceAdapter(ABC):
    """Abstract interface for a tender source (v1.1 §5.1 design note).

    Concrete adapters implement exactly these three operations; everything else
    (pagination, polite pacing, polite backoff, language handling) is adapter-internal
    under the rules in docs/05.
    """

    source_type: str = "abstract"

    @abstractmethod
    def list_new_tenders(self) -> list[TenderListing]:
        """Enumerate listing rows not yet known to the caller.

        The adapter is stateless w.r.t. seen state; the pipeline supplies the
        already-seen set and keeps the (source_id, external_id) dedupe key.
        Raise :class:`SourceError` with a structured code on failure.
        """

    @abstractmethod
    def get_detail(self, tender_id: str) -> TenderDetail:
        """Fetch a tender's full detail page (docs/05 §5.2 Detail-page extraction)."""

    @abstractmethod
    def get_attachments(self, tender_id: str) -> list[TenderAttachment]:
        """Enumerate every document link the detail page exposes (prompt 07 §2, §3).

        One top-level :class:`TenderAttachment` per link — including ZIPs/bundles, which
        are never unpacked here and never enumerated as inner files (that is the
        acquisition/understanding stage's job, docs/06 §6.2, prompt 09). Return metadata
        only; raise :class:`SourceError` with a structured code on failure.
        """

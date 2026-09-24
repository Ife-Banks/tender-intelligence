""":mod:`tender_intelligence.deadline.model` — canonical deadline value objects (Prompt 12.1).

A ``DeadlineResult`` is the output of every resolution path.  It is a pure value — no ORM,
no I/O.  The orchestrator hands it to the ``TenderRepository.apply_deadline_resolution``
method which writes the three new provenance columns (migration 0010) and the existing
``deadline`` / ``deadline_timezone`` columns in one commit.

Resolution statuses
-------------------
``RESOLVED``
    A single authoritative deadline was established from one source.

``UNRESOLVED``
    No parseable deadline found anywhere.  ``deadline_utc`` will be ``None``.
    A machine-readable ``reason_code`` explains why.

``CONFLICTING``
    At least two sources gave different deadlines.  ``deadline_utc`` is ``None``;
    the evidence blob carries the competing values so a human can arbitrate.

Evidence security
-----------------
``evidence`` is a bounded JSON-safe dict:

*  ``original_text``   — the raw text fragment that was parsed (≤ 200 chars)
*  ``source_url``      — the URL the text was taken from (if applicable)
*  ``document_id``     — DB id when the source was a document row
*  ``filename``        — document filename when source was a document
*  ``reason_code``     — machine-readable failure code when not RESOLVED
*  ``conflict_a`` / ``conflict_b`` — competing sources when CONFLICTING

Never put secrets, raw document bodies, or AI output in the evidence blob.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

# ── resolution status constants ───────────────────────────────────────────────
RESOLVED:    Final[str] = "resolved"
UNRESOLVED:  Final[str] = "unresolved"
CONFLICTING: Final[str] = "conflicting"

# ── deadline source constants (mirror Tender.DEADLINE_SOURCES) ────────────────
SOURCE_LISTING:     Final[str] = "listing_page"
SOURCE_DETAIL:      Final[str] = "detail_page"
SOURCE_DOCUMENT:    Final[str] = "document"
SOURCE_CONFLICTING: Final[str] = "conflicting"
SOURCE_UNRESOLVED:  Final[str] = "unresolved"

# ── reason codes for unresolved / conflicting ─────────────────────────────────
REASON_NOT_PRESENT_ON_LISTING:   Final[str] = "NOT_PRESENT_ON_LISTING"
REASON_NOT_PRESENT_ON_DETAIL:    Final[str] = "NOT_PRESENT_ON_DETAIL"
REASON_NOT_FOUND_IN_DOCUMENTS:   Final[str] = "NOT_FOUND_IN_DOCUMENTS"
REASON_UNPARSEABLE:              Final[str] = "UNPARSEABLE"
REASON_CONFLICTING_SOURCES:      Final[str] = "CONFLICTING_SOURCES"
REASON_MISSING_TIMEZONE:         Final[str] = "MISSING_TIMEZONE"

#: Maximum characters stored in evidence.original_text (bounded by design).
EVIDENCE_TEXT_LIMIT: Final[int] = 200


@dataclass(frozen=True)
class DeadlineResult:
    """Outcome of one deadline resolution attempt (Prompt 12.1).

    ``status`` is always RESOLVED, UNRESOLVED, or CONFLICTING.
    ``deadline_utc`` is set only when ``status == RESOLVED``.
    ``deadline_timezone`` carries the original tz abbreviation (e.g. "GMT") when known.
    ``source`` is the DEADLINE_SOURCES value recorded on the Tender row.
    ``evidence`` is a bounded JSON-safe dict — never secrets or large content.
    """

    status: str                              # RESOLVED | UNRESOLVED | CONFLICTING
    source: str                              # listing_page | detail_page | document | …
    deadline_utc: datetime | None            # UTC-aware datetime, or None
    deadline_timezone: str | None            # raw tz string from the source, or None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in (RESOLVED, UNRESOLVED, CONFLICTING):
            raise ValueError(f"invalid status: {self.status!r}")
        if self.status == RESOLVED and self.deadline_utc is None:
            raise ValueError("RESOLVED result must carry a deadline_utc")
        if self.status != RESOLVED and self.deadline_utc is not None:
            raise ValueError("non-RESOLVED result must not carry a deadline_utc")

    @property
    def is_resolved(self) -> bool:
        return self.status == RESOLVED

    @classmethod
    def resolved(
        cls,
        deadline_utc: datetime,
        source: str,
        *,
        deadline_timezone: str | None = None,
        original_text: str | None = None,
        source_url: str | None = None,
        document_id: int | None = None,
        filename: str | None = None,
    ) -> "DeadlineResult":
        evidence: dict[str, Any] = {}
        if original_text:
            evidence["original_text"] = original_text[:EVIDENCE_TEXT_LIMIT]
        if source_url:
            evidence["source_url"] = source_url
        if document_id is not None:
            evidence["document_id"] = document_id
        if filename:
            evidence["filename"] = filename
        return cls(
            status=RESOLVED,
            source=source,
            deadline_utc=deadline_utc,
            deadline_timezone=deadline_timezone,
            evidence=evidence,
        )

    @classmethod
    def unresolved(
        cls,
        reason_code: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> "DeadlineResult":
        evidence: dict[str, Any] = {"reason_code": reason_code}
        if extra:
            evidence.update(extra)
        return cls(
            status=UNRESOLVED,
            source=SOURCE_UNRESOLVED,
            deadline_utc=None,
            deadline_timezone=None,
            evidence=evidence,
        )

    @classmethod
    def conflicting(
        cls,
        conflict_a: dict[str, Any],
        conflict_b: dict[str, Any],
    ) -> "DeadlineResult":
        evidence: dict[str, Any] = {
            "reason_code": REASON_CONFLICTING_SOURCES,
            "conflict_a": conflict_a,
            "conflict_b": conflict_b,
        }
        return cls(
            status=CONFLICTING,
            source=SOURCE_CONFLICTING,
            deadline_utc=None,
            deadline_timezone=None,
            evidence=evidence,
        )

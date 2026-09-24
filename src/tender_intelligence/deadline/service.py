""":mod:`tender_intelligence.deadline.service` — Deadline Resolution Service (Prompt 12.1).

Implements the deterministic source hierarchy:

  1. Listing page    — ``TenderListing.deadline_at`` from Stage 04 discovery.
  2. Tender detail   — ``TenderDetail.listing.deadline_at`` from Stage 07 detail fetch.
  3. Document text   — extracted from Prompt 09 bundle artifacts, if available.
  4. UNRESOLVED      — if no source found a parseable deadline.

Conflict detection: if the listing-page deadline and detail-page deadline both resolve
but disagree beyond a configurable tolerance, the result is CONFLICTING and neither value
is written as canonical.  The detail page is treated as the higher-authority source, so
a listing/detail disagreement always favours the detail when the delta is within tolerance.

This service is **pure computation** given its inputs — no ORM sessions, no HTTP calls.
The caller (orchestrator coordinator) supplies the parsed DTOs and bundle artifacts.
TenderRepository.apply_deadline_resolution() owns the write side.

Ownership boundaries (Prompt 12.1 §3):
  - Does NOT crawl WAHO.
  - Does NOT acquire documents.
  - Does NOT call get_detail() or get_attachments().
  - Does NOT touch RunHistory.
  - Does NOT modify notification behaviour.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from tender_intelligence.deadline.extractor import extract_deadline_from_text
from tender_intelligence.deadline.model import (
    CONFLICTING,
    REASON_CONFLICTING_SOURCES,
    REASON_MISSING_TIMEZONE,
    REASON_NOT_FOUND_IN_DOCUMENTS,
    REASON_NOT_PRESENT_ON_DETAIL,
    REASON_NOT_PRESENT_ON_LISTING,
    RESOLVED,
    SOURCE_CONFLICTING,
    SOURCE_DETAIL,
    SOURCE_LISTING,
    DeadlineResult,
)
from tender_intelligence.interfaces.source import TenderDetail, TenderListing

log = logging.getLogger("tender_intelligence.deadline.service")

#: Maximum delta (seconds) between listing and detail deadlines that is considered
#: "same deadline, different precision" rather than a true conflict.  14 minutes covers
#: rounding differences in human-text representations (e.g. "1.00 pm" vs "13:00:00").
CONFLICT_TOLERANCE_SECONDS: Final[int] = 900  # 15 minutes


def _deadline_from_listing(listing: TenderListing, source: str = SOURCE_LISTING) -> DeadlineResult | None:
    """Extract a resolved deadline from a TenderListing DTO.

    Returns None when no deadline is present (not an error — the caller falls through
    to the next source).  Returns UNRESOLVED when a timezone indicator was found but
    the timestamp could not be emitted (the existing WAHO adapter rule: no naive
    timestamps — see sources/waho.py _parse_deadline()).
    """
    if listing.deadline_at is not None:
        return DeadlineResult.resolved(
            deadline_utc=listing.deadline_at.astimezone(UTC),
            source=source,
            deadline_timezone=listing.deadline_timezone,
            original_text=(listing.raw_metadata or {}).get("deadline_raw"),
            source_url=listing.url,
        )

    # deadline_at is None — check whether a raw deadline text existed but lacked timezone
    raw = (listing.raw_metadata or {}).get("deadline_raw")
    if raw:
        return DeadlineResult.unresolved(
            REASON_MISSING_TIMEZONE,
            extra={
                "original_text": str(raw)[:200],
                "source_url": listing.url,
            },
        )
    return None


def _deadline_from_detail(detail: TenderDetail) -> DeadlineResult | None:
    """Extract a resolved deadline from a TenderDetail DTO (uses its embedded listing)."""
    return _deadline_from_listing(detail.listing, source=SOURCE_DETAIL)


def _deadlines_conflict(a: DeadlineResult, b: DeadlineResult) -> bool:
    """Return True when two RESOLVED results disagree beyond tolerance."""
    if a.deadline_utc is None or b.deadline_utc is None:
        return False
    delta = abs((a.deadline_utc - b.deadline_utc).total_seconds())
    return delta > CONFLICT_TOLERANCE_SECONDS


def _evidence_for(result: DeadlineResult, label: str) -> dict:
    """Compact evidence blob for a conflict record (no secrets)."""
    return {
        "source": result.source,
        "deadline_utc": result.deadline_utc.isoformat() if result.deadline_utc else None,
        "deadline_timezone": result.deadline_timezone,
        "original_text": result.evidence.get("original_text", "")[:200],
        "source_url": result.evidence.get("source_url", ""),
    }


class DeadlineResolutionService:
    """Resolves the canonical deadline for one tender (Prompt 12.1).

    Inputs:
      - ``listing``      : TenderListing from Stage 04 discovery.
      - ``detail``       : TenderDetail from Stage 07 fetch (may be None if fetch failed).
      - ``bundle_docs``  : sequence of (document_id, filename, extracted_text) tuples from
                           the Prompt 09 extraction bundle (may be empty).

    All inputs are optional — the service gracefully falls through missing sources.
    """

    def resolve(
        self,
        listing: TenderListing,
        *,
        detail: TenderDetail | None = None,
        bundle_docs: list[tuple[int, str, str]] | None = None,
        correlation_id: str | None = None,
    ) -> DeadlineResult:
        """Run the source hierarchy and return the best available DeadlineResult.

        Resolution order (Prompt 12.1 §6):
          1. listing_page deadline
          2. detail_page deadline   (if detail fetch succeeded and differs from listing)
          3. document text          (if bundle_docs available and previous sources empty)
          4. UNRESOLVED

        Conflict detection:
          If listing and detail both resolve but disagree beyond tolerance →
          CONFLICTING (neither value written as canonical).

        Never guesses: no value is synthesised from a publication date, today's date,
        or any approximation.
        """
        cid = correlation_id or ""
        log_extra = {"stage": "deadline_resolution", "correlation_id": cid}

        # ── Source 1: listing page ─────────────────────────────────────────
        listing_result = _deadline_from_listing(listing)

        # ── Source 2: detail page ──────────────────────────────────────────
        detail_result: DeadlineResult | None = None
        if detail is not None:
            detail_result = _deadline_from_detail(detail)

        # ── Conflict check ─────────────────────────────────────────────────
        if (
            listing_result is not None
            and listing_result.is_resolved
            and detail_result is not None
            and detail_result.is_resolved
            and _deadlines_conflict(listing_result, detail_result)
        ):
            log.warning(
                "conflicting deadlines from listing (%s) and detail (%s)",
                listing_result.deadline_utc,
                detail_result.deadline_utc,
                extra={**log_extra, "status": "conflict"},
            )
            return DeadlineResult.conflicting(
                conflict_a=_evidence_for(listing_result, "listing"),
                conflict_b=_evidence_for(detail_result, "detail"),
            )

        # ── Detail page wins when listing had no deadline ──────────────────
        # When listing resolved, prefer it (it was already stored at discovery time).
        # When listing did not resolve but detail does, use detail.
        if listing_result is not None and listing_result.is_resolved:
            log.info(
                "deadline resolved from listing page: %s (%s)",
                listing_result.deadline_utc,
                listing_result.deadline_timezone,
                extra={**log_extra, "status": "resolved", "source": SOURCE_LISTING},
            )
            return listing_result

        if detail_result is not None and detail_result.is_resolved:
            log.info(
                "deadline resolved from detail page: %s (%s)",
                detail_result.deadline_utc,
                detail_result.deadline_timezone,
                extra={**log_extra, "status": "resolved", "source": SOURCE_DETAIL},
            )
            return detail_result

        # ── Source 3: document text ────────────────────────────────────────
        docs = bundle_docs or []
        for doc_id, filename, text in docs:
            if not text:
                continue
            doc_result = extract_deadline_from_text(
                text, document_id=doc_id, filename=filename
            )
            if doc_result is None:
                continue  # no deadline language in this document
            if doc_result.is_resolved:
                log.info(
                    "deadline resolved from document %s: %s",
                    filename,
                    doc_result.deadline_utc,
                    extra={**log_extra, "status": "resolved", "source": "document"},
                )
                return doc_result
            # UNRESOLVED result from document — log and continue to next doc
            log.debug(
                "deadline language found in document %s but not parseable: %s",
                filename,
                doc_result.evidence.get("reason_code"),
                extra={**log_extra, "status": "unresolved"},
            )

        # ── Source 4: UNRESOLVED ───────────────────────────────────────────
        # Build the most informative reason code.
        if listing_result is not None:
            # Listing had deadline text but couldn't parse it (missing tz, unparseable)
            reason = listing_result.evidence.get("reason_code", REASON_MISSING_TIMEZONE)
            extra = dict(listing_result.evidence)
        elif detail_result is not None:
            reason = detail_result.evidence.get("reason_code", REASON_NOT_PRESENT_ON_DETAIL)
            extra = dict(detail_result.evidence)
        elif docs:
            reason = REASON_NOT_FOUND_IN_DOCUMENTS
            extra = {}
        else:
            reason = REASON_NOT_PRESENT_ON_LISTING
            extra = {}

        log.info(
            "deadline unresolved: %s",
            reason,
            extra={**log_extra, "status": "unresolved", "reason_code": reason},
        )
        return DeadlineResult.unresolved(reason, extra=extra or None)

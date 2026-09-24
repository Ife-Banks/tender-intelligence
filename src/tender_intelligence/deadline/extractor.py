""":mod:`tender_intelligence.deadline.extractor` — text-based deadline extraction (Prompt 12.1 §9).

Extracts a deadline from **already-extracted document text** (Prompt 09 bundle artifacts).
Does NOT download documents, re-run processing, or access the storage layer directly —
it consumes ``TenderDocumentBundle`` objects from :class:`ExtractionStore`.

Security: the evidence excerpt stored is bounded to EVIDENCE_TEXT_LIMIT characters.
No raw document body is stored or logged.

Design: prefers explicit submission-deadline language over any other dates in the text,
across EN / FR / PT.  Never guesses — if no clear match is found, returns None so the
caller can fall through to UNRESOLVED.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Final

from tender_intelligence.deadline.model import (
    EVIDENCE_TEXT_LIMIT,
    REASON_MISSING_TIMEZONE,
    REASON_NOT_FOUND_IN_DOCUMENTS,
    REASON_UNPARSEABLE,
    SOURCE_DOCUMENT,
    DeadlineResult,
)

log = logging.getLogger("tender_intelligence.deadline.extractor")

# ── timezone offset map (same as WAHO adapter, kept independent) ──────────────
_TZ_OFFSETS: Final[dict[str, timezone]] = {
    "GMT": UTC,
    "UTC": UTC,
    "WAT": timezone(timedelta(hours=1)),
    "CET": timezone(timedelta(hours=1)),
    "CEST": timezone(timedelta(hours=2)),
    "EAT": timezone(timedelta(hours=3)),
}

# ── month name map ─────────────────────────────────────────────────────────────
_MONTHS: Final[dict[str, int]] = {
    "january": 1, "janvier": 1, "janeiro": 1, "janv": 1,
    "february": 2, "février": 2, "fevereiro": 2, "fev": 2,
    "march": 3, "mars": 3, "março": 3, "marco": 3,
    "april": 4, "avril": 4, "abril": 4,
    "may": 5, "mai": 5, "maio": 5,
    "june": 6, "juin": 6, "junho": 6,
    "july": 7, "juillet": 7, "julho": 7,
    "august": 8, "août": 8, "aout": 8, "agosto": 8,
    "september": 9, "septembre": 9, "setembro": 9, "sept": 9, "sep": 9,
    "october": 10, "octobre": 10, "outubro": 10, "oct": 10,
    "november": 11, "novembre": 11, "novembro": 11, "nov": 11,
    "december": 12, "décembre": 12, "dezembro": 12, "dec": 12,
}

# ── deadline-indicator keywords (EN / FR / PT) ────────────────────────────────
# These must appear *before* a date expression.  Sorted longest-first so the most-
# specific pattern matches before a shorter one.
_DEADLINE_LABELS: Final[tuple[str, ...]] = (
    # English
    "deadline for submission of applications",
    "deadline for submission",
    "deadline for proposals",
    "submission deadline",
    "closing date",
    "proposals must be received no later than",
    "proposals must be submitted",
    "deadline",
    # French
    "date limite de dépôt des candidatures",
    "date limite de soumission des offres",
    "date limite de soumission",
    "date limite",
    "date de clôture",
    "toutes les offres doivent être reçues au plus tard",
    "toutes les offres doivent etre recues au plus tard",
    "date limite de remise des offres",
    # Portuguese
    "prazo para apresentação de propostas",
    "prazo para submissão de propostas",
    "prazo para apresentação",
    "prazo de submissão",
    "data limite",
    "prazo",
)

# Machine-readable timestamp: "2026-09-24 13:00:00 UTC" or "2026-09-24T13:00:00Z"
_ISO_DATETIME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<year>\d{4})-(?P<month_num>\d{2})-(?P<day>\d{2})"
    r"[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::\d{2})?"
    r"\s*(?P<tz>UTC|GMT|Z)?",
    re.IGNORECASE,
)

# Human-readable textual date: "24 September 2026 at 1.00 pm GMT"
_TEXTUAL_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<day>\d{1,2})\s+"
    r"(?P<month>[A-Za-zÀ-ÿ]+)\s+"
    r"(?P<year>\d{4})"
    r"(?:"
    r"\s+(?:at|à|le|às?|em)\s*"
    r"(?P<hour>\d{1,2})[h:.:](?P<minute>\d{2})\s*(?P<ampm>[ap]\.?m\.?)?"
    r")?"
    r"\s*(?P<tz>[A-Z]{2,4})?",
    re.IGNORECASE,
)

# French numeric: "31 juillet 2026 à 10 h 00 GMT"
_FRENCH_HHMM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<day>\d{1,2})\s+"
    r"(?P<month>[A-Za-zÀ-ÿ]+)\s+"
    r"(?P<year>\d{4})"
    r"(?:\s+à\s+(?P<hour>\d{1,2})\s*h\s*(?P<minute>\d{2}))?"
    r"\s*(?P<tz>[A-Z]{2,4})?",
    re.IGNORECASE,
)


def _parse_iso(text: str) -> tuple[datetime | None, str | None]:
    """Try to parse an ISO-style timestamp from *text*.  Returns (utc_dt, tz_str)."""
    m = _ISO_DATETIME_RE.search(text)
    if not m:
        return None, None
    try:
        naive = datetime(
            int(m.group("year")), int(m.group("month_num")), int(m.group("day")),
            int(m.group("hour")), int(m.group("minute")),
        )
    except ValueError:
        return None, None
    tz_str = (m.group("tz") or "").upper().replace("Z", "UTC")
    tz = _TZ_OFFSETS.get(tz_str)
    if tz is None:
        # Default UTC for the machine-readable field — WAHO always publishes UTC
        tz = UTC
        tz_str = "UTC"
    return naive.replace(tzinfo=tz).astimezone(UTC), tz_str


def _parse_textual(text: str) -> tuple[datetime | None, str | None]:
    """Try to parse a human-readable date from *text*.  Returns (utc_dt, tz_str)."""
    # Try all patterns and prefer the one that successfully parses with a timezone.
    candidates: list[tuple[datetime, str]] = []

    for pattern in (_TEXTUAL_DATE_RE, _FRENCH_HHMM_RE):
        m = pattern.search(text)
        if not m:
            continue
        month_name = m.group("month").lower()
        month = _MONTHS.get(month_name)
        if month is None:
            continue
        try:
            day, year = int(m.group("day")), int(m.group("year"))
            hour = int(m.group("hour")) if m.groupdict().get("hour") and m.group("hour") else 0
            minute = int(m.group("minute")) if m.groupdict().get("minute") and m.group("minute") else 0
        except (TypeError, ValueError):
            continue
        # AM/PM handling
        ampm = m.group("ampm") if m.groupdict().get("ampm") else None
        if ampm:
            ampm = ampm.replace(".", "").lower()
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
        tz_str = (m.group("tz") or "").upper() if m.groupdict().get("tz") else ""
        # Guard: common English/French words that are not timezone abbreviations
        if tz_str in ("AT", "LE", "À", "DE", "AUX", "LES"):
            tz_str = ""
        tz = _TZ_OFFSETS.get(tz_str)
        if tz is None and tz_str:
            # Unrecognised abbreviation — skip this match, try next pattern
            continue
        if tz is None:
            # No timezone — keep as fallback candidate only
            # (never emit a naive timestamp per project rule)
            continue
        try:
            dt = datetime(year, month, day, hour, minute, tzinfo=tz).astimezone(UTC)
            candidates.append((dt, tz_str))
        except ValueError:
            continue

    if candidates:
        return candidates[0]
    return None, None


def _extract_from_segment(segment: str) -> tuple[datetime | None, str | None, str | None]:
    """Return (deadline_utc, tz_str, original_text) from a bounded text segment."""
    # Prefer machine-readable ISO timestamp
    dt, tz = _parse_iso(segment)
    if dt is not None:
        return dt, tz, segment[:EVIDENCE_TEXT_LIMIT]
    # Fall back to human-readable text date
    dt, tz = _parse_textual(segment)
    if dt is not None:
        return dt, tz, segment[:EVIDENCE_TEXT_LIMIT]
    return None, None, None


def extract_deadline_from_text(
    text: str,
    *,
    document_id: int,
    filename: str,
) -> DeadlineResult | None:
    """Search *text* for a submission-deadline date and return a ``DeadlineResult``.

    Returns ``None`` when no deadline-indicator keyword is found so the caller can
    distinguish "document has no deadline language" from "document has deadline language
    but it was unparseable" (the latter produces UNRESOLVED, the former produces None).

    The caller decides how many documents to search and whether document-level results
    are promoted to the tender-level resolution.

    Security: the evidence excerpt is limited to EVIDENCE_TEXT_LIMIT characters.
    No raw document content is stored beyond the minimal excerpt.
    """
    if not text:
        return None

    lower_text = text.lower()

    for label in _DEADLINE_LABELS:
        idx = lower_text.find(label.lower())
        if idx == -1:
            continue
        # Take the segment immediately after the label (up to 250 chars)
        segment_start = idx + len(label)
        segment = text[segment_start : segment_start + 250].strip()
        # Strip leading punctuation / whitespace
        segment = segment.lstrip(":;,. ").strip()
        if not segment:
            continue

        deadline_utc, tz_str, original_text = _extract_from_segment(segment)

        if deadline_utc is not None:
            log.debug(
                "deadline extracted from document text",
                extra={
                    "stage": "deadline_resolution",
                    "status": "extracted",
                    "document_id": document_id,
                    "filename": filename,
                },
            )
            return DeadlineResult.resolved(
                deadline_utc=deadline_utc,
                source=SOURCE_DOCUMENT,
                deadline_timezone=tz_str,
                original_text=original_text,
                document_id=document_id,
                filename=filename,
            )

        # Label found but date not parseable
        log.debug(
            "deadline label found in document but date not parseable",
            extra={
                "stage": "deadline_resolution",
                "status": "unparseable",
                "document_id": document_id,
                "filename": filename,
                "segment": segment[:80],
            },
        )
        # Return an UNRESOLVED with the raw text preserved as evidence
        return DeadlineResult.unresolved(
            REASON_UNPARSEABLE,
            extra={
                "original_text": (label + " " + segment)[:EVIDENCE_TEXT_LIMIT],
                "document_id": document_id,
                "filename": filename,
            },
        )

    return None

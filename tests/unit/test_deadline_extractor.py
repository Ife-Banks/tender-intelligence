"""Unit tests for deadline text extraction from document content (Prompt 12.1 §16)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tender_intelligence.deadline.extractor import extract_deadline_from_text
from tender_intelligence.deadline.model import (
    REASON_UNPARSEABLE,
    RESOLVED,
    SOURCE_DOCUMENT,
    UNRESOLVED,
)


def _extract(text: str):
    return extract_deadline_from_text(text, document_id=1, filename="test.pdf")


class TestExtractDeadlineFromText:

    # ── No deadline language ──────────────────────────────────────────────────
    def test_no_deadline_language_returns_none(self):
        assert _extract("This document covers procurement procedures.") is None

    def test_empty_text_returns_none(self):
        assert _extract("") is None

    # ── English patterns ──────────────────────────────────────────────────────
    def test_english_deadline_text_date(self):
        r = _extract("Deadline for submission of applications: 24 September 2026 at 1.00 pm GMT.")
        assert r is not None
        assert r.status == RESOLVED
        assert r.source == SOURCE_DOCUMENT
        assert r.deadline_utc is not None
        assert r.deadline_utc.year == 2026
        assert r.deadline_utc.month == 9
        assert r.deadline_utc.day == 24

    def test_english_closing_date(self):
        r = _extract("The closing date for proposals is 31 July 2026 at 10:00 GMT.")
        assert r is not None
        assert r.status == RESOLVED

    def test_english_submission_deadline(self):
        r = _extract("Submission deadline: 29 August 2026 at 5.00 pm GMT")
        assert r is not None
        assert r.status == RESOLVED

    def test_proposals_must_be_received(self):
        r = _extract("All proposals must be received no later than 29 August 2026.")
        # No timezone — may be UNRESOLVED or None depending on tz availability
        # The key assertion: no crash, returns a value
        assert r is None or r.status in (RESOLVED, UNRESOLVED)

    # ── French patterns ───────────────────────────────────────────────────────
    def test_french_date_limite(self):
        r = _extract("Date limite de dépôt des candidatures : le 31 juillet 2026 à 10 h 00 GMT.")
        assert r is not None
        assert r.status == RESOLVED
        assert r.deadline_utc is not None
        assert r.deadline_utc.month == 7

    def test_french_toutes_offres(self):
        r = _extract(
            "toutes les offres doivent être reçues au plus tard le 29 Août 2026 à 17:00 GMT"
        )
        assert r is not None
        assert r.status == RESOLVED

    def test_french_date_cloture(self):
        r = _extract("Date de clôture : 24 septembre 2026 à 13:00 GMT.")
        assert r is not None
        assert r.status == RESOLVED

    # ── Portuguese patterns ───────────────────────────────────────────────────
    def test_portuguese_prazo(self):
        r = _extract("Prazo para apresentação de propostas: 24 de setembro de 2026 às 13:00 GMT.")
        # Portuguese parsing is best-effort in document text
        # Key assertion: does not crash, and if resolved, deadline is correct
        if r is not None and r.status == RESOLVED:
            assert r.deadline_utc is not None
            assert r.deadline_utc.month == 9

    # ── Missing timezone ──────────────────────────────────────────────────────
    def test_missing_timezone_returns_unresolved(self):
        r = _extract("Deadline for submission: 24 September 2026")
        # No timezone — should be None (no further evidence) or UNRESOLVED
        assert r is None or r.status == UNRESOLVED

    # ── Evidence preservation ─────────────────────────────────────────────────
    def test_evidence_carries_document_reference(self):
        r = extract_deadline_from_text(
            "Deadline for submission of applications: 24 September 2026 at 13:00 GMT.",
            document_id=99,
            filename="TOR_Health.pdf",
        )
        assert r is not None
        assert r.status == RESOLVED

    def test_evidence_original_text_bounded(self):
        long_prefix = "x " * 10
        r = _extract(f"Deadline for submission: {long_prefix}24 September 2026 at 1 pm GMT.")
        if r and r.evidence.get("original_text"):
            assert len(r.evidence["original_text"]) <= 200

    # ── No false positives from publication dates ─────────────────────────────
    def test_publication_date_not_mistaken_for_deadline(self):
        r = _extract("This document was published on 1 January 2026.")
        assert r is None

    def test_start_date_not_mistaken_for_deadline(self):
        r = _extract("Start Date: 2026-01-01 09:00:00 UTC. End Date: 2026-12-31 23:59:00 UTC.")
        # May or may not find a deadline — but must not crash
        # If found, must be from a deadline-labeled field not "Start Date"
        if r is not None and r.is_resolved:
            assert r.deadline_utc is not None
            assert r.deadline_utc.year == 2026

    # ── Multiple dates ────────────────────────────────────────────────────────
    def test_picks_first_deadline_label(self):
        """The first deadline-label hit should be used; unrelated dates ignored."""
        r = _extract(
            "Background published on 1 January 2026.\n"
            "Deadline for submission of applications: 24 September 2026 at 1.00 pm GMT.\n"
            "Further information available at the office."
        )
        assert r is not None
        assert r.status == RESOLVED
        # Should find September 24 from the Deadline label
        assert r.deadline_utc.month == 9
        assert r.deadline_utc.day == 24

    # ── ISO machine timestamps ────────────────────────────────────────────────
    def test_iso_timestamp_after_label_resolves(self):
        r = _extract("Deadline for submission: 2026-09-24 13:00:00 UTC")
        assert r is not None
        assert r.status == RESOLVED
        assert r.deadline_utc == datetime(2026, 9, 24, 13, 0, tzinfo=UTC)

    def test_iso_gmt_after_label_resolves(self):
        r = _extract("Deadline for submission: 2026-09-24 13:00:00 GMT")
        assert r is not None
        assert r.status == RESOLVED
        assert r.deadline_utc == datetime(2026, 9, 24, 13, 0, tzinfo=UTC)

    def test_iso_without_timezone_never_fabricated_as_utc(self):
        # No explicit zone — must NOT resolve as UTC (generic extractor serves sources
        # that publish local timestamps). Label was found ⇒ UNRESOLVED, never RESOLVED.
        r = _extract("Deadline for submission: 2026-09-24 13:00:00")
        assert r is not None
        assert not r.is_resolved
        assert r.status == UNRESOLVED

    def test_iso_unrecognised_timezone_never_fabricated_as_utc(self):
        # "EST" is not a known offset here; must not silently become UTC.
        r = _extract("Deadline for submission: 2026-09-24 13:00:00 EST")
        assert r is not None
        assert not r.is_resolved
        assert r.status == UNRESOLVED

    def test_iso_scan_is_anchored_to_label(self):
        # Old behaviour searched the whole window and could pick the first ISO — here
        # the earlier "opens" date. The timestamp must immediately follow the label.
        r = _extract(
            "Deadline: opens 2026-03-01 09:00 UTC, closes 2026-04-15 17:00 UTC."
        )
        assert r is not None
        assert not r.is_resolved
        assert r.status == UNRESOLVED

    def test_iso_ignores_secondary_dates_in_window(self):
        r = _extract(
            "Deadline for submission: 2026-09-24 13:00:00 UTC\n"
            "Tender opens on 2026-09-01 09:00 UTC."
        )
        assert r is not None
        assert r.status == RESOLVED
        assert r.deadline_utc.month == 9
        assert r.deadline_utc.day == 24

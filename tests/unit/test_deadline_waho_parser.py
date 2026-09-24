"""Tests for the WAHO adapter End Date parsing (Prompt 12.1 §16).

Verifies the machine-readable ``End Date: YYYY-MM-DD HH:MM:SS UTC`` pattern that was
present on all live WAHO tenders but missing from the fixture-based deadline_patterns.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tender_intelligence.sources.waho import WahoPaginatedAdapter


def _adapter() -> WahoPaginatedAdapter:
    return WahoPaginatedAdapter("https://data.wahooas.org/tenders/tenders/list")


class TestEndDatePattern:
    """The machine-readable End Date field (LIVE-VERIFIED 2026-09-24)."""

    def test_end_date_utc_parsed(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "End Date: 2026-09-24 13:00:00 UTC"
        )
        assert dl is not None
        assert dl == datetime(2026, 9, 24, 13, 0, tzinfo=UTC)
        assert tz == "UTC"
        assert raw is not None

    def test_end_date_gmt_parsed(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "End Date: 2026-07-31 10:00:00 GMT"
        )
        assert dl == datetime(2026, 7, 31, 10, 0, tzinfo=UTC)
        assert tz == "GMT"

    def test_end_date_embedded_in_card_text(self):
        """Listing card text includes surrounding content — End Date must still parse."""
        adapter = _adapter()
        card_text = (
            "Reference: REF-123 Name: Senior Health Officer "
            "Description: EOI for medical services. "
            "End Date: 2027-12-31 00:00:00 UTC "
            "Tender Documents MPDER-MR AMI MTN.pdf"
        )
        dl, tz, raw = adapter._parse_deadline(card_text)
        assert dl == datetime(2027, 12, 31, 0, 0, tzinfo=UTC)

    def test_end_date_with_different_timestamp(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "End Date: 2026-08-29 23:59:00 UTC"
        )
        assert dl == datetime(2026, 8, 29, 23, 59, tzinfo=UTC)


class TestFrenchDeadlinePatterns:
    """Human-readable FR deadline in trix-content."""

    def test_date_limite_with_time_gmt(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Date limite de dépôt des candidatures : le 24 September 2026 at 1.00 pm GMT."
        )
        assert dl is not None
        assert dl.year == 2026
        assert dl.month == 9
        assert dl.day == 24
        assert tz == "GMT"

    def test_date_limite_july_gmt(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Date limite de dépôt des candidatures : 31 juillet 2026 à 10 h 00 GMT."
        )
        assert dl is not None
        assert dl.month == 7
        assert dl.day == 31

    def test_french_no_timezone_returns_none_datetime(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Date limite : 24 septembre 2026"
        )
        # No tz → must not emit a naive timestamp
        assert dl is None
        assert raw is not None  # raw text preserved


class TestEnglishDeadlinePatterns:
    def test_deadline_for_submission_gmt(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Deadline for submission of applications: 24 September 2026 at 1.00 pm GMT."
        )
        assert dl is not None
        assert dl.month == 9
        assert dl.day == 24
        assert tz == "GMT"

    def test_deadline_no_time(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Deadline for submission of applications: 29 August 2026 GMT"
        )
        if dl is not None:
            assert dl.month == 8


class TestPortugueseDeadlinePatterns:
    def test_prazo_gmt(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Prazo para apresentação de propostas: 29 de agosto de 2026, às 23:59 GMT"
        )
        assert dl is not None
        assert dl.month == 8


class TestNoDeadline:
    def test_no_deadline_returns_none(self):
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Reference: REF-123 Name: Senior Health Officer Description: EOI for medical."
        )
        assert dl is None
        assert tz is None
        assert raw is None

    def test_publication_date_not_mistaken(self):
        """Start Date must not be captured as the deadline."""
        adapter = _adapter()
        dl, tz, raw = adapter._parse_deadline(
            "Start Date: 2026-01-01 09:00:00 UTC"
        )
        # Start Date is not a deadline label — should not parse
        assert dl is None


class TestDetailTitleSelectors:
    """Live-verified: div.card-header a is the primary title selector."""

    def test_live_title_selector_is_first(self):
        adapter = _adapter()
        selectors = adapter._config["detail_title_selectors"]
        assert selectors[0] == "div.card-header a", (
            "Live-verified selector must be first; fixture fallbacks follow"
        )

    def test_detail_body_selector_covers_card_body(self):
        adapter = _adapter()
        assert adapter._config["detail_body_selector"] == "div.card-body"

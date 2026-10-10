"""Unit tests for the Admin API tender DTO.

The console renders a tender's deadline and input-completeness columns directly from
these fields, so a mismatch between what is stored and what is published is a silent
wrong-answer bug: the column shows nothing rather than raising.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tender_intelligence.admin.api import _deadline_view

_RESOLVED_AT = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


class _Resolution:
    """Stands in for a TenderDeadlineResolution row without needing a database."""

    def __init__(self, source: str, resolved: datetime | None) -> None:
        self.deadline_source = source
        self.deadline_resolved = resolved
        self.deadline_timezone = "Africa/Abidjan"


class TestDeadlineView:
    def test_no_resolution_row_is_unresolved(self) -> None:
        view = _deadline_view(None)
        assert view == {
            "state": "unresolved",
            "source": None,
            "utc": None,
            "timezone": None,
        }

    def test_a_provenance_source_is_resolved_and_keeps_its_provenance(self) -> None:
        """The console only shows a date when state is exactly "resolved".

        deadline_source holds provenance, so publishing it as the state meant the test
        for "resolved" could never pass and the Deadline column stayed empty for every
        tender whose date had in fact been read.
        """
        for provenance in ("listing_page", "detail_page", "document"):
            view = _deadline_view(_Resolution(provenance, _RESOLVED_AT))
            assert view["state"] == "resolved", provenance
            assert view["source"] == provenance
            assert view["utc"] == "2026-10-03T16:00:00+00:00"
            assert view["timezone"] == "Africa/Abidjan"

    def test_conflicting_sources_report_conflicting_not_resolved(self) -> None:
        view = _deadline_view(_Resolution("conflicting", None))
        assert view["state"] == "conflicting"
        assert view["source"] == "conflicting"
        assert view["utc"] is None

    def test_unresolved_source_reports_unresolved(self) -> None:
        view = _deadline_view(_Resolution("unresolved", None))
        assert view["state"] == "unresolved"
        assert view["utc"] is None

    def test_a_missing_timestamp_never_claims_a_resolved_deadline(self) -> None:
        """Provenance alone is not proof a date was resolved.

        A row can carry a source and no resolved value. Reporting that as "resolved"
        would show a blank date in a column that claims a date exists.
        """
        view = _deadline_view(_Resolution("detail_page", None))
        assert view["state"] == "unresolved"
        assert view["source"] == "detail_page"
        assert view["utc"] is None

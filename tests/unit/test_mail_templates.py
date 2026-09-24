"""Unit tests for the notification templates (docs/08 §8.1/§8.2/§8.15, prompt 11 §19–§22).

Rendering is deterministic: the same content always produces the same bytes, and verdict +
deadline pass through verbatim. The ``[TEST]`` prefix and URGENT marker compose per §8.15.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tender_intelligence.mail.templates import (
    LinkedDocument,
    NotificationContent,
    NotificationKind,
    base_subject,
    format_deadline,
    render_html,
    render_text,
    subject,
)
from tender_intelligence.notifications.test_mode import TEST_PREFIX

WAT_PLUS = timedelta(hours=1)  # Africa/Lagos is UTC+1, no DST


def _content(**overrides: object) -> NotificationContent:
    fields: dict[str, object] = {
        "source_name": "WAHO",
        "title": "Expression of Interest: Health Data Platform",
        "verdict": "APPLY",
        "confidence": 0.87,
        "background_summary": "The platform modernises national reporting.",
        "requirements_summary": ("Data schema", "KYC checks"),
        "gap_analysis": ("We can supply both.",),
        "deadline": datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
        "deadline_timezone": "UTC",
    }
    fields.update(overrides)
    return NotificationContent(**fields)


class TestSubjects:
    def test_new_subject(self) -> None:
        assert (
            base_subject(_content()) == "WAHO Expression of Interest – Assessment Report: "
            "Expression of Interest: Health Data Platform"
        )

    def test_update_subject_carries_what_changed(self) -> None:
        c = _content(kind=NotificationKind.UPDATE, update_summary="status updated to processed")
        assert base_subject(c) == (
            "WAHO UPDATE – Expression of Interest: Health Data Platform: "
            "status updated to processed"
        )

    def test_update_subject_without_change_detail(self) -> None:
        c = _content(kind=NotificationKind.UPDATE)
        assert base_subject(c) == "WAHO UPDATE – Expression of Interest: Health Data Platform"

    def test_test_mode_prefix(self) -> None:
        c = _content(test_mode=True)
        assert subject(c) == f"{TEST_PREFIX} {base_subject(c)}"
        assert subject(c).startswith(TEST_PREFIX)

    def test_urgent_marker(self) -> None:
        c = _content(urgent=True)
        assert f"URGENT {base_subject(c)}" == subject(c)

    def test_test_mode_and_urgent_compose(self) -> None:
        c = _content(test_mode=True, urgent=True)
        assert subject(c) == f"{TEST_PREFIX} URGENT {base_subject(c)}"

    def test_source_name_cannot_disguise_missing_test_marker(self) -> None:
        c = _content(test_mode=True, source_name="[TEST]FAKE")
        assert subject(c).startswith(f"{TEST_PREFIX} [TEST]FAKE ")

    def test_no_markers_plain_subject(self) -> None:
        assert subject(_content()) == base_subject(_content())


class TestDeadline:
    def test_naive_deadline_treated_as_utc_and_shown_explicitly(self) -> None:
        naive = datetime(2026, 10, 15, 12, 0)  # no tzinfo: pipeline rule = UTC
        rendered = format_deadline(naive, "UTC")
        # 12:00 UTC == 13:00 WAT (UTC+1), and the notice's stated value is kept explicit.
        assert "2026-10-15 13:00 WAT (WAT)" in rendered
        assert "stated in notice: 2026-10-15 12:00 UTC (UTC)" in rendered

    def test_aware_deadline_renders_both(self) -> None:
        aware = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
        rendered = format_deadline(aware, "UTC")
        assert "2026-10-15 13:00 WAT (WAT)" in rendered
        assert "stated in notice:" in rendered

    def test_no_source_timezone_still_shows_wat(self) -> None:
        rendered = format_deadline(datetime(2026, 10, 15, 12, 0, tzinfo=UTC), None)
        assert "13:00 WAT (WAT)" in rendered
        assert "stated in notice" not in rendered


class TestTextBody:
    def test_all_sections_present_in_order(self) -> None:
        body = render_text(_content())
        assert "Subject:" in body
        assert "1. Background" in body
        assert "2. Requirements for Tender" in body
        assert "3. Why We Can / Cannot Apply" in body
        assert body.index("1. Background") < body.index("2. Requirements for Tender")
        assert body.index("2. Requirements for Tender") < body.index("3. Why We Can / Cannot Apply")

    def test_verdict_and_confidence_verbatim(self) -> None:
        body = render_text(_content())
        assert "Final verdict: APPLY (confidence 87%)" in body

    def test_deadline_line_in_scoring_section(self) -> None:
        body = render_text(_content())
        assert "Deadline: 2026-10-15 13:00 WAT (WAT)" in body

    def test_notes_footer_states(self) -> None:
        now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        body = render_text(
            _content(
                attached_filenames=("annex.pdf",),
                linked_documents=(
                    LinkedDocument(
                        filename="big.zip",
                        url="https://archive.example.invalid/archive/1/2?e=1&sig=x",
                        expires_at=now + timedelta(days=14),
                        expiry_days=14,
                    ),
                ),
                failed_filenames=("broken.xlsx",),
                incomplete_inputs=True,
            )
        )
        assert "Documents attached: annex.pdf" in body
        assert "big.zip (expires 2026-10-07)" in body
        assert "Documents that failed to download/extract: broken.xlsx" in body
        assert "Note: the verdict was reached on incomplete inputs." in body

    def test_update_notes(self) -> None:
        body = render_text(
            _content(
                kind=NotificationKind.UPDATE,
                update_summary="deadline extended",
                previous_assessment_date="2026-09-01",
            )
        )
        assert "What changed: deadline extended" in body
        assert "The earlier assessment (of 2026-09-01) remains valid." in body

    def test_requirements_and_gap_defaults(self) -> None:
        body = render_text(_content(requirements_summary=(), gap_analysis=()))
        assert "- (no requirements summary available)" in body
        assert "- (no comparison analysis available)" in body

    def test_structured_evidence_retains_all_fields(self) -> None:
        body = render_text(
            _content(
                requirements_summary=(
                    {
                        "summary": "Must provide a data dictionary",
                        "evidence": "Annex A, page 4",
                        "qualification": "Preferred bidder",
                    },
                )
            )
        )
        assert "Must provide a data dictionary" in body
        assert "evidence: Annex A, page 4" in body
        assert "qualification: Preferred bidder" in body

    def test_deterministic(self) -> None:
        assert render_text(_content()) == render_text(_content())


class TestHtmlBody:
    def test_escapes_and_sections(self) -> None:
        body = render_html(_content(background_summary="Raw & <b>bold</b> text"))
        assert "<h1>" in body and "</h1>" in body
        assert "Raw &amp; &lt;b&gt;bold&lt;/b&gt; text" in body
        assert "<h2>1. Background</h2>" in body
        assert "<h2>3. Why We Can / Cannot Apply</h2>" in body

    def test_verdict_present(self) -> None:
        assert "Final verdict: APPLY" in render_html(_content())

    def test_secure_link_is_present_in_text_and_html(self) -> None:
        link = LinkedDocument(
            filename="annex.pdf",
            url="https://archive.example.invalid/archive/1/2?expires=1&sig=x",
            expires_at=datetime(2026, 10, 7, tzinfo=UTC),
            expiry_days=14,
        )
        content = _content(linked_documents=(link,))
        assert link.url in render_text(content)
        assert "secure download" in render_html(content)
        assert "archive.example.invalid" in render_html(content)

    def test_deterministic(self) -> None:
        assert render_html(_content()) == render_html(_content())

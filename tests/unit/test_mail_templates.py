"""Unit tests for the notification templates (docs/08 §8.1/§8.2/§8.15, prompt 11 §19–§22).

Rendering is deterministic: the same content always produces the same bytes, and verdict +
deadline pass through verbatim. The ``[TEST]`` prefix and URGENT marker compose per §8.15.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tender_intelligence.mail.templates import (
    ASSESSMENT_UNAVAILABLE_BANNER,
    NO_DOWNLOADABLE_SOURCES_BANNER,
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


class TestApplicabilityEvidence:
    """docs/07 §7.2/§7.3: section 3 states why we can or cannot apply, per requirement.

    Section 3 is the part a reader acts on. It previously rendered only the model's
    standalone gap strings, so it carried no citation and no statement of whether the
    company met anything - the notification gave the reader a verdict with nothing to
    assess it against.
    """

    ASSESSMENTS = (
        {
            "requirement": "Minimum average annual turnover of 70,000,000",
            "tender_evidence": (
                {
                    "document_id": 2,
                    "location": "section:29",
                    "quote": "Le chiffre d'affaires moyen annuel est de 70 000 000",
                },
            ),
            "company_evidence": (
                {
                    "section": "Financial Capacity",
                    "quote": "Audited average annual turnover of NGN 500,000,000.",
                },
            ),
            "status": "met",
            "assessment": "Audited turnover exceeds the threshold.",
            "gap": None,
        },
        {
            "requirement": "Bid guarantee of 1,500,000 from a recognised bank",
            "tender_evidence": (
                {
                    "document_id": 2,
                    "location": "section:23",
                    "quote": "une garantie de soumission delivree par une banque reconnue",
                },
            ),
            "company_evidence": (),
            "status": "unverified",
            "assessment": "No banking relationship is recorded.",
            "gap": "No evidence on file for a bid guarantee facility.",
        },
    )

    def _body(self, **overrides: object) -> str:
        return render_text(
            _content(requirements_summary=self.ASSESSMENTS, **overrides)
        )

    def test_section_three_cites_the_knowledge_base_for_every_met_claim(self) -> None:
        body = self._body(gap_analysis=())
        section = body.split("3. Why We Can / Cannot Apply", 1)[1]
        assert 'Company evidence (KB "Financial Capacity")' in section
        assert "Audited average annual turnover of NGN 500,000,000." in section

    def test_section_three_reports_status_per_requirement(self) -> None:
        section = self._body(gap_analysis=()).split("3. Why We Can / Cannot Apply", 1)[1]
        assert "Minimum average annual turnover of 70,000,000 - Met" in section
        assert "Unverified - no company evidence on file" in section

    def test_section_three_carries_the_gap_and_the_reasoning(self) -> None:
        section = self._body(gap_analysis=()).split("3. Why We Can / Cannot Apply", 1)[1]
        assert "Assessment: No banking relationship is recorded." in section
        assert "Gap: No evidence on file for a bid guarantee facility." in section

    def test_section_two_quotes_the_tender_itself(self) -> None:
        section = self._body(gap_analysis=()).split("2. Requirements for", 1)[1]
        section = section.split("3. Why We Can", 1)[0]
        assert "Minimum average annual turnover of 70,000,000" in section
        assert "Le chiffre d'affaires moyen annuel est de 70 000 000" in section
        # The company's own evidence belongs in section 3, not the tender's requirements.
        assert "Audited average annual turnover" not in section

    def test_gap_analysis_falls_back_when_no_assessments_are_available(self) -> None:
        body = render_text(
            _content(requirements_summary=(), gap_analysis=("We can supply both.",))
        )
        section = body.split("3. Why We Can / Cannot Apply", 1)[1]
        assert "We can supply both." in section

    def test_a_gap_is_not_repeated_when_the_assessment_already_states_it(self) -> None:
        body = self._body(
            gap_analysis=(
                {"gap": "No evidence on file for a bid guarantee facility."},
                {"gap": "No evidence on file for Portuguese-language capability."},
            )
        )
        section = body.split("3. Why We Can / Cannot Apply", 1)[1]
        assert section.count("No evidence on file for a bid guarantee facility.") == 1
        # A gap the model reported without attaching it to a requirement is still shown.
        assert "No evidence on file for Portuguese-language capability." in section

    def test_html_section_three_escapes_evidence(self) -> None:
        body = render_html(
            _content(
                requirements_summary=(
                    {
                        "requirement": "Scripted requirement",
                        "company_evidence": (
                            {"section": "Evidence", "quote": "a < b & c > d"},
                        ),
                        "status": "met",
                        "assessment": "Met & exceeded",
                        "gap": None,
                    },
                ),
                gap_analysis=(),
            )
        )
        assert "a &lt; b &amp; c &gt; d" in body
        assert "Met &amp; exceeded" in body
        assert "<script" not in body


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


class TestNoticeOnlyAssessment:
    """A verdict that exists but rests on a summary rather than on the tender document.

    This is not the same as "assessment unavailable" and must not read as one: there is a
    verdict here, and the reader needs to know what it rests on.
    """

    def _notice_only(self, **overrides: object) -> NotificationContent:
        return _content(
            notice_only_assessment=True,
            incomplete_inputs=True,
            confidence=0.65,
            **overrides,
        )

    def test_banner_names_the_missing_files_in_text(self) -> None:
        body = render_text(self._notice_only())
        assert NO_DOWNLOADABLE_SOURCES_BANNER in body

    def test_banner_names_the_missing_files_in_html(self) -> None:
        assert NO_DOWNLOADABLE_SOURCES_BANNER in render_html(self._notice_only())

    def test_the_caveat_says_the_confidence_was_reduced(self) -> None:
        # The number looks lower than the model's own estimate, and the reader is entitled to
        # know that is deliberate rather than the model's opinion.
        body = render_text(self._notice_only())
        assert "confidence" in body.casefold()

    def test_the_banner_arrives_before_the_analysis(self) -> None:
        # A reader who has reached Section 3 has begun trusting the reasoning; the caveat has
        # to precede it, not follow it.
        body = render_text(self._notice_only())
        assert body.index(NO_DOWNLOADABLE_SOURCES_BANNER) < body.index("1. Background")

    def test_the_notes_footer_repeats_it(self) -> None:
        body = render_text(self._notice_only())
        assert "no downloadable source files" in body.casefold()

    def test_a_verdict_is_still_reported(self) -> None:
        # The whole point: an assessment was made, so its verdict and confidence are shown.
        body = render_text(self._notice_only())
        assert "Final verdict: APPLY" in body
        assert "65%" in body

    def test_no_banner_when_a_document_was_assessed(self) -> None:
        body = render_text(_content())
        assert NO_DOWNLOADABLE_SOURCES_BANNER not in body

    def test_no_banner_for_an_unavailable_assessment(self) -> None:
        # The two states are distinct: one has a verdict on a weak basis, the other has none.
        # Showing both banners at once would contradict itself.
        body = render_text(
            _content(notice_only_assessment=True, assessment_unavailable=True)
        )
        assert NO_DOWNLOADABLE_SOURCES_BANNER not in body
        assert ASSESSMENT_UNAVAILABLE_BANNER in body

    def test_rendering_is_deterministic(self) -> None:
        assert render_text(self._notice_only()) == render_text(self._notice_only())
        assert render_html(self._notice_only()) == render_html(self._notice_only())

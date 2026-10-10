"""Unit tests for the notice-text fallback (:mod:`tender_intelligence.processing.notice`).

These cover the rule that decides whether a tender can be assessed from its published notice
at all. The case that motivated the module is TenderDetail, whose detail pages carry a real
description but expose no downloadable tender document — those tenders were being reported as
``requirement_sources_unavailable`` when the system had enough to say something.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tender_intelligence.processing.notice import (
    NOTICE_DOCUMENT_ID,
    NOTICE_FILENAME,
    NOTICE_METHOD,
    NoticeSource,
    build_notice_text,
    is_notice,
    notice_extraction,
    notice_is_usable,
    notice_sections,
)

FULL = NoticeSource(
    title="Supply of 300 farming kits",
    source_url="https://example.test/tender/1",
    procuring_body="Kano State Ministry of Agriculture",
    reference_numbers=("KN/AGR/2026/14",),
    requirements_hints=(
        "Bidder must have offices in three Nigerian states",
        "Evidence of at least two similar contracts required",
    ),
    scope="Supply and delivery of 300 farming kits to six rural clusters.",
    published_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
    deadline_at=datetime(2026, 10, 20, 15, 0, tzinfo=UTC),
    deadline_timezone="WAT",
    language="en",
    body_text="Prospective suppliers are invited to submit sealed bids.",
)


class TestNoticeSections:
    def test_every_populated_field_becomes_a_labelled_section(self) -> None:
        labels = [label for label, _text in notice_sections(FULL)]
        assert "Title: Supply of 300 farming kits" in labels
        assert "Procuring entity: Kano State Ministry of Agriculture" in labels
        assert "Reference numbers: KN/AGR/2026/14" in labels
        assert "Scope: Supply and delivery of 300 farming kits to six rural clusters." in labels

    def test_requirements_are_joined_not_dropped(self) -> None:
        labels = [label for label, _text in notice_sections(FULL)]
        requirements = next(label for label in labels if label.startswith("Requirements stated"))
        # Both hints must survive: a notice that lists two requirements must not be reduced to
        # one, or the assessment is missing a requirement the source actually published.
        assert "offices in three Nigerian states" in requirements
        assert "two similar contracts" in requirements

    def test_deadline_carries_the_timezone_the_source_stated(self) -> None:
        labels = [label for label, _text in notice_sections(FULL)]
        deadline = next(label for label in labels if label.startswith("Submission deadline"))
        assert "2026-10-20" in deadline
        assert "WAT" in deadline

    def test_empty_fields_produce_no_section(self) -> None:
        sections = notice_sections(NoticeSource(title="Only a title"))
        assert [label for label, _text in sections] == ["Title: Only a title"]

    def test_ordering_is_stable_across_calls(self) -> None:
        # A previously-cited location must still mean the same thing on a later run, so the
        # order is fixed by field rather than by what happened to be populated.
        assert notice_sections(FULL) == notice_sections(FULL)

    def test_whitespace_is_collapsed(self) -> None:
        noisy = NoticeSource(title="  Supply \n\n of   kits  ")
        assert notice_sections(noisy)[0][1] == "Supply of kits"

    def test_naive_deadline_is_read_as_utc(self) -> None:
        naive = NoticeSource(title="T", deadline_at=datetime(2026, 10, 20, 15, 0))
        label = next(label for label, _t in notice_sections(naive) if "deadline" in label.lower())
        assert "2026-10-20 15:00" in label


class TestNoticeUsability:
    def test_a_title_alone_is_not_an_assessment_input(self) -> None:
        # The whole point of this gate: a thin document that satisfies "there is a document"
        # while supporting no requirement at all.
        assert notice_is_usable(notice_sections(NoticeSource(title="Supply of kits"))) is False

    def test_a_scope_is_enough(self) -> None:
        source = NoticeSource(title="Supply of kits", scope="Supply 300 kits.")
        assert notice_is_usable(notice_sections(source)) is True

    def test_body_text_alone_is_enough(self) -> None:
        source = NoticeSource(title="Supply of kits", body_text="Bids are invited.")
        assert notice_is_usable(notice_sections(source)) is True

    def test_nothing_at_all_is_unusable(self) -> None:
        assert notice_is_usable(notice_sections(NoticeSource(title="  "))) is False


class TestNoticeExtraction:
    def test_usable_notice_becomes_an_extracted_document(self) -> None:
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert extraction.tender_id == 42
        assert extraction.extraction_status == "extracted"
        assert extraction.extraction_method == NOTICE_METHOD
        assert extraction.filename == NOTICE_FILENAME

    def test_unusable_notice_produces_nothing(self) -> None:
        # Returning None keeps the tender on its existing path (a raw notice) instead of
        # handing Stage B a document it cannot cite anything from.
        assert notice_extraction(NoticeSource(title="Only a title"), tender_id=42) is None

    def test_sections_are_citable_locations(self) -> None:
        # Stage B validates every quote against the text at the cited location, so the sections
        # must carry real text and one-based indices.
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert [section.index for section in extraction.sections] == list(
            range(1, len(extraction.sections) + 1)
        )
        for section in extraction.sections:
            assert section.text.strip()
            assert section.location == f"section:{section.index}"

    def test_full_text_contains_every_section(self) -> None:
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        for section in extraction.sections:
            assert section.text in extraction.text

    def test_is_marked_as_notice_text(self) -> None:
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert is_notice(extraction) is True

    def test_declared_language_is_kept(self) -> None:
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert extraction.language == "en"

    def test_language_is_detected_when_the_source_gave_none(self) -> None:
        # Long enough to clear the detector's own MIN_TOKENS floor; a short sample is
        # deliberately reported as unknown rather than guessed (processing.languages §16).
        french = NoticeSource(
            title="Fourniture de 300 kits agricoles",
            body_text=(
                "Les soumissionnaires sont invités à remettre leurs offres dans une enveloppe "
                "scellée avant la date limite. Les offres doivent être rédigées en français et "
                "déposées au siège du ministère. Toute offre reçue après la date limite sera "
                "rejetée par le bureau des marchés."
            ),
        )
        extraction = notice_extraction(french, tender_id=42)
        assert extraction is not None
        assert extraction.language == "fr"

    def test_synthetic_document_id_cannot_collide_with_a_real_row(self) -> None:
        # Stage B matches citations on document_id. Real Document rows are serial-numbered
        # from 1, so the reserved 0 is the only value guaranteed not to name a real document.
        assert NOTICE_DOCUMENT_ID == 0

    def test_no_storage_path_is_claimed(self) -> None:
        # Nothing was downloaded, so pointing at storage would be a claim about bytes that do
        # not exist — and would make a later reader look for them.
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert extraction.storage_path is None

    def test_mime_type_states_that_it_is_plain_text(self) -> None:
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert extraction.mime_type == "text/plain"

    def test_content_is_deterministic(self) -> None:
        # Same notice in, same document out, or a re-run would look like a changed tender.
        first = notice_extraction(FULL, tender_id=42)
        second = notice_extraction(FULL, tender_id=42)
        assert first is not None and second is not None
        assert first.text == second.text
        assert first.checksum == second.checksum

    def test_a_different_notice_produces_different_content(self) -> None:
        first = notice_extraction(FULL, tender_id=42)
        second = notice_extraction(
            NoticeSource(title="Repair of air conditioners", scope="Repair 40 units."),
            tender_id=42,
        )
        assert first is not None and second is not None
        assert first.checksum != second.checksum


class TestBuildNoticeText:
    def test_joins_labelled_blocks_with_blank_lines(self) -> None:
        sections = notice_sections(FULL)
        text = build_notice_text(sections)
        assert text.startswith("Title: Supply of 300 farming kits")
        for label, _body in sections:
            assert label in text

    def test_agrees_with_the_extraction_text(self) -> None:
        # Two ways of rendering the same notice must not drift: the file a reader opens and the
        # chunks Stage B cites have to be the same words.
        extraction = notice_extraction(FULL, tender_id=42)
        assert extraction is not None
        assert extraction.text == build_notice_text(notice_sections(FULL))

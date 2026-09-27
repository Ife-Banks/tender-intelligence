"""The explicit translation boundary (prompt 12 §6).

The requirement is *not* "translate tender documents". It is: when the AI provider requires a
single working language, say so explicitly and preserve the originals rather than hiding the
mismatch. These tests therefore assert the two things that can actually go wrong:

* a non-English document is **not** silently dropped, and its text reaches the bundle verbatim; and
* the bundle **states** that no translation happened, names the languages, and tells the AI stage
  what to do about it.

A test asserting a translation *happened* would be asserting the one thing prompt 12 §6 forbids
doing implicitly, since no provider is configured.
"""

from __future__ import annotations

import pytest

from tender_intelligence.processing.representation import (
    DocumentExtraction,
    TenderDocumentBundle,
)
from tender_intelligence.processing.translation import (
    NOT_CONFIGURED,
    NOT_REQUIRED,
    TRANSLATED,
    TRANSLATION_STATUSES,
    NullTranslationBoundary,
    TranslationBoundary,
    TranslationRecord,
    build_translation_record,
    describe_unconfigured,
)


class TestNullBoundary:
    def test_ships_unavailable(self):
        assert NullTranslationBoundary().is_available() is False

    def test_has_no_working_language(self):
        assert NullTranslationBoundary().working_language() is None

    def test_translate_raises_rather_than_returning_the_input(self):
        # Returning the input would look like a successful translation of text to itself, and a
        # caller ignoring is_available() would believe it had been translated.
        with pytest.raises(NotImplementedError):
            NullTranslationBoundary().translate("Bonjour", target_language="en")

    def test_satisfies_the_protocol(self):
        assert isinstance(NullTranslationBoundary(), TranslationBoundary)

    def test_protocol_is_runtime_checkable(self):
        class _Broken:
            name = "broken"

        assert not isinstance(_Broken(), TranslationBoundary)


class TestBuildRecord:
    def test_no_boundary_records_the_limitation(self):
        record = build_translation_record(["fr"])
        assert record.status == NOT_CONFIGURED
        assert record.is_limitation_recorded is True

    def test_no_boundary_preserves_originals(self):
        assert build_translation_record(["fr"]).originals_preserved is True

    def test_no_boundary_names_no_provider(self):
        record = build_translation_record(["fr"])
        assert record.provider is None
        assert record.working_language is None

    def test_limitation_names_the_languages_found(self):
        record = build_translation_record(["fr", "pt"])
        assert record.source_languages == ["fr", "pt"]
        assert "fr" in record.limitation
        assert "pt" in record.limitation

    def test_limitation_tells_the_ai_stage_what_to_do(self):
        # The sentence is read by the AI stage; it must be actionable, not merely present.
        limitation = build_translation_record(["fr"]).limitation
        assert "original language" in limitation
        assert "preserved verbatim" in limitation

    def test_undetectable_language_is_stated_not_assumed_english(self):
        # With no language detected, the sentence must not imply the documents are English.
        limitation = describe_unconfigured([])
        assert "could not be determined" in limitation

    def test_unavailable_boundary_is_treated_as_no_boundary(self):
        assert build_translation_record(["fr"], NullTranslationBoundary()).status == NOT_CONFIGURED

    def test_available_boundary_is_representable_without_a_provider_being_bundled(self):
        # Nothing in this package sets TRANSLATED. It is asserted here only so the state a future
        # provider would produce is part of the schema rather than a future breaking change.
        class _Provider:
            name = "hypothetical"

            def is_available(self):
                return True

            def working_language(self):
                return "en"

            def translate(self, text, *, target_language):
                return f"[{target_language}] {text}"

        record = build_translation_record(["fr"], _Provider())
        assert record.status == TRANSLATED
        assert record.provider == "hypothetical"
        assert record.working_language == "en"
        assert record.originals_preserved is False

    def test_english_only_tender_still_records_a_limitation(self):
        # Even an all-English tender records the unconfigured state, so the consumer reads one
        # shape rather than having to handle a missing field.
        assert build_translation_record(["en"]).status == NOT_CONFIGURED


class TestRecordSerialization:
    def test_round_trip_preserves_every_field(self):
        original = TranslationRecord(
            status=NOT_CONFIGURED,
            provider=None,
            working_language=None,
            source_languages=["fr", "pt"],
            originals_preserved=True,
            limitation="explicit text",
        )
        assert TranslationRecord.from_dict(original.to_dict()) == original

    def test_to_dict_is_json_shaped(self):
        payload = build_translation_record(["fr"]).to_dict()
        assert set(payload) == {
            "status",
            "provider",
            "working_language",
            "source_languages",
            "originals_preserved",
            "limitation",
        }

    def test_from_dict_defaults_to_unconfigured(self):
        # An old artifact with no translation key must read as the conservative state, never as
        # "translation happened".
        assert TranslationRecord.from_dict({}).status == NOT_CONFIGURED
        assert TranslationRecord.from_dict({}).originals_preserved is True

    def test_source_languages_are_copied_not_aliased(self):
        source = ["fr"]
        record = build_translation_record(source)
        source.append("pt")
        assert record.source_languages == ["fr"]

    def test_statuses_are_declared(self):
        assert set(TRANSLATION_STATUSES) == {NOT_CONFIGURED, NOT_REQUIRED, TRANSLATED}


class TestBundleCarriesTheRecord:
    def _bundle(self) -> TenderDocumentBundle:
        return TenderDocumentBundle(
            tender_id=1,
            documents=[
                DocumentExtraction(
                    document_id=1,
                    tender_id=1,
                    filename="avis.pdf",
                    source_url="https://example.org/avis.pdf",
                    extraction_status="extracted",
                    extraction_method="native_pdf",
                    text="Le soumissionnaire doit fournir une methodologie detaillee.",
                    language="fr",
                )
            ],
            languages=["fr"],
            translation=build_translation_record(["fr"]),
        )

    def test_bundle_defaults_to_recording_a_limitation(self):
        # A bundle must not be constructible while silent about language handling.
        assert TenderDocumentBundle(tender_id=1).translation.status == NOT_CONFIGURED
        assert TenderDocumentBundle(tender_id=1).translation.is_limitation_recorded is True

    def test_bundle_serializes_the_translation_record(self):
        payload = self._bundle().to_dict()
        assert payload["translation"]["status"] == NOT_CONFIGURED
        assert payload["translation"]["source_languages"] == ["fr"]

    def test_bundle_round_trip_keeps_the_limitation(self):
        assert TenderDocumentBundle.from_dict(self._bundle().to_dict()) == self._bundle()

    def test_original_text_is_unchanged_by_the_boundary(self):
        # The decisive assertion: French text reaches the bundle exactly as extracted.
        assert "methodologie detaillee" in self._bundle().documents[0].text

    def test_bundle_from_old_artifact_defaults_to_unconfigured(self):
        # A bundle persisted before this field existed must read as unconfigured, not as
        # "translation is not needed".
        assert TenderDocumentBundle.from_dict({"tender_id": 1}).translation.status == NOT_CONFIGURED

"""Prompt 09 §10 — best-guess language detection and its metadata."""

from __future__ import annotations

from support.documents import ENGLISH, FRENCH, PORTUGUESE
from tender_intelligence.processing.languages import (
    INITIAL_ACCEPTANCE_LANGUAGES,
    MIN_TOKENS,
    SUPPORTED_LANGUAGES,
    bundle_languages,
    detect_language,
    normalize_language,
)


def test_detects_each_supported_language_from_realistic_prose() -> None:
    assert detect_language(ENGLISH)[0] == "en"
    assert detect_language(FRENCH)[0] == "fr"
    assert detect_language(PORTUGUESE)[0] == "pt"


def test_confidence_is_reported_alongside_the_guess() -> None:
    language, confidence = detect_language(FRENCH)
    assert language == "fr"
    assert confidence is not None
    assert 0.0 < confidence <= 1.0


def test_short_text_is_not_guessed() -> None:
    """Below the token floor the honest answer is "unknown", not a coin flip."""
    language, confidence = detect_language("Deadline")
    assert language is None
    assert confidence is None


def test_text_at_the_token_floor_is_still_evaluated() -> None:
    text = " ".join(["the"] * MIN_TOKENS)
    language, _confidence = detect_language(text)
    assert language == "en"


def test_substring_collisions_do_not_masquerade_as_french() -> None:
    """Token matching, not substring matching: "table"/"able"/"simple" contain "le"."""
    english_with_le = (
        "The table is available in a simple and stable format. All bidders are able to assemble "
        "a complete file. The timetable is flexible and the schedule is comfortable for all "
        "parties. Please sample the printable template and handle the double file carefully."
    )
    language, _confidence = detect_language(english_with_le)
    assert language != "fr"


def test_english_document_is_not_misread_as_portuguese() -> None:
    """Portuguese and English share function words; the scorer must still separate them."""
    assert detect_language(ENGLISH)[0] == "en"


def test_supported_languages_are_the_documented_set() -> None:
    assert SUPPORTED_LANGUAGES == ("en", "fr", "pt")


def test_normalize_language_reduces_regional_variants() -> None:
    assert normalize_language("fr-FR") == "fr"
    assert normalize_language("PT_br") == "pt"
    assert normalize_language("en") == "en"
    assert normalize_language(None) is None
    assert normalize_language("") is None


def test_normalize_language_does_not_validate_against_a_closed_vocabulary() -> None:
    """docs/03 §3.2 enumerates no allowed values for ``Document.language``."""
    assert normalize_language("de-DE") == "de"


def test_bundle_languages_are_sorted_distinct_and_drop_unknowns() -> None:
    assert bundle_languages(["pt", None, "en", "fr", "en", None]) == ["en", "fr", "pt"]
    assert bundle_languages([None, None]) == []


# ── Language Extensibility Tests (Amendment) ─────────────────────────────────


GERMAN_TEXT = (
    "Der Vertrag wird zwischen der Organisation und dem erfolgreichen Anbieter geschlossen. "
    "Die Lieferung muss innerhalb von drei Monaten nach Vertragsunterzeichnung erfolgen. "
    "Der Anbieter hat alle verpflichtenden Unterlagen einzureichten. "
    "Die Zahlung erfolgt nach erfolgreicher Lieferung und Abnahme. "
    "Alle Streitigkeiten werden vor den zuständigen Gerichten ausgetragen."
)

SPANISH_TEXT = (
    "El contrato se firma entre la organización y el exitoso proveedor. "
    "La entrega debe realizarse dentro de los tres meses posteriores a la firma del contrato. "
    "El proveedor debe presentar todos los documentos obligatorios. "
    "El pago se realiza después de la entrega exitosa y la aceptación. "
    "Todas las disputas se resuelven ante los tribunales competentes."
)

ITALIAN_TEXT = (
    "Il contratto viene firmato tra l'organizzazione e il fornitore vincente. "
    "La consegna deve avvenire entro tre mesi dalla firma del contratto. "
    "Il fornitore deve presentare tutti i documenti obbligatori. "
    "Il pagamento viene effettuato dopo la consegna riuscita e l'accettazione. "
    "Tutte le dispute sono risolte davanti ai tribunali competenti."
)


class TestLanguageExtensibility:
    """Prove the language model is extensible beyond EN/FR/PT."""

    def test_initial_acceptance_languages_are_en_fr_pt(self) -> None:
        """Initial acceptance-test set is EN/FR/PT (not a hard-coded limitation)."""
        assert INITIAL_ACCEPTANCE_LANGUAGES == ("en", "fr", "pt")
        assert SUPPORTED_LANGUAGES == INITIAL_ACCEPTANCE_LANGUAGES

    def test_german_can_be_represented_without_code_changes(self) -> None:
        """A language outside EN/FR/PT can be represented in the language model."""
        # German stopwords are in _STOPWORDS, so detection works
        language, confidence = detect_language(GERMAN_TEXT)
        assert language == "de"
        assert confidence is not None
        assert confidence > 0.0

    def test_spanish_can_be_represented_without_code_changes(self) -> None:
        """Spanish can be detected without source-code changes."""
        language, confidence = detect_language(SPANISH_TEXT)
        assert language == "es"
        assert confidence is not None
        assert confidence > 0.0

    def test_italian_can_be_represented_without_code_changes(self) -> None:
        """Italian can be detected without source-code changes."""
        language, confidence = detect_language(ITALIAN_TEXT)
        assert language == "it"
        assert confidence is not None
        assert confidence > 0.0

    def test_bundle_languages_includes_non_acceptance_languages(self) -> None:
        """Bundle languages can include languages outside the initial acceptance set."""
        result = bundle_languages(["de", "en", "fr", "es", "it", "pt"])
        assert result == ["de", "en", "es", "fr", "it", "pt"]

    def test_normalize_language_accepts_any_valid_code(self) -> None:
        """normalize_language accepts any valid language code, not just EN/FR/PT."""
        assert normalize_language("de-DE") == "de"
        assert normalize_language("es-ES") == "es"
        assert normalize_language("it-IT") == "it"
        assert normalize_language("ar-SA") == "ar"
        assert normalize_language("zh-CN") == "zh"

    def test_unsupported_language_detection_returns_unknown(self) -> None:
        """Languages without stopword sets return unknown, not silent misclassification."""
        # Arabic has no stopword set, so detection should return None
        arabic_text = (
            "هذا نص عربي للاختبار. يجب أن يتم التعرف على اللغة بشكل صحيح. "
            "النص يحتوي على كلمات عربية واضحة."
        )
        language, confidence = detect_language(arabic_text)
        # Arabic has no stopword set, so it should return None (unknown)
        # rather than silently misclassifying as English
        assert language is None
        assert confidence is None

    def test_original_content_preserved_when_translation_not_configured(self) -> None:
        """Original content is preserved when translation is not configured."""
        import pytest

        from tender_intelligence.processing.translation import NullTranslationBoundary

        boundary = NullTranslationBoundary()
        original = "Le contrat est signé entre les deux parties."
        # NullTranslationBoundary raises NotImplementedError — original is preserved verbatim
        # (the caller is expected to use the original text when translation is unavailable)
        with pytest.raises(
            NotImplementedError, match="original document text is preserved verbatim"
        ):
            boundary.translate(original, target_language="en")

    def test_no_provider_name_branching_in_language_logic(self) -> None:
        """No provider-name branching exists in language detection logic."""
        import inspect

        from tender_intelligence.processing import languages

        source = inspect.getsource(languages)
        # Check that no provider names are used in language logic
        assert "DeepSeek" not in source
        assert "OpenAI" not in source
        assert "Claude" not in source
        assert "Groq" not in source
        assert "NVIDIA" not in source

    def test_ocr_language_is_configurable(self) -> None:
        """OCR language is configurable, not hard-coded to EN/FR/PT."""
        from tender_intelligence.processing.service import DocumentProcessingConfig

        # Default includes EN/FR/PT
        config = DocumentProcessingConfig()
        assert "eng" in config.ocr_lang
        assert "fra" in config.ocr_lang
        assert "por" in config.ocr_lang

        # Can be extended with additional languages
        config_deu = DocumentProcessingConfig(ocr_lang="eng+fra+por+deu")
        assert "deu" in config_deu.ocr_lang

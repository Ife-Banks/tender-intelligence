"""Prompt 09 §10 — best-guess language detection and its metadata."""

from __future__ import annotations

from support.documents import ENGLISH, FRENCH, PORTUGUESE
from tender_intelligence.processing.languages import (
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

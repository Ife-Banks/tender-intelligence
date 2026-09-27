"""Business-role classification of extracted tables (prompt 12 §7).

Two things are being asserted here, and the distinction matters:

* that a table whose vocabulary names a business concept is labelled with it in **English, French
  and Portuguese**, so a French-only annex is as classifiable as an English one; and
* that a table whose vocabulary is inconclusive is labelled ``"other"`` rather than guessed at.

The second is the more important half. A classifier that always returns *some* role would satisfy
the first test perfectly and be worse than useless, because a consumer would prioritise a contact
list as though it were the eligibility matrix.

Every call passes the **complete** matrix, matching
:class:`~tender_intelligence.processing.representation.ExtractedTable.rows`: when *headers* is
non-empty the header is ``rows[0]`` and the body follows it. The helper below builds that shape so
the tests cannot accidentally rely on a truncated matrix.
"""

from __future__ import annotations

import pytest

from tender_intelligence.processing.tables import (
    BUSINESS_CRITICAL_ROLES,
    MIN_ROLE_HITS,
    TABLE_ROLES,
    classify_table,
    role_is_business_critical,
)


def _classify(headers: list[str], *body: list[str]) -> tuple[str, str]:
    """Classify a table from its headers and body rows, completing the matrix as extractors do."""
    return classify_table(headers, [headers, *body] if headers else list(body))


class TestEvaluationTables:
    def test_english_evaluation_matrix_is_evaluation(self):
        role, source = _classify(
            ["Criterion", "Weight", "Threshold"],
            ["Technical methodology", "40%", "70"],
        )
        assert role == "evaluation"
        assert source == "vocabulary"

    def test_french_evaluation_matrix_is_evaluation(self):
        # Accented forms: "Critère" must match the unaccented vocabulary.
        role, _ = _classify(
            ["Critère", "Pondération", "Note maximale"],
            ["Expérience du candidat", "30%", "20"],
        )
        assert role == "evaluation"

    def test_portuguese_evaluation_matrix_is_evaluation(self):
        role, _ = _classify(
            ["Critério", "Pontuação", "Percentual"],
            ["Experiência da equipe", "30%", "20"],
        )
        assert role == "evaluation"


class TestDeadlineTables:
    def test_english_schedule_is_deadline(self):
        role, _ = _classify(
            ["Milestone", "Date", "Responsible"],
            ["Proposal submission deadline", "30 March 2026", "Bidder"],
        )
        assert role == "deadline"

    def test_french_schedule_is_deadline(self):
        role, _ = _classify(
            ["Jalon", "Date limite", "Responsable"],
            ["Dépôt des offres", "30 mars 2026", "Soumissionnaire"],
        )
        assert role == "deadline"

    def test_portuguese_schedule_is_deadline(self):
        role, _ = _classify(
            ["Marco", "Prazo", "Responsável"],
            ["Entrega das propostas", "30 de março de 2026", "Licitante"],
        )
        assert role == "deadline"


class TestEligibilityAndExperience:
    def test_eligibility_matrix_is_eligibility(self):
        role, _ = _classify(
            ["Requirement", "Mandatory", "Evidence required"],
            ["Registered in a WHO member state", "Yes", "Certificate of incorporation"],
        )
        assert role == "eligibility"

    def test_experience_requirement_table_is_experience(self):
        role, _ = _classify(
            ["Experience", "Years", "Reference contracts"],
            ["Regional health consulting", "5", "Three reference contracts"],
        )
        assert role == "experience"

    def test_financial_requirement_table_is_financial(self):
        role, _ = _classify(
            ["Financial requirement", "Minimum turnover", "Currency"],
            ["Average annual turnover", "USD 500,000", "USD"],
        )
        assert role == "financial"


class TestInconclusiveTables:
    """The half of the contract that keeps the classifier honest."""

    def test_contact_list_is_not_classified(self):
        role, source = _classify(
            ["Name", "Phone", "Email"],
            ["Procurement Unit", "+41 22 123 4567", "procurement@example.org"],
        )
        assert role == "other"
        assert source == "none"

    def test_empty_table_is_not_classified(self):
        assert classify_table([], []) == ("other", "none")

    def test_single_matching_word_is_insufficient(self):
        # "required" alone is not evidence of an eligibility table; MIN_ROLE_HITS is the floor.
        role, source = _classify(["Required"], ["Yes"])
        assert role == "other"
        assert source == "none"

    def test_min_hits_is_enforced(self):
        # Sanity-check the floor is actually two, not an accident of the vocabulary above.
        assert MIN_ROLE_HITS == 2

    def test_numeric_only_table_is_not_classified(self):
        # A grid of figures carries no vocabulary. Classifying it would be inventing meaning.
        role, _ = classify_table([], [["1", "2", "3"], ["4", "5", "6"]])
        assert role == "other"


class TestDeductions:
    def test_digits_do_not_count_as_vocabulary(self):
        # Percentages and amounts are values, not words: they must not drive a role.
        role, _ = classify_table([], [["40%", "30%", "20%"], ["70", "65", "60"]])
        assert role == "other"

    def test_header_evidence_outweighs_body(self):
        # "criterion"/"weight" are in the header; the body row is plain prose.
        role, _ = _classify(
            ["Criterion", "Weight"],
            ["The bidder shall describe its approach in full narrative detail"],
        )
        assert role == "evaluation"

    def test_repeated_word_counts_once(self):
        # A header repeating one keyword many times is still one piece of evidence.
        role, _ = _classify(["experience", "experience", "experience"], ["x", "y", "z"])
        assert role == "other"

    def test_header_row_is_not_counted_twice(self):
        # rows[0] *is* the header. Reading it again from the matrix would double the evidence and
        # skip the data row, so a two-word header would classify on its own.
        role, _ = classify_table(["Milestone", "Date"], [["Milestone", "Date"]])
        assert role == "other"

    def test_deterministic_for_ambiguous_input(self):
        # Same input, same output — the tie-break must not depend on dict ordering.
        headers = ["Total", "Maximum", "Minimum"]
        first = _classify(headers, ["a", "b", "c"])
        second = _classify(headers, ["a", "b", "c"])
        assert first == second


class TestRoleVocabulary:
    def test_every_declared_role_is_known_to_the_classifier(self):
        for role in TABLE_ROLES:
            assert classify_table([], [])[0] in TABLE_ROLES
            assert isinstance(role_is_business_critical(role), bool)

    def test_other_is_not_business_critical(self):
        assert role_is_business_critical("other") is False

    @pytest.mark.parametrize("role", sorted(BUSINESS_CRITICAL_ROLES))
    def test_prompt_12_roles_are_business_critical(self, role):
        # The five roles prompt 12 §7 names explicitly.
        assert role_is_business_critical(role) is True

    def test_unknown_role_is_not_business_critical(self):
        assert role_is_business_critical("not-a-role") is False

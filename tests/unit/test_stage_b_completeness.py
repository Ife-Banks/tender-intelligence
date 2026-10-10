"""Stage B requirement-completeness unit tests (Prompt 14).

Tests the completeness rules enforced by ``_validate_requirement_sources`` and
``_validate_semantics`` in isolation — not through the full VerdictEngine pipeline.

This directly addresses the Prompt 14 finding:

    "Current validation can compare model-supplied requirements with model-supplied
    assessments without independently establishing that the requirement set is complete."

The two-pass mechanism establishes the requirement set independently (first LLM call),
and these tests verify that the completeness validator correctly enforces it.

Test matrix (§11 of the specification):
    Test 1  — Complete independently established requirement set → verdict may proceed
    Test 2  — One missing assessment → rejected
    Test 3  — Orphan assessment (references nonexistent requirement) → rejected
    Test 4  — Model omits independently established material requirement → rejected
    Test 5  — Missing source document → incomplete_inputs=True propagated
    Test 6  — Evidence-backed requirement → assessment includes that evidence
    Test 7  — No KB evidence → says "no evidence on file" not unsupported absence claim
    Test 8  — Conditions present → verdict is APPLY WITH CONDITIONS
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from tender_intelligence.verdict.service import (
    KBCitation,
    MaterialRequirement,
    RequirementAssessment,
    RequirementExtraction,
    VerdictDocument,
    VerdictPayload,
    _apply_authoritative_overrides,
    _normalize_verdict_json,
    _validate_requirement_sources,
    _validate_semantics,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DOC_ID = 1
_LOCATION = "section:1"
_QUOTE_A = "The tenderer shall provide a technical proposal."
_QUOTE_B = "The tenderer shall supply three references."
_QUOTE_C = "The tenderer shall submit audited financial accounts."

# One source document with three content chunks, one per requirement.
_SOURCE_DOCS = [
    {
        "document_id": _DOC_ID,
        "filename": "tor.docx",
        "extraction_status": "extracted",
        "content": [
            {"location": _LOCATION, "text": _QUOTE_A},
            {"location": _LOCATION, "text": _QUOTE_B},
            {"location": _LOCATION, "text": _QUOTE_C},
        ],
    }
]

_REQ_A = "technical proposal"
_REQ_B = "three references"
_REQ_C = "audited financial accounts"

# The independently extracted requirement set: R1, R2, R3.
_REQUIREMENTS = [
    MaterialRequirement(
        requirement=_REQ_A,
        tender_evidence=[
            VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_A)
        ],
    ),
    MaterialRequirement(
        requirement=_REQ_B,
        tender_evidence=[
            VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_B)
        ],
    ),
    MaterialRequirement(
        requirement=_REQ_C,
        tender_evidence=[
            VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_C)
        ],
    ),
]

# KB with one heading section.
_KB_HEADING = "Company capabilities"
_KB_QUOTE = "OPEX has delivered similar projects in 12 countries."
_KB = f"# {_KB_HEADING}\n{_KB_QUOTE}"

# Deadline context — resolved, not urgent.
_DEADLINE = {
    "status": "RESOLVED",
    "deadline_utc": "2027-06-30T17:00:00+00:00",
    "source_timezone": "UTC",
    "date": "2027-06-30",
    "time": "17:00:00",
    "timezone": "UTC",
}
_DEADLINE_UNRESOLVED = {
    "status": "UNRESOLVED",
    "deadline_utc": None,
    "source_timezone": None,
    "date": None,
    "time": None,
    "timezone": None,
}

_NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=UTC)


def _assessment(req: str, quote: str, *, status: str = "unverified") -> dict:
    """Build a single RequirementAssessment dict for the given requirement."""
    gap = f"No evidence on file for {req}." if status == "unverified" else None
    return {
        "requirement": req,
        "tender_evidence": [
            {"document_id": _DOC_ID, "location": _LOCATION, "quote": quote}
        ],
        "company_evidence": (
            [{"section": _KB_HEADING, "quote": _KB_QUOTE}]
            if status == "met"
            else []
        ),
        "status": status,
        "assessment": (
            f"No evidence on file for {req}."
            if status == "unverified"
            else f"Company meets {req}."
        ),
        "gap": gap,
    }


def _full_payload(
    *,
    requirements: list[str] | None = None,
    assessments: list[dict] | None = None,
    verdict: str = "APPLY WITH CONDITIONS",
    incomplete: bool = False,
    deadline: dict | None = None,
    gaps: list[str] | None = None,
) -> VerdictPayload:
    """Build a valid VerdictPayload covering all three requirements."""
    reqs = requirements if requirements is not None else [_REQ_A, _REQ_B, _REQ_C]
    asmt = assessments if assessments is not None else [
        _assessment(_REQ_A, _QUOTE_A),
        _assessment(_REQ_B, _QUOTE_B),
        _assessment(_REQ_C, _QUOTE_C),
    ]
    dl = deadline if deadline is not None else _DEADLINE
    return VerdictPayload(
        schema_version="verdict.v1",
        background="Synthetic assessment.",
        requirements=reqs,
        deadline_status=dl["status"],
        deadline_utc=dl["deadline_utc"],
        deadline_date=dl["date"],
        deadline_time=dl["time"],
        deadline_timezone=dl["timezone"],
        source_timezone=dl["source_timezone"],
        assessments=[RequirementAssessment(**a) for a in asmt],
        gaps=gaps if gaps is not None else [
            f"No evidence on file for {_REQ_A}.",
            f"No evidence on file for {_REQ_B}.",
            f"No evidence on file for {_REQ_C}.",
        ],
        verdict=verdict,
        confidence=0.4,
        urgency=False,
        incomplete_inputs=incomplete,
        limitations=(
            ["One or more tender inputs were unavailable."] if incomplete else []
        ),
    )


def _call_validate(payload: VerdictPayload, *, incomplete: bool = False) -> None:
    """Call _validate_semantics with the shared test fixtures."""
    _validate_semantics(
        payload,
        _SOURCE_DOCS,
        _KB,
        _DEADLINE,
        incomplete,
        None,   # settings — no urgency window configured
        _NOW,
        _REQUIREMENTS,
    )


# ---------------------------------------------------------------------------
# Test 1 — Complete independently established requirement set → verdict may proceed
# ---------------------------------------------------------------------------

def test_complete_requirement_set_passes_validation() -> None:
    """Every independently established requirement has an assessment → validator raises nothing.

    This is the baseline: R1, R2, R3 extracted; R1, R2, R3 assessed; all good.
    """
    payload = _full_payload()
    _call_validate(payload)   # must not raise


def test_gemma_json_normalization_repairs_only_known_aliases() -> None:
    normalized = _normalize_verdict_json(
        '{"assessments":[{"status":"verified","company_evidence":'
        '[{"location":"Office","quote":"address"}]}]}'
    )
    assessment = normalized["assessments"][0]
    assert assessment["status"] == "met"
    assert assessment["company_evidence"] == [
        {"section": "Office", "quote": "address"}
    ]


def test_authoritative_verdict_requires_all_met_and_no_gaps() -> None:
    assessments = [
        _assessment(req.requirement, req.tender_evidence[0].quote, status="met")
        for req in _REQUIREMENTS
    ]
    payload = _full_payload(assessments=assessments, gaps=[], verdict="DO NOT APPLY")
    repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)
    assert repaired.verdict == "APPLY"


def test_kb_citation_survives_the_heading_markdown_prefix() -> None:
    """A citation naming a real KB heading is kept, however the model wrote it.

    The prompt shows headings as "# Heading" and tells the model to cite the heading it
    sees, so a compliant answer carries the leading "#". The validator compared that
    string against a "#"-stripped set and discarded the citation, which downgraded every
    requirement to "unverified" and left the notification's applicability section with
    no evidence at all. A citation must only be dropped when the heading is not in the KB.
    """
    assessments = [
        _assessment(req.requirement, req.tender_evidence[0].quote, status="met")
        for req in _REQUIREMENTS
    ]
    for cited in (f"# {_KB_HEADING}", _KB_HEADING, f"#{_KB_HEADING}", f"  {_KB_HEADING}  "):
        payload = _full_payload(assessments=assessments, gaps=[], verdict="APPLY")
        for asmt in payload.assessments:
            asmt.company_evidence = [KBCitation(section=cited, quote=_KB_QUOTE)]

        repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)

        assert [c.section for c in repaired.assessments[0].company_evidence] == [
            _KB_HEADING
        ], f"citation {cited!r} was discarded"
        assert repaired.assessments[0].status == "met"


def test_kb_citation_to_an_unknown_heading_is_still_dropped() -> None:
    """The tolerance above must not admit a heading the knowledge base does not contain.

    Accepting any string would let a model satisfy docs/07 §7.3's citation requirement
    by citing a section it invented.
    """
    payload = _full_payload(
        assessments=[_assessment(_REQ_A, _QUOTE_A, status="met")],
        requirements=[_REQ_A],
        gaps=[],
    )
    payload.assessments[0].company_evidence = [
        KBCitation(section="A Section We Never Wrote", quote="Fabricated.")
    ]

    repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)

    assert repaired.assessments[0].company_evidence == []
    assert repaired.assessments[0].status == "unverified"


def test_genuine_unverified_reasoning_is_not_replaced_by_boilerplate() -> None:
    """A model that explains why a requirement is unverified keeps its explanation.

    Only empty text and schema placeholders are replaced. Overwriting every unverified
    assessment with "No evidence on file" discarded the model's actual reasoning, which
    is the only account of what OPEX would need in order to act.
    """
    reasoning = "No banking relationship is recorded; a bid guarantee must be arranged."
    assessment = _assessment(_REQ_A, _QUOTE_A)
    assessment["assessment"] = reasoning
    assessment["gap"] = reasoning
    payload = _full_payload(
        assessments=[assessment], requirements=[_REQ_A], gaps=[reasoning]
    )

    repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)

    assert repaired.assessments[0].assessment == reasoning
    assert repaired.assessments[0].gap == reasoning


def test_schema_placeholder_text_is_replaced_with_the_mandated_wording() -> None:
    """docs/07 §7.2 requires the exact phrase when nothing is on file.

    A model echoing the schema's angle-bracket filler carries no information and must
    never reach a reader, so it becomes the mandated sentence.
    """
    assessment = _assessment(_REQ_A, _QUOTE_A, status="met")
    assessment["company_evidence"] = []
    assessment["assessment"] = "<one sentence explaining why>"
    assessment["gap"] = "<specific gap, or null>"
    payload = _full_payload(
        assessments=[assessment], requirements=[_REQ_A], gaps=["<gap>"]
    )

    repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)

    assert repaired.assessments[0].assessment == "No evidence on file for this requirement."
    assert repaired.assessments[0].gap == "No evidence on file for this requirement."
    assert repaired.gaps == ["No evidence on file for all requirements."]


def test_assessment_is_not_rebound_when_the_model_drops_a_requirement() -> None:
    """Evidence stays with the requirement the model actually described.

    Binding by position alone moved requirement A's evidence onto requirement B whenever
    the model returned fewer assessments than were extracted.
    """
    first = _assessment(_REQ_A, _QUOTE_A, status="met")
    first["company_evidence"] = [KBCitation(section=_KB_HEADING, quote=_KB_QUOTE)]
    payload = _full_payload(assessments=[first], requirements=[_REQ_A, _REQ_B, _REQ_C], gaps=[])

    repaired = _apply_authoritative_overrides(payload, _REQUIREMENTS, _DEADLINE, _KB)

    assert len(repaired.assessments) == 1
    assert repaired.assessments[0].requirement == _REQ_A
    assert repaired.assessments[0].company_evidence == [
        KBCitation(section=_KB_HEADING, quote=_KB_QUOTE)
    ]


# ---------------------------------------------------------------------------
# Test 2 — One missing assessment → rejected
# ---------------------------------------------------------------------------

def test_missing_assessment_is_rejected() -> None:
    """Omitting the assessment for R3 (audited accounts) must fail completeness.

    The model cannot satisfy completeness by simply not mentioning a hard requirement.
    """
    payload = _full_payload(
        assessments=[
            _assessment(_REQ_A, _QUOTE_A),
            _assessment(_REQ_B, _QUOTE_B),
            # R3 (audited accounts) deliberately absent
        ],
        requirements=[_REQ_A, _REQ_B, _REQ_C],   # model still lists it in requirements
    )
    with pytest.raises((ValueError, Exception)):
        _call_validate(payload)


# ---------------------------------------------------------------------------
# Test 3 — Orphan assessment (references nonexistent requirement) → rejected
# ---------------------------------------------------------------------------

def test_orphan_assessment_is_rejected() -> None:
    """An assessment for R99 (not in the established set) must be rejected.

    The model cannot expand the universe of assessments beyond the independent extraction.
    """
    orphan = {
        "requirement": "R99 — invented requirement",
        "tender_evidence": [
            {"document_id": _DOC_ID, "location": _LOCATION, "quote": _QUOTE_A}
        ],
        "company_evidence": [],
        "status": "unverified",
        "assessment": "No evidence on file for R99.",
        "gap": "No evidence on file for R99.",
    }
    payload = _full_payload(
        requirements=[_REQ_A, _REQ_B, _REQ_C],
        assessments=[
            _assessment(_REQ_A, _QUOTE_A),
            _assessment(_REQ_B, _QUOTE_B),
            _assessment(_REQ_C, _QUOTE_C),
            orphan,  # extra assessment for a nonexistent requirement
        ],
    )
    with pytest.raises((ValueError, Exception)):
        _call_validate(payload)


# ---------------------------------------------------------------------------
# Test 4 — Model omits independently established requirement → rejected
# ---------------------------------------------------------------------------

def test_model_omission_of_established_requirement_is_rejected() -> None:
    """The model claims only R1 and R2 exist while the extraction established R1/R2/R3.

    This is the core Prompt 14 finding: the model cannot redefine the requirement universe
    by omitting a requirement from its own ``requirements`` list.
    """
    payload = _full_payload(
        requirements=[_REQ_A, _REQ_B],        # model omits R3
        assessments=[
            _assessment(_REQ_A, _QUOTE_A),
            _assessment(_REQ_B, _QUOTE_B),
        ],
    )
    with pytest.raises((ValueError, Exception)):
        _call_validate(payload)


# ---------------------------------------------------------------------------
# Test 5 — Missing source document → incomplete_inputs=True propagated
# ---------------------------------------------------------------------------

def test_incomplete_inputs_flag_is_propagated_to_payload() -> None:
    """When the document bundle has failed acquisitions, incomplete_inputs must be True.

    The validator checks that the payload's incomplete_inputs field matches the bundle's
    flag — a mismatch is an error.  When incomplete=True, a limitations field is required.
    """
    # A valid payload with incomplete=True must pass when the flag matches.
    payload = _full_payload(incomplete=True)
    _validate_semantics(
        payload,
        _SOURCE_DOCS,
        _KB,
        _DEADLINE,
        True,   # bundle says incomplete
        None,
        _NOW,
        _REQUIREMENTS,
    )   # must not raise

    # A payload claiming complete=False when the bundle says incomplete must be rejected.
    lying_payload = _full_payload(incomplete=False)
    with pytest.raises((ValueError, Exception)):
        _validate_semantics(
            lying_payload,
            _SOURCE_DOCS,
            _KB,
            _DEADLINE,
            True,   # bundle says incomplete
            None,
            _NOW,
            _REQUIREMENTS,
        )


# ---------------------------------------------------------------------------
# Test 6 — Evidence-backed requirement → assessment includes that evidence
# ---------------------------------------------------------------------------

def test_evidence_backed_requirement_assessment_must_include_source_evidence() -> None:
    """The independently established provenance must appear in the assessment.

    _validate_semantics checks that every VerdictDocument in the MaterialRequirement's
    tender_evidence also appears in the corresponding RequirementAssessment's tender_evidence.
    An assessment that strips out the source evidence the extraction established must fail.
    """
    # A valid assessment that includes the required evidence passes.
    payload = _full_payload()
    _call_validate(payload)  # must not raise

    # An assessment that omits the independently established source evidence must fail.
    no_evidence_assessment = {
        "requirement": _REQ_A,
        "tender_evidence": [],    # provenance stripped — the extraction's evidence is gone
        "company_evidence": [],
        "status": "unverified",
        "assessment": f"No evidence on file for {_REQ_A}.",
        "gap": f"No evidence on file for {_REQ_A}.",
    }
    bad_payload = _full_payload(
        assessments=[
            no_evidence_assessment,
            _assessment(_REQ_B, _QUOTE_B),
            _assessment(_REQ_C, _QUOTE_C),
        ]
    )
    with pytest.raises((ValueError, Exception)):
        _call_validate(bad_payload)


# ---------------------------------------------------------------------------
# Test 7 — No KB evidence → says "no evidence on file" not unsupported absence claim
# ---------------------------------------------------------------------------

def test_no_kb_evidence_says_no_evidence_on_file_not_absence_claim() -> None:
    """When no KB evidence supports a requirement, the assessment must say 'no evidence on file'.

    This prevents the model from making unsupported negative capability claims
    (e.g. "OPEX does not have audited accounts") instead of the correct epistemic statement.
    Specifically: status='unverified', no company_evidence, assessment text must contain
    the phrase 'no evidence on file' (case-insensitive), and gap must state the same.
    """
    # All three unverified with "no evidence on file" → passes.
    payload = _full_payload()
    _call_validate(payload)  # must not raise

    # Unverified with empty company_evidence but gap/assessment do NOT say "no evidence on file"
    # → must fail.
    bad_assessment = {
        "requirement": _REQ_A,
        "tender_evidence": [
            {"document_id": _DOC_ID, "location": _LOCATION, "quote": _QUOTE_A}
        ],
        "company_evidence": [],
        "status": "unverified",
        "assessment": "OPEX lacks the required technical proposal capability.",  # absence claim
        "gap": "Company does not have this.",   # absence claim — not "no evidence on file"
    }
    bad_payload = _full_payload(
        assessments=[
            bad_assessment,
            _assessment(_REQ_B, _QUOTE_B),
            _assessment(_REQ_C, _QUOTE_C),
        ]
    )
    with pytest.raises((ValueError, Exception)):
        _call_validate(bad_payload)


# ---------------------------------------------------------------------------
# Test 8 — Conditions present → verdict is APPLY WITH CONDITIONS
# ---------------------------------------------------------------------------

def test_unverified_requirements_produce_apply_with_conditions_verdict() -> None:
    """When any assessment is unverified, the verdict must be APPLY WITH CONDITIONS.

    The validator derives the expected verdict from the assessments:
    - all met + no gaps → APPLY
    - any not_met → DO NOT APPLY
    - anything else (unverified or partially_met) → APPLY WITH CONDITIONS

    An incorrect verdict label must be rejected even if all other fields are valid.
    """
    # Correct: unverified requirements → APPLY WITH CONDITIONS passes.
    payload = _full_payload(verdict="APPLY WITH CONDITIONS")
    _call_validate(payload)  # must not raise

    # Incorrect: same assessments but verdict claims APPLY → must fail.
    wrong_verdict = _full_payload(verdict="APPLY")
    with pytest.raises((ValueError, Exception)):
        _call_validate(wrong_verdict)

    # Incorrect: same assessments but verdict claims DO NOT APPLY → must fail.
    wrong_verdict2 = _full_payload(verdict="DO NOT APPLY")
    with pytest.raises((ValueError, Exception)):
        _call_validate(wrong_verdict2)


# ---------------------------------------------------------------------------
# Bonus: validate_requirement_sources unit tests
# ---------------------------------------------------------------------------

def test_requirement_sources_accepts_valid_extraction() -> None:
    """A well-formed extraction with real source quotes passes."""
    extraction = RequirementExtraction(requirements=_REQUIREMENTS)
    _validate_requirement_sources(extraction, _SOURCE_DOCS)  # must not raise


def test_requirement_sources_rejects_invented_location() -> None:
    """A requirement citing a location that does not exist in any source document is rejected."""
    bad = [
        MaterialRequirement(
            requirement="some requirement",
            tender_evidence=[
                VerdictDocument(
                    document_id=_DOC_ID,
                    location="page:999",      # invented — not in _SOURCE_DOCS
                    quote=_QUOTE_A,
                )
            ],
        )
    ]
    with pytest.raises(ValueError, match="has invalid citation"):
        _validate_requirement_sources(RequirementExtraction(requirements=bad), _SOURCE_DOCS)


def test_requirement_sources_rejects_empty_requirement_text() -> None:
    """A requirement with an empty (whitespace-only) text is rejected."""
    bad = [
        MaterialRequirement(
            requirement="   ",
            tender_evidence=[
                VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_A)
            ],
        )
    ]
    with pytest.raises(ValueError, match="empty or duplicate"):
        _validate_requirement_sources(RequirementExtraction(requirements=bad), _SOURCE_DOCS)


def test_requirement_sources_rejects_duplicate_requirements() -> None:
    """Two requirements with the same normalised text are rejected."""
    dup = [
        MaterialRequirement(
            requirement=_REQ_A,
            tender_evidence=[
                VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_A)
            ],
        ),
        MaterialRequirement(
            requirement=_REQ_A.upper(),   # same when case-folded
            tender_evidence=[
                VerdictDocument(document_id=_DOC_ID, location=_LOCATION, quote=_QUOTE_A)
            ],
        ),
    ]
    with pytest.raises(ValueError, match="empty or duplicate"):
        _validate_requirement_sources(RequirementExtraction(requirements=dup), _SOURCE_DOCS)

# Stage B Requirement Completeness Report

**Date:** 2026-09-24  
**Scope:** Independent requirement-completeness mechanism for Stage B (Prompt 14)

---

## 1. Finding Addressed

> Current validation can compare model-supplied requirements with model-supplied assessments
> without independently establishing that the requirement set is complete.

The concern is that a model could satisfy validation by producing a self-consistent but
incomplete response — omitting a hard requirement and simultaneously omitting its
assessment, leaving no discrepancy for the validator to detect.

This report documents how the pipeline prevents that attack vector.

---

## 2. Mechanism: Two-Pass Independent Extraction

The VerdictEngine runs two separate LLM calls before persisting any verdict:

```
Pass 1 — Requirement Extraction
    Input:  tender document bundle (source text only, no KB, no company data)
    Output: RequirementExtraction { requirements: [MaterialRequirement, ...] }
    Model:  MaterialRequirement { requirement: str, tender_evidence: [VerdictDocument, ...] }

Pass 2 — Assessment
    Input:  tender documents + company KB + material_requirements from Pass 1
    Output: VerdictPayload { assessments: [RequirementAssessment, ...], ... }
```

The Pass 1 output is the **independently established requirement set**. Pass 2 cannot
redefine it — the validator enforces strict equality between the established set and
the assessed set.

### Data models

```python
class MaterialRequirement(BaseModel):
    requirement: StrictStr                    # requirement text
    tender_evidence: list[VerdictDocument]    # source-grounded evidence (min 1)

class RequirementExtraction(BaseModel):
    requirements: list[MaterialRequirement]   # min 1

class RequirementAssessment(BaseModel):
    requirement: StrictStr
    tender_evidence: list[VerdictDocument]
    company_evidence: list[KBCitation]
    status: StrictStr   # met | not_met | partially_met | unverified
    assessment: StrictStr
    gap: StrictStr | None
```

---

## 3. Completeness Rules Enforced

All rules are enforced in `_validate_semantics` (called after both passes) and
`_validate_requirement_sources` (called after Pass 1).

### Rule 1 — Requirements list must exactly match independent extraction

```python
# service.py _validate_semantics
established = {item.requirement.strip().casefold(): item for item in material_requirements}
listed = [item.strip().casefold() for item in payload.requirements]
if len(set(listed)) != len(listed) or set(listed) != set(established):
    raise ValueError("model requirements do not match independent extraction")
```

### Rule 2 — Assessments must exactly cover independent extraction

```python
assessed = [item.requirement.strip().casefold() for item in payload.assessments]
if len(set(assessed)) != len(assessed) or set(assessed) != set(established):
    raise ValueError("assessments do not exactly cover independent requirements")
```

### Rule 3 — Each assessment must preserve the independently established source evidence

```python
for key, requirement in established.items():
    assessment_refs = {(ref.document_id, ref.location, ref.quote)
                       for ref in assessments_by_requirement[key].tender_evidence}
    required_refs = {(ref.document_id, ref.location, ref.quote)
                     for ref in requirement.tender_evidence}
    if not required_refs.issubset(assessment_refs):
        raise ValueError("assessment omitted independently established source evidence")
```

### Rule 4 — Orphan assessments rejected

Rule 2 above (`set(assessed) != set(established)`) catches any assessment for a
requirement that does not exist in the independent extraction. An assessment of
`R99` when the extraction established only `R1/R2/R3` fails the set equality check.

### Rule 5 — Incomplete inputs flag must match the bundle

```python
if payload.incomplete_inputs is not incomplete:
    raise ValueError("incomplete input state mismatch")
```

### Rule 6 — Unsupported capability must say "no evidence on file"

```python
if (assessment.status == "unverified" and not assessment.company_evidence and (
    "no evidence on file" not in assessment.gap.casefold()
    or "no evidence on file" not in assessment.assessment.casefold()
)):
    raise ValueError("unsupported capability assessment must say no evidence on file")
```

### Rule 7 — Verdict must follow from assessments

```python
expected_verdict = (
    "DO NOT APPLY" if any(item.status == "not_met" for item in payload.assessments)
    else "APPLY" if all(item.status == "met" for item in payload.assessments) and not payload.gaps
    else "APPLY WITH CONDITIONS"
)
if payload.verdict != expected_verdict:
    raise ValueError("verdict does not follow requirement assessments and gaps")
```

### Rule 8 — Source evidence validated against real document content

```python
# _validate_requirement_sources
valid_refs = {
    (document["document_id"], chunk["location"], chunk["text"])
    for document in source_docs
    for chunk in document.get("content", [])
}
for requirement in extraction.requirements:
    for evidence in requirement.tender_evidence:
        if not any(
            document_id == evidence.document_id
            and location == evidence.location
            and evidence.quote in text
            for document_id, location, text in valid_refs
        ):
            raise ValueError("requirement source evidence is invalid")
```

---

## 4. Validation Retry Policy

The validator is called after both Pass 1 and Pass 2. Each pass has exactly one
retry. On the second failure the engine records `verdict_invalid_output` and
emits a `critical` alert — no verdict is persisted.

```
Pass 1:  extract → validate_sources → [retry once if invalid] → fail-safe
Pass 2:  assess  → validate_semantics → [retry once if invalid] → fail-safe
```

This preserves the existing retry semantics documented in Prompt 14 §12.

---

## 5. Requirement Provenance

Every `MaterialRequirement` carries its source evidence:

```json
{
  "requirement": "The tenderer shall submit audited financial accounts",
  "tender_evidence": [
    {
      "document_id": 42,
      "location": "page:7",
      "quote": "The tenderer shall submit audited financial accounts."
    }
  ]
}
```

`document_id` and `location` are real values from the document bundle; `quote` must
be a substring of the actual text at that location. Page numbers are never fabricated —
they come from the `ExtractedPage.location` or `ExtractedSection.location` fields in
the stored bundle.

---

## 6. Existing Controls Preserved

| Control | Preserved |
|---|---|
| Stage A PASS gate | ✅ Triage check unchanged |
| Provider approval (`approved_for_company_docs`) | ✅ Unchanged |
| Monthly AI budget gate | ✅ Unchanged |
| Validation retry (exactly one) | ✅ Unchanged |
| Transport retry with fallback | ✅ Unchanged |
| Test Mode / idempotency | ✅ Unchanged |
| Provenance (document_id / location / quote) | ✅ Unchanged |
| Verdict enum (APPLY / DO NOT APPLY / APPLY WITH CONDITIONS) | ✅ Unchanged |
| Deadline fields (date / time / timezone / UTC) | ✅ Unchanged |
| `incomplete_inputs` semantics | ✅ Unchanged |
| Confidence field | ✅ Unchanged |

No new verdict category was introduced. No existing validation rule was weakened.

---

## 7. Test Matrix Results

**File:** `tests/unit/test_stage_b_completeness.py`  
**12 tests, all pass**

| # | Test | Rule Exercised | Result |
|---|---|---|---|
| T1 | `test_complete_requirement_set_passes_validation` | All requirements assessed → passes | ✅ PASS |
| T2 | `test_missing_assessment_is_rejected` | R3 omitted from assessments → rejected | ✅ PASS |
| T3 | `test_orphan_assessment_is_rejected` | R99 in assessments, not in established set → rejected | ✅ PASS |
| T4 | `test_model_omission_of_established_requirement_is_rejected` | Model's `requirements` list omits R3 → rejected | ✅ PASS |
| T5 | `test_incomplete_inputs_flag_is_propagated_to_payload` | `incomplete=True` passes; mismatch rejected | ✅ PASS |
| T6 | `test_evidence_backed_requirement_assessment_must_include_source_evidence` | Provenance stripped from assessment → rejected | ✅ PASS |
| T7 | `test_no_kb_evidence_says_no_evidence_on_file_not_absence_claim` | Unsupported absence claim → rejected | ✅ PASS |
| T8 | `test_unverified_requirements_produce_apply_with_conditions_verdict` | Unverified → APPLY WITH CONDITIONS; wrong label → rejected | ✅ PASS |
| B1 | `test_requirement_sources_accepts_valid_extraction` | Valid extraction passes source check | ✅ PASS |
| B2 | `test_requirement_sources_rejects_invented_location` | Invented location rejected | ✅ PASS |
| B3 | `test_requirement_sources_rejects_empty_requirement_text` | Empty requirement text rejected | ✅ PASS |
| B4 | `test_requirement_sources_rejects_duplicate_requirements` | Case-folded duplicate rejected | ✅ PASS |

**Integration tests also green:**

| Test | Result |
|---|---|
| `test_independently_extracted_requirements_all_assessed` | ✅ PASS |
| `test_incomplete_or_orphaned_requirement_assessments_are_rejected[missing_assessment]` | ✅ PASS |
| `test_incomplete_or_orphaned_requirement_assessments_are_rejected[orphan_assessment]` | ✅ PASS |
| `test_incomplete_or_orphaned_requirement_assessments_are_rejected[model_omission]` | ✅ PASS |
| `test_incomplete_bundle_flag_is_forwarded_and_persisted` | ✅ PASS |

---

## 8. Files

| Role | File | Change |
|---|---|---|
| Completeness validator | `src/tender_intelligence/verdict/service.py` | **Pre-existing** — no change required |
| Pydantic models | `src/tender_intelligence/verdict/service.py` | **Pre-existing** — no change required |
| Unit test matrix | `tests/unit/test_stage_b_completeness.py` | **New** — created this session |

---

## 9. Gate

```
STAGE B REQUIREMENT COMPLETENESS: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION
```

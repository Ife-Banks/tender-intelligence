# Gemma 4 Verdict Validation Fixes

## Problem Summary

Gemma 4 (31B cloud model) was generating verdict outputs that failed validation in three ways:

### 1. Invalid Status Values
- **Model output**: `"status": "verified"`
- **Expected**: One of `met`, `not_met`, `partially_met`, `unverified`
- **Root cause**: Model used natural language status instead of schema-defined enum

### 2. Wrong company_evidence Shape
- **Model output**: `{"location": "Office", "quote": "..."}`
- **Expected**: `{"section": "...", "quote": "..."}`
- **Root cause**: Model mirrored tender_evidence structure (which uses `location`) instead of KB citation structure (which requires `section`)

### 3. Invalid KB Citations
- **Model output**: Cited non-existent KB sections like "Office" (an address line, not a heading)
- **Expected**: Only cite actual KB section headings (lines starting with `#`)
- **Root cause**: Model fabricated plausible-sounding KB sections without checking they exist

## Solution

### 1. Enhanced Verdict Prompt (lines 2010-2110)

**Added unambiguous schema rules:**
```
CRITICAL SCHEMA RULES:
1. verdict: EXACTLY one of "APPLY" | "DO NOT APPLY" | "APPLY WITH CONDITIONS"
2. status per assessment: EXACTLY one of "met" | "not_met" | "partially_met" | "unverified"
   - NEVER use "verified", "failed", "partial", or other values
3. company_evidence items: {"section": "<exact KB heading>", "quote": "<verbatim text>"}
   - Use ONLY "section" and "quote" keys (NOT "location")
   - section MUST be an exact heading from the KB (starts with #)
   - Use empty list [] if no KB evidence exists
```

**Added concrete examples:**
- One example with `status: "met"` and proper KB citation
- One example with `status: "unverified"` and empty `company_evidence: []`

### 2. Robust Normalization in _apply_authoritative_overrides (lines 1970-2070)

**Status normalization:**
```python
status_map = {
    "verified": "met",
    "partial": "partially_met",
    "partially_verified": "partially_met",
    "failed": "not_met",
}
status = status_map.get(status, status)
```

**company_evidence field repair:**
```python
# Rename location→section
if "location" in citation_dict and "section" not in citation_dict:
    citation_dict["section"] = citation_dict.pop("location")
```

**KB citation validation:**
```python
# Get valid KB headings
kb_headings = {
    line.lstrip("# ").strip() 
    for line in kb_text.splitlines() 
    if line.startswith("#")
}

# Drop citation if section isn't a real KB heading
if kb_headings and section not in kb_headings:
    continue  # Skip invalid citation
```

**Automatic unverified status when evidence is dropped:**
```python
# If we dropped all company evidence and status was met, set to unverified
if not fixed_company_evidence and status == "met":
    status = "unverified"
    gap = "No evidence on file for this requirement."
    assessment_text = "No evidence on file for this requirement."
```

### 3. Verdict Derivation (lines 2050-2060)

Compute verdict from assessment statuses instead of trusting model output:

```python
statuses = [a.status for a in fixed_assessments]
if any(s == "not_met" for s in statuses):
    derived_verdict = "DO NOT APPLY"
elif all(s == "met" for s in statuses):
    derived_verdict = "APPLY"
else:
    derived_verdict = "APPLY WITH CONDITIONS"
```

This ensures verdict matches the documented derivation rule:
- Any `not_met` → `DO NOT APPLY`
- All `met` → `APPLY`
- Otherwise → `APPLY WITH CONDITIONS`

### 4. Detailed Validation Error Retry (lines 700-730)

**Capture validation errors:**
```python
except (ValidationError, ValueError, TypeError) as first_error:
    error_details = ""
    if isinstance(first_error, ValidationError):
        errors = first_error.errors()
        error_lines = []
        for err in errors[:5]:  # Limit to first 5 errors
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", "validation failed")
            error_lines.append(f"  - {loc}: {msg}")
        error_details = "\n".join(error_lines)
```

**Send specific corrections in retry:**
```python
content=(
    "The output has validation errors:\n"
    f"{error_details}\n\n"
    "CRITICAL CORRECTIONS REQUIRED:\n"
    "1. status field: MUST be exactly \"met\", \"not_met\", \"partially_met\", or \"unverified\"\n"
    "   - NEVER use \"verified\", \"failed\", \"partial\"\n"
    "2. company_evidence: MUST use {\"section\": \"...\", \"quote\": \"...\"}\n"
    "   - Use \"section\" (NOT \"location\")\n"
    "   - section must be an exact KB heading that exists in the provided evidence\n"
    "3. If status=\"unverified\", company_evidence MUST be []\n"
    "4. If status=\"met\", company_evidence MUST NOT be empty\n"
)
```

### 5. Removed Debug Printing

Removed all `🔍 DEBUG` output that logged:
- Full request/response content (lines 96-115 of openai_compatible.py)
- KB content in response preview (lines 161-195 of openai_compatible.py)

This violated the data policy rule against logging KB content and prompt details.

## Expected Outcomes

After these fixes:

1. **Gemma 4 status values normalized**: `verified` → `met` automatically
2. **company_evidence shape corrected**: `location` → `section` automatically
3. **Invalid KB citations dropped**: Non-existent sections removed, status set to `unverified`
4. **Verdict derivation reliable**: Computed from assessments, not model output
5. **Better retry success rate**: Detailed error messages guide model to correct output
6. **No KB content logged**: Debug printing removed per data policy

## Testing

Restart the server to load new code:
```bash
uv run python -m tender_intelligence.admin
```

Re-test the same WAHO tender that previously failed. Expected debug output:
```
✅ json.loads: PASSED
✅ Pydantic VerdictPayload: PASSED
✅ _apply_authoritative_overrides: normalized status, fixed citations
✅ Source validation: PASSED
✅ Stage B final verdict validated
```

## Separate Issue: OCR Failures

The logs showed 15 of 16 documents failed OCR extraction (`ocr_failed`). This means:
- Stage B had only 1 document to work with
- Verdict reports `incomplete_inputs: true`
- This is expected without Tesseract installed

**To fix OCR:**
```bash
# Check if Tesseract is installed
tesseract --version

# If not, install with language packs
# Windows: Download installer from https://github.com/UB-Mannheim/tesseract/wiki
# Install with eng, fra, por language packs
```

The default OCR config is `eng+fra+por` (English, French, Portuguese) which matches WAHO's multilingual tenders.

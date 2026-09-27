# Quote Validation Fix

## Problem
The `DocumentMapEvidence.quote` field had a `max_length=500` character limit, but procurement document eligibility/requirement clauses routinely run longer. The model was producing longer verbatim quotes (600-700+ chars), causing validation failures even after the bounded correction retry explicitly told it which fields were too long.

## Root Cause
Schema limit mismatch: 500 characters is too restrictive for realistic procurement document text. The model wasn't reliably trimming quotes even when instructed because the limit itself was the problem, not the model's adherence.

## Solution (Three-Part Fix)

### 1. Raised Schema Limit (Line 98)
```python
# Before
quote: StrictStr = Field(min_length=1, max_length=500)

# After  
quote: StrictStr = Field(min_length=1, max_length=1000)
```

**Rationale:** 1000 chars provides realistic headroom for verbose procurement requirements while still preventing abuse. Tender eligibility clauses often contain multiple conditions, cross-references, and legal language that exceed 500 chars.

### 2. Kept Correction-Retry Feedback As-Is
The existing correction retry (lines 1058-1071) already tells the model the exact validation error using `_safe_validation_shape(errors)`. The retry not fixing the issue twice in a row confirmed the limit itself was the mismatch, not the prompt wording.

### 3. Added Safety Net: Smart Quote Truncation (Lines 1518-1545)

New helper function `_truncate_overlong_quotes()`:
```python
def _truncate_overlong_quotes(
    mapped: DocumentMapResult, segment_text: str, max_quote_length: int = 1000
) -> DocumentMapResult:
    """
    Safety net: if a quote is a valid substring but exceeds max_length, truncate it
    to max_length. A prefix of a verbatim quote is still verbatim/findable.
    
    This avoids depending on model compliance for something we can fix deterministically.
    """
```

**Logic:**
- If a quote is a valid substring of the segment text but exceeds `max_length`
- Truncate it to exactly `max_length` (a prefix of a verbatim quote is still verbatim and findable)
- This deterministically prevents validation failures without depending on model compliance
- Applied in both validation paths (initial validation line 1050, retry validation line 1087)

## Impact

### What This Fixes
✅ Models can now produce realistic-length procurement requirement quotes  
✅ Validation failures from overlong but valid quotes are eliminated via safety net  
✅ The bounded correction retry remains effective for actual schema/structure issues

### What Remains Unchanged
✅ `reasoning_effort=low` configuration (already proven working)  
✅ `max_output_tokens` and payload-reduction logic (already proven working)  
✅ All other validation rules (location matching, quote presence in segment)  
✅ Correction retry mechanism and feedback structure

## Testing Recommendations

1. **Verify the 1000-char limit** handles your longest realistic requirement clauses
2. **Monitor correction retry effectiveness** - it should still catch genuine schema issues
3. **Check truncation behavior** on edge cases where a quote is exactly at or just over 1000 chars
4. **Confirm the safety net doesn't mask** model instruction-following problems (it only truncates valid quotes, not invalid ones)

## Related Files
- `src/tender_intelligence/verdict/service.py` (lines 98, 1050, 1087, 1518-1545)
- Original issue: model producing 600-700+ char quotes, validation failing even after retry

## Notes
This is a **schema-limit fix**, not a prompt/config fix. The previous `reasoning_effort` and payload changes solved the output quality/length issues. This change addresses the mismatch between realistic procurement text lengths and the schema's constraints.

# Prompt 16E — Stage B Document Map Null / Invalid Reference

## Result

**PROMPT 16E: NEEDS LIVE VERIFICATION**

## Root cause

The code emits `document_map_invalid_reference` only after JSON parsing and `DocumentMapResult.model_validate(data)` have succeeded, in one of two source-reference checks: the returned location differs from the currently supplied segment location, or the normalized returned quote cannot be found in that segment's text. A null or schema-invalid value cannot reach those checks: JSON `null` is passed to Pydantic and fails as a model-type validation error; schema failures return before the evidence loop.

The reported `parsed_response_shape: {"kind":"null"}` did **not** establish that the provider returned JSON null and did not mean the parsed map became null. Both citation-failure calls omitted `parsed_data` when invoking `_document_map_error`; its default is Python `None`, which the safe shape helper reports as JSON-like `null`. The same call site hard-coded `schema_validation_succeeded` to false. Thus the log was incorrect for a citation failure. Given the control flow and the provided `document_map_invalid_reference` error, the map had passed local schema validation; the actual provider completion was not retained, so the exact failing reference branch (location mismatch vs quote not found) cannot be recovered from this run.

## Changes

- `src/tender_intelligence/verdict/service.py`: route all parsed JSON types, including `null`, through Pydantic before reference validation; classify non-object model values as invalid type; pass the parsed result to reference diagnostics; mark schema validation successful only on citation failures; log a redacted failure category, reference index/location, document ID, supplied document/location identifiers, and text lengths. Quote contents are never logged.
- `tests/integration/test_verdict_engine.py`: cover JSON null and schema-invalid object rejection before citation checks, valid map schema, valid-shape citation failure diagnostics/redaction, and rejection of a reference to original evidence omitted from the reduced map input. Existing payload-reduction success tests continue to exercise persistence of a valid verdict.

Reference validation remains exact to the segment supplied to each map request; an original bundle location not present in the reduced input is rejected. No provider/model conditionals, `/v1/models` behavior, Stage A behavior, or reduction algorithm changes were introduced.

## Verification

- Stage B, document bundle, generic provider client, selected orchestrator persistence, and Prompt 16C reduction tests: passed.
- Ruff for changed source and test files: passed.
- Pytest reported a cache-directory warning (`WinError 183`); selected tests passed.

## Live Tender 163

Not repeated. The local Admin health request at `http://127.0.0.1:8000/health` timed out, so I could not verify current Test Mode or issue Process Now safely. No database was cleared, no live operation was launched, and no notification was sent. The historical live run had no final persisted verdict, and there is no new evidence of Stage 15 notification behavior.

The instrumentation now distinguishes null/object/array/string/number/boolean shapes and identifies which citation check failed on the next run without exposing quote text. Prompt 16E is not accepted until the live run reaches a persisted verdict and completes Stage 15 in Test Mode.

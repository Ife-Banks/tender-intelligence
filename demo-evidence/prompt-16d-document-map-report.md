# Prompt 16D — Stage B Document Map Schema Failure

## Result

**STAGE B DOCUMENT MAP: NEEDS LIVE VERIFICATION**

The map prompt and validator now share the canonical `DocumentMapResult` Pydantic JSON Schema. Schema-invalid responses receive one bounded correction attempt using only safe field/type diagnostics. Failures record structural shape and validation paths without recording response values or tender text.

## Diagnosis and limits

The expected intermediate response is a strict JSON object with `summary` (non-empty string, at most 1,200 characters) and `evidence` (array of at most four objects, each containing a non-empty `location` of at most 200 characters and a verbatim `quote` of at most 500 characters). Extra fields are forbidden. Each location and quote is then checked against the exact supplied source segment.

The supplied live evidence reports HTTP 200, successful JSON parsing, `finish_reason=stop`, and failed schema validation. The raw parsed response was not retained in the supplied logs, database, or repository fixtures. Therefore the exact historical response shape and exact root cause cannot be established without guessing. A synthetic nested response is covered by regression tests, but is explicitly not represented as the live response.

The earlier generic `document_map_unexpected_schema` obscured useful Pydantic errors, especially nested output and constraint failures. New diagnostics classify nested expected fields as `document_map_unexpected_nesting`, and report missing/type/constraint/extra-field failures distinctly. Safe structural logs include key names, JSON kinds, sizes, and schema paths, but never rejected values.

## Changes

- `src/tender_intelligence/verdict/service.py`: generate the map instructions from `DocumentMapResult.model_json_schema()`, add one schema-correction attempt, and emit redacted structural validation diagnostics.
- `tests/integration/test_verdict_engine.py`: cover canonical-schema prompt agreement, valid map output, nested schema rejection with precise diagnostic, redaction, bounds, and existing reduction/citation behavior.

No provider/model conditionals or provider-specific response formats were added. The final verdict contract, Stage A, notifications, and provider selection were not changed.

## Verification

- Stage B, document bundle, generic OpenAI-compatible client, and selected orchestrator persistence/reduction tests: passed.
- Focused canonical-schema and nested-diagnostic tests: passed.
- Ruff on changed source and test files: passed.
- Pytest emitted a cache-directory warning (`WinError 183`) while using an isolated basetemp; test outcomes were successful.

## Live verification

Not run. `http://127.0.0.1:8000/health` did not return within the 3-second local check. Test Mode and the Tender 163/internal 142 configuration could not be verified from the live Admin API. No Process Now request was issued, no database was cleared, and no email was sent.

Accordingly, there is no evidence yet that the reduced request validates against the live provider, the final verdict is persisted, or Stage 15 completes safely. Prompt 16D acceptance criteria are not all satisfied until a live run records the actual sanitized response shape and persisted verdict under Test Mode.

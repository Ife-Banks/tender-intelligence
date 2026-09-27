# Prompt 16F — Stage B Malformed JSON After Payload Reduction

## Result

**STAGE B LIVE VERIFICATION: NOT PASSED — LIVE RUN REQUIRED**

The source now records redacted response/request diagnostics, makes the JSON-only instruction explicit, and permits exactly one correction request when a document-map response is malformed JSON. Payload reduction, schema requirements, citation checks, and stage-status policy remain unchanged.

## A. Root cause and response path

The supplied 40-byte count is computed from `LLMResponse.content`. The worker/Admin OpenAI-compatible client selects the first choice's `message.content`, verifies it is a non-empty string, and returns that exact string as `LLMResponse.content`; it does not substitute `reasoning` or `reasoning_content`. Thus the 40 bytes are the content string the generic client handed Stage B. No raw or sanitized structural capture of that historical string was retained, so it is not possible to determine whether it was whitespace, plain text, fenced JSON, truncated JSON, or something else.

The evidence does **not** establish that reasoning consumed the budget or that the model generated malformed JSON for a particular reason. The map request currently caps `max_tokens` at 1,200. When thinking is enabled, the effective reasoning budget is bounded by that same call allowance. The profile values for the failing run could not be retrieved because the local Admin server is unavailable. No token-budget or provider setting was changed based on speculation.

The path remains: HTTP response → generic client `choices[0].message.content` → response diagnostics → JSON parse → strict `DocumentMapResult` validation → exact supplied-segment location/quote checks. Citation/reference validation cannot run before successful schema validation. Malformed JSON receives at most one correction attempt; the correction is not retried if it is malformed or schema-invalid.

## B. Safe diagnostics added

- Character and byte counts; redacted structural boundary characters; empty/whitespace, JSON object/array, fenced, incomplete, and plain-text classifications.
- Sanitized JSON parse error category and position, without provider error snippets or response text.
- Safe request configuration: effective `max_tokens`, temperature/top-p, reasoning budget/effort settings, response-format capability/request, stop-parameter presence, and reported token usage.
- Safe request summary: prompt length/token estimate, schema-instruction presence, reduced document/segment counts, text-size estimate, document identifiers, and supplied-location count.
- Generic response extraction metadata: choice count/index, whether message `content` existed, content type, and presence flags for reasoning/refusal/tool-call fields. No response values are stored.

The prompt now explicitly requires only the schema object, with no Markdown fences or commentary. The 1,200-token cap and provider capability settings are exposed in diagnostics for evidence-based follow-up.

## C. Files changed

- `src/tender_intelligence/interfaces/openai_compatible.py`: record safe request capabilities and response-field extraction metadata while retaining only `message.content` as completion text.
- `src/tender_intelligence/verdict/service.py`: add safe request/response shape diagnostics, explicit JSON-only instructions, and one bounded malformed-JSON correction attempt.
- `tests/integration/test_verdict_engine.py`: exercise malformed/plain/fenced/null/array responses, redacted shape reporting, correction success and bounded failure, plus existing schema/citation/reduction checks.
- `tests/unit/test_openai_compatible_llm.py`: verify content extraction does not substitute hidden reasoning and extraction metadata contains no response text.
- `demo-evidence/prompt-16f-stage-b-malformed-json-report.md`: this report.

## D. Automated verification

- Selected Stage B, document-bundle, generic OpenAI-compatible client, payload-reduction, and orchestrator persistence tests: passed.
- Focused response classification and bounded correction tests: passed.
- Ruff on the four changed source/test files: passed.
- Pytest emitted a cache-directory warning (`WinError 183`); selected test outcomes passed.
- Full repository suite/type check: not run.

## E. Live Tender 163

Not run. `curl` could not connect to `127.0.0.1:8000` (`HTTP 000`), so current Test Mode, Groq profile, and Tender 163 configuration were unavailable. No Process Now request was sent, no data was cleared, and no email was sent. There is no final verdict from this work and no new Stage 15 delivery evidence.

## F. Remaining issues

**Unresolved:** the exact structure and cause of the historical 40-byte completion; current live profile/reasoning configuration; persisted Tender 163 verdict and Test Mode notification outcome.

**Separate issues reported in the prompt:** Tender 160's storage `FileNotFoundError`/incomplete bundle policy and the French deadline-parser test `test_date_limite_july_gmt`. They were not investigated or changed. Stage-level `PARTIAL` semantics were not changed.

Prompt 17 was not started. The live verification gate remains open.

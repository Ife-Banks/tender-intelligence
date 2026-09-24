# Prompt 14 — Stage B Implementation Report

Status: implementation complete; independent behavioral verification remains the next gate.

## Files and behavior

- `src/tender_intelligence/verdict/service.py` implements Stage B context assembly, strict Pydantic JSON validation, semantic evidence/deadline/urgency checks, one validation retry, bounded transient retries, approved-profile fallback, map/reduce, budget checks, safe LLMCall and AlertEvent metadata, persisted Verdict records, a structured failure outcome, and a pure notification-content formatter.
- `src/tender_intelligence/orchestrator/coordinator.py` adds the `14-verdict` handoff stage. Only durable Stage A `passed` decisions reach its injected callback. When no Stage B runtime is configured, the stage is recorded as `SKIPPED_NOT_IMPLEMENTED`.
- `src/tender_intelligence/db/models/verdicts.py`, `src/tender_intelligence/db/models/llm.py`, and `src/tender_intelligence/db/models/deadline.py` add provenance and authoritative deadline fields. `migrations/versions/0012_verdict_stage_b.py` adds these columns.
- `src/tender_intelligence/db/repositories/tenders.py` now persists Prompt 12.1's original source timezone. `src/tender_intelligence/db/repositories/status.py` allows the verdict wait/failure states.
- `src/tender_intelligence/interfaces/llm.py` represents absent usage as unknown (`None`); Stage A call recording handles the same interface without inventing zero usage.
- `tests/integration/test_verdict_engine.py` exercises persistence, formatting, LLMCall provenance, and the unapproved-profile zero-invocation rule using a mock provider. Pipeline regression covers PASS-only handoff.

## Specification mapping

- `docs/07 §7.1–§7.4`: Stage B only follows a persisted PASS; complete bundle and KB are supplied; output schema version `verdict.v1`, prompt version `stage-b.v1`; one validation retry then `verdict_failed` and safe alert hook.
- `docs/07 §7.3`: tender evidence must match an actual document ID/location/quote; KB claims must cite an actual Markdown heading and quote. Unsupported claims are `unverified`; unsupported gaps require “No evidence on file for …”.
- `docs/07 §7.5, §7.11–§7.12`: two retries for known timeout/rate-limit/429/5xx classes, fallback profile for provider failures, monthly budget pause at 100%, configurable warning share, and no invented monthly budget.
- `docs/07 §7.6, §7.8–§7.10`: persisted provider/profile/model/KB/prompt/schema/map-reduce provenance; all documents are mapped when needed and the complete KB remains in the reduce prompt; every candidate profile is checked for `approved_for_company_docs` before KB is included.
- `docs/04 §4.2, §4.6–§4.7, §4.12–§4.13`: verdict stage handoff is after triage; formatter returns data only; missing inputs remain marked incomplete; failure and budget states are explicit.
- `docs/03`: extends existing `Verdict`, `LLMCall`, and deadline resolution entities. No parallel verdict model was introduced.
- `docs/10 §10.1`: KB content and prompts/responses are not written to logs or LLMCall; metadata only.
- `docs/13`: O9 monthly budget remains unconfigured if null. Provider host is used as safe provider label because the current LLMProfile schema has no provider-name field. Approval for company docs remains explicit on each profile.

## Contracts and decisions

Generation outcome statuses: `VERDICT_AVAILABLE`, `VERDICT_FAILED`, `AWAITING_BUDGET`, `AWAITING_APPROVED_PROVIDER`. Failures set the tender state and return `automatic_assessment_unavailable=true`; no notification is sent. The formatter returns background, requirements, canonical deadline data, applicability text, recommendation, confidence, urgency, incomplete-input warning, and unavailable state. It does not select recipients/providers or plan attachments.

Resolved deadlines are rendered with canonical UTC date/time and `timezone=UTC`, alongside the original source timezone label stored by Prompt 12.1. Unresolved/conflicting deadlines have no exact date/time. This avoids inventing a timezone conversion from abbreviations. Urgency uses the configured `urgency_window_days`; the current implementation compares elapsed calendar days. Holiday/business-day interpretation remains a specification ambiguity to resolve during independent verification.

The current application has no production LLM adapter/runtime wiring. Stage B uses the established injectable `LLMClientFactory` seam; tests use mocks only. The orchestration callback must be wired to `VerdictEngine.generate` by the runtime configuration before live use. No live credentials or provider calls were used.

Map/reduce uses a character-based token estimate (`ceil(chars / 4)`), maps every document, retains only mapped evidence quotes in reduce context, and fails safely if the KB or an individual document cannot fit. The budget remains unset unless configured; cost values absent from provider usage remain null.

## Tests run

- `tests/integration/test_verdict_engine.py`, `tests/integration/test_migrations.py`, and `tests/unit/test_interfaces.py`: 17 passed in an isolated run.
- `tests/integration/test_triage_service.py`: 11 passed.
- Pipeline checks `test_triage_handoff_only_receives_pass_and_records_run_link` and `test_full_offline_source_run_walks_04_to_09_and_persists_every_stage`: both passed.
- `tests/integration/test_orchestrator_pipeline.py::test_triage_handoff_only_receives_pass_and_records_run_link`: passed; one passed triage decision reached the verdict callback while two discarded tenders did not.
- The initial full suite completed with two failures. The Prompt 14 pipeline trace assertion expected every future stage to be COMPLETED; it was updated to expect the deliberate no-runtime `SKIPPED_NOT_IMPLEMENTED` state and then passed independently. `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` failed on a pre-existing fixture containing replacement characters (`d�p�t`, `�`); this belongs to the WAHO parser/Prompt 04 boundary and was not changed here. The full suite was not rerun after correcting the pipeline assertion.
- `python -m compileall -q src/tender_intelligence`: passed.

## Remaining verification focus

Independent verification should probe every malformed-output branch, retry/fallback classification, data-policy behavior for fallback, document evidence and map/reduce citations, resolved/unresolved/conflicting deadline fixtures, urgency boundaries, budget warning/pause, timeline persistence, and concurrent/repeated execution. The existing pipeline callback is a seam, not an installed production LLM runtime. No Admin UI, email send, RAG, embedding, vector search, or source adapter was added.

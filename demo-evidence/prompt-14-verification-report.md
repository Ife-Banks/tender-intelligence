# Prompt 14 — Independent AI Verdict Behavioral Verification

## Scope and baseline

Read-only verification of Stage B verdict generation and formatter. No product code, tests, prompts, configuration, or migrations were modified. Repository HEAD: `16b654383a0f6633f8eeaea4bc0bbe9b098bc883`. The worktree was already dirty with unrelated Prompt 16C/admin/docs changes and existing pytest temporary directories; those were left untouched.

Prompt 14 implementation files inspected: `src/tender_intelligence/verdict/service.py`, `src/tender_intelligence/db/models/verdicts.py`, `src/tender_intelligence/db/models/knowledge.py`, `src/tender_intelligence/db/models/llm.py`, `src/tender_intelligence/orchestrator/coordinator.py`, `src/tender_intelligence/audit/timeline.py`, `src/tender_intelligence/notifications/service.py`, `src/tender_intelligence/mail/templates.py`, `tests/integration/test_verdict_engine.py`, and migration `0012_verdict_stage_b.py`.

Specifications reviewed: `PROJECT_RULES.md`; `docs/07-ai-verdict-spec.md` §§7.2–7.13; `docs/04-pipeline-spec.md` §§4.2–4.9; `docs/03-data-model.md` entities Verdict, KnowledgeBaseVersion, LLMCall, LLMProfile, LLMRoleAssignment; `docs/10-security-spec.md`; `docs/13-open-decisions.md`; `prompts/14-ai-verdict-engine.md`; `prompts/13-ai-triage.md`; implementation report and current-state traceability artifacts. `prompts/12.1-*` is not present; authoritative deadline resolution exists under `src/tender_intelligence/deadline/` and is tested separately.

## Ownership and implementation findings

Stage B engine is implemented as `VerdictEngine.generate`; it gates on a persisted triage result with status `passed`, selects the `verdict` role, checks profile approval before loading/sending KB content, reads a current versioned KB, assembles document/deadline context, validates structured output, records actual LLM calls, persists successful Verdict provenance, and exposes a pure formatter. No Stage B verdict or notification delivery is performed by the formatter.

**Integration finding (HIGH):** `RunCoordinator` accepts an optional `verdict_handoff`; when it is absent, the default runtime explicitly records Stage B as skipped with detail `no Stage B engine configured`. The Stage B engine is therefore callable/testable but is not wired as a default production pipeline service. Coordinator PASS-only callback behavior exists, but this does not prove the full source→documents→triage→verdict path executes Stage B in the normal runtime.

## Verification commands and results

- `.venv\Scripts\python.exe -m pytest -q -r a -p no:cacheprovider --basetemp=.pytest-p14-independent tests/integration/test_verdict_engine.py` — 8 passed.
- Focused regression: `.venv\Scripts\python.exe -m pytest -q -r a -p no:cacheprovider --basetemp=.pytest-p14-regression tests/integration/test_deadline_resolution.py tests/unit/test_deadline_model.py tests/unit/test_deadline_service.py tests/unit/test_deadline_waho_parser.py tests/integration/test_triage_service.py tests/integration/test_orchestrator_pipeline.py tests/integration/test_notification_service.py` — all selected tests passed except the one French WAHO parser failure below.
- Full suite: `.venv\Scripts\python.exe -m pytest -q -r a -p no:cacheprovider --basetemp=.pytest-p14-full` — 619 collected, 618 passed, 1 failed.
- The single failure is `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`, because a French date string containing replacement characters (`d�p�t`, `�`) yields no parsed deadline. This is a pre-existing Prompt 12.1/deadline parser issue, outside Prompt 14 ownership, but it blocks complete authoritative-deadline verification.

The eight direct Stage B tests cover: successful persistence/formatter without email send; unapproved profile not invoked; triage discard/failure gating; exactly one invalid-output retry; bounded timeout retries plus fallback; unapproved primary routing to approved fallback; budget block before model call; and incomplete-input propagation. These are mock-provider integration tests, not a real production provider run.

## Behavioral evidence matrix

| Test ID / scenario | Expected | Observed | Result / evidence |
|---|---|---|---|
| P14-01: triage PASS | Stage B executes | Direct engine accepts persisted `passed` result; coordinator only invokes injected callback | PARTIAL; engine test and coordinator code |
| P14-02: triage discarded/failed | no Stage B model call | Direct test confirms both are rejected | PASS for engine boundary |
| P14-03: normal runtime handoff | PASS reaches Stage B | Coordinator default records Stage B skipped when `verdict_handoff` absent | FAIL / HIGH integration gap |
| P14-04: verdict persistence/provenance | Verdict + actual LLMCall metadata persisted | Successful mock integration test asserts key provenance and one call | PASS for tested fields |
| P14-05: approval boundary | no KB to unapproved provider | Unapproved-only and unapproved-primary/approved-fallback scenarios are covered with spies | PASS for tested paths; broader retry/fallback permutations unverified |
| P14-06: transport retry/fallback | bounded retries, accurate calls | Timeout path exercises primary initial + two retries + fallback, with each actual call recorded | PASS for timeout case; 429/5xx/permanent cases unverified |
| P14-07: validation retry | exactly one validation retry, then explicit failure | Two invalid JSON outputs yield two calls and no successful Verdict | PASS for malformed JSON; schema/semantic-invalid response not separately exercised |
| P14-08: budget | block before provider at budget cap | 100% cap test verifies awaiting state and no model call | PASS at cap; 80% warning boundary and accounting history not independently tested |
| P14-09: incomplete inputs | flag propagated and limitations exposed | Mock fixture verifies incomplete flag forwarded/persisted | PARTIAL; bundle failure is manually represented, not full extraction-to-verdict flow |
| P14-10: tender/KB evidence chain | citations valid and evidence classes distinct | Validator checks exact document quote/location and KB heading/quote; direct fixtures do not prove adversarial positive/negative chains end to end | PARTIAL |
| P14-11: no evidence on file | absence not turned into negative fact | Validator requires unverified status and “no evidence on file” for unsupported unverified capability | PARTIAL; adversarial fabricated-negative model outputs not independently executed |
| P14-12: verdict derivation | verdict follows assessment statuses/gaps | Semantic validator derives expected enum from assessment list | PARTIAL; only conditions fixture directly exercised |
| P14-13: deadline states | resolved/unresolved/conflicting faithfully preserved | Context builder consumes resolved row and semantic checks require exact fields; no full three-state independent fixture matrix | PARTIAL; French parser regression also fails |
| P14-14: urgency | derives from configured deadline window independent of verdict | Validator computes from UTC deadline and `urgency_window_days`; no boundary matrix run | UNVERIFIED |
| P14-15: whole KB / oversized map-reduce | complete KB retained; every doc summarized if needed and provenance marked | Code retains full KB and maps each document; no oversized behavioral test found in `test_verdict_engine.py` | UNVERIFIED |
| P14-16: KB historical version | verdict pins exact immutable version | model has KB version FK and service selects latest at run; no v1/v2 historical integration test | UNVERIFIED |
| P14-17: alert/failure contract | failed verdict explicit, alert hook and downstream unavailable state | service marks tender failed and triggers AlertManager; direct test verifies no Verdict after invalid response, not persisted alert lifecycle/downstream raw-notice path | PARTIAL |
| P14-18: formatter boundary | structured content only, no email/send side effect | integration test confirms formatter output and no send invocation | PASS for tested boundary |
| P14-19: full pipeline | persisted source candidate→bundle→triage→verdict→formatter | Not exercised; default coordinator skips Stage B without injected handoff | FAIL / HIGH integration gap |
| P14-20: repository regression | no regression | 618/619 pass; one known French WAHO deadline parser failure | FAIL (owned outside P14) |

## Implementation contract observations

- Output schema is strict Pydantic with `schema_version=verdict.v1`, prompt version `stage-b.v1`, strict string verdict validated against the three allowed labels, numeric confidence in `[0,1]`, deadline fields, assessments, citations, gaps, urgency, and incomplete-input state.
- Semantic validation verifies citations point to exact supplied tender document locations/quotes and KB section headings/quotes; met claims require KB citations; unsupported capability is required to be unverified; verdict is derived from the provided assessments.
- The requirements list is supplied by the model and validation checks those listed requirements have assessments. It does not independently derive an authoritative complete requirement inventory from the tender; completeness of every material requirement is therefore not proven by this validation alone.
- Deadline context uses the persisted resolution state and does not reparse raw tender text. Exact UTC/display values are checked; unresolved/conflicting states cannot carry fabricated display dates.
- Profile candidates are filtered for active and `approved_for_company_docs` before calls; actual calls record `LLMCall` metadata. API keys and prompt contents are not written into `LLMCall`.
- Map step summarizes each document and validates quoted evidence; no test in the Prompt 14 integration file proves oversized trigger, all-doc retention, output provenance, or cost/call count.
- No Prompt 14 changes were made. No live LLM or email provider was contacted. No business email was sent.

## Defects and remaining issues

1. **HIGH — Stage B normal-runtime integration absent.** Owner: Prompt 14 integration seam. Root cause: optional `verdict_handoff` defaults to `None`, and normal coordinator flow marks verdict skipped. Evidence: `src/tender_intelligence/orchestrator/coordinator.py` constructor and `_execute` branch. Fix: wire the Stage B engine through the existing pass-only handoff in a separately authorized remediation task and add an end-to-end persisted pipeline test.
2. **HIGH verification blocker — authoritative deadline regression.** Owner: deadline/Prompt 12.1. Root cause: French WAHO replacement-character date fixture is not parsed. Evidence: exact failing test above. Not changed because outside Prompt 14 verification ownership.
3. **MEDIUM evidence debt — required Stage B adversarial matrices remain unverified.** Owner: Prompt 14 verification/testing. Missing independent behavioral evidence includes all verdict enums/derivation, exact tender-vs-KB citation adversaries, unsupported negative claim challenge, all deadline states and urgency boundaries, KB version immutability, oversized map/reduce, 80% budget warning, 429/5xx/permanent provider outcomes, fallback-denied cases, alert persistence, and end-to-end incomplete bundle flow. Existing unit/integration suite passing does not establish these claims.
4. **MEDIUM evidence debt — complete requirement enumeration is not independently anchored.** The semantic validator checks assessments for model-supplied `requirements`, but no independent requirement extractor/list proves the model did not omit a material tender requirement. This warrants behavioral review against project policy before treating completeness as verified.

## Final gate

PROMPT 14: NEEDS FIXES

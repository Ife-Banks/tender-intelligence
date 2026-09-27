# Prompt 14 Remediation #2 — Implementation Report

**Scope:** production worker Stage B wiring and material requirement completeness. This is an implementation report, not an independent verification or a declaration that Prompt 14 is verified.

## Gate

**PROMPT 14 REMEDIATION #2: IMPLEMENTED — READY FOR INDEPENDENT RE-VERIFICATION**

The two cited implementation blockers are addressed by the production entrypoint regression and the distinct, source-grounded requirement extraction pass with exact-set assessment validation. The required focused tests pass. The full suite has one separately identified, explicitly out-of-scope Prompt 12.1 WAHO parser failure; it is not represented as a green suite.

## 1. Original defects

The independent re-verification identified (1) that the executable headless worker did not construct and execute the Stage B-enabled pipeline, and (2) that completeness was only checked for consistency between requirement and assessment lists supplied by the final verdict model.

## 2. Root causes

`worker.main()` previously performed startup/bootstrap work without calling the canonical `build_pipeline()` constructor and running the resulting worker. Consequently, a test that merely invoked the constructor and then substituted a separately assembled coordinator could not establish production startup wiring.

`VerdictEngine.generate()` previously gave the verdict model the document bundle and relied on `_validate_semantics()` to compare `payload.requirements` and `payload.assessments`. That only established internal consistency of the final response, not a separate source-based requirement set.

## 3. Changes made

- `src/tender_intelligence/worker/main.py`: extended `build_pipeline()` with injectable transports/policy for deterministic tests; retained real `RunCoordinator`, `build_verdict_handoff()`, and `Worker` construction. `main()` now seeds/commits startup configuration, builds local storage, calls `build_pipeline()`, calls `worker.run_once()`, closes network clients, records the worker result, and returns its success code. Optional provider factories keep tests credential-free and preserve the configured profile approval path.
- `src/tender_intelligence/verdict/service.py`: added strict `RequirementExtraction`/`MaterialRequirement` response schemas; a separate extraction request over tender document content; validation of each quoted source reference against extracted document IDs, locations, and text; a single correction retry; and exact normalized set checks requiring each extracted requirement exactly once in the final requirements and assessment lists, with no orphan or omitted entries. The assessment evidence must also retain the independently extracted tender source references. Extraction calls use the existing verdict provider policy and are recorded through the existing LLM call path. Empty source content fails safely rather than producing a complete verdict.
- `tests/integration/test_orchestrator_pipeline.py`: added `test_worker_entrypoint_uses_stage_b_enabled_pipeline` using the actual `worker.main()` and its actual `build_pipeline()` path. Only external DB/HTTP/config/provider edges are controlled. The test does not replace the production-created coordinator. It asserts Stage A pass and durable Stage B verdict/provenance, plus the Stage B failure semantics. Existing no-verdict tests cover DISCARD and triage failure; existing coordinator test covers persisted Stage B failure.
- `tests/integration/test_verdict_engine.py`: added complete, missing assessment, orphan assessment, model omission, and incomplete-input coverage using source documents with extractable text and exact source references. Existing validation, retry, approval, budget, fallback, evidence, and persistence coverage remains.
- `tests/integration/test_worker.py`: made the empty-database startup test seed no active external source, so it covers startup without requiring provider credentials or accidental network work.

No WAHO parser, Prompt 17, provider credentials, provider approval, production budget, alert recipient, email provider, OPEX KB, or external source credential configuration was changed.

## 4. Production worker path

The exercised path is:

```text
worker.main()
→ build_pipeline(maker, LocalFileSystemStorage, injected external edges)
→ RunCoordinator(..., verdict_handoff=build_verdict_handoff(...))
→ Worker(coordinator=that RunCoordinator, ...)
→ worker.run_once()
→ Stage A triage PASS
→ verdict_handoff / VerdictEngine
→ persisted Verdict and provenance
```

The Stage B handoff is wired in `build_pipeline()` for normal worker startup. Missing provider configuration remains a safe recorded failure, rather than silently omitting Stage B or requiring test credentials.

## 5. Requirement completeness mechanism

The existing persisted verdict contract represents requirements and assessments in `Verdict.requirements_summary`; it has no separate stable requirement entity/ID model. The implementation therefore does not invent IDs or a parallel persistence model. After document acquisition/processing and any existing context reduction, the verdict service makes a distinct structured extraction call over available tender document content. Each material requirement must include a quote and existing document ID/location. Those references are checked against the source bundle's extracted text. The final assessment call receives that extracted set and must reproduce it exactly once in both its requirement list and assessment list. Missing, duplicate, or orphan entries, or missing independent source evidence, fail validation after the existing single correction retry. Persisted requirement summaries retain the assessment text and tender evidence citations; standard verdict provider/model/KB/prompt/schema/run provenance is preserved.

If there is no extracted source text, extraction cannot be validated and the service records `requirement_sources_unavailable`. Failed document inputs retain the bundle's existing `incomplete_inputs` state and are passed through to the final assessment; unavailable documents do not create fabricated requirements. This is an independent extraction *pass* and source-reference check within the existing model-provider architecture, not a formal proof that a probabilistic extractor can never miss a material requirement. That residual semantic-recall limitation is explicit for independent re-verification.

## 6. Test evidence

Commands and exact results:

| Coverage | Result |
|---|---|
| Worker/orchestration focused suite (`test_orchestrator_pipeline.py`, `test_worker.py`) | 27 passed |
| Actual worker entrypoint `test_worker_entrypoint_uses_stage_b_enabled_pipeline` | 2 passed (success and Stage B failure parameter cases) |
| Positive Stage A PASS → Stage B → persisted Verdict and provenance | Passed in positive entrypoint case |
| Stage A DISCARD; Stage A triage failure | Both covered and passed by `test_build_pipeline_stage_b_not_invoked_for_non_pass_triage` |
| Stage B failure status/error and no false Verdict | Passed in entrypoint and coordinator failure cases (`verdict_failed`, no Verdict) |
| Direct Stage B/verdict suite (`test_verdict_engine.py`) | 12 passed |
| Complete independently extracted requirements (R1/R2/R3) | Passed |
| Missing assessment; orphan assessment; final model omission | All three parameter cases passed; invalid verdict rejected with no persisted Verdict |
| Incomplete source inputs | Passed; `incomplete_inputs` retained in request and persisted verdict |
| Existing Stage B/orchestration checks, including provider fallback/retry and handoff | 27 passed in focused worker/orchestration suite; direct verdict suite 12 passed |
| Full suite | 632 passed, 1 failed, 1 warning in 240.15s |
| Ruff on five changed implementation/test files | All checks passed |

Full-suite failure (unchanged and out of scope): `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` (parser returned `None`; expected deadline). Do not read the full-suite result as green.

## 7. Known unrelated regression

`tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` remains a separate Prompt 12.1 issue and was not modified.

## 8. Remaining limitations

Independent extraction is a separate provider call with strict source quote/location validation and downstream exact-set enforcement. Semantic exhaustiveness of an LLM extraction is not mathematically guaranteed; independent re-verification should assess whether this meets the specification's practical completeness requirement. Production provider credentials and approvals, monthly budget, production alert/email configuration, OPEX knowledge-base content, external source credentials, and deployment behavior remain configuration/deployment matters; this implementation does not manufacture evidence or mark them passed.

## 9. Gate

**PROMPT 14 REMEDIATION #2: IMPLEMENTED — READY FOR INDEPENDENT RE-VERIFICATION**

This gate means the two implementation blockers have been addressed and scoped tests pass. It does not declare Prompt 14 VERIFIED and does not claim a fully green full suite.

# Prompt 14 Independent Reverification Report

## Result

**PROMPT 14: NEEDS FIXES**

The HIGH normal-worker Stage B integration defect is not resolved. `build_pipeline()` exists, but the executable worker path `main()` never calls it, constructs a `RunCoordinator`, or runs a `Worker`. The new purported end-to-end test calls `build_pipeline()` and then replaces its coordinator with a separately constructed `RunCoordinator`; its success therefore does not prove production wiring. The coordinator still accepts `verdict_handoff=None` and records Stage B as skipped in that case.

Requirement completeness is also not independently established: validation checks that each model-supplied requirement has an assessment, but does not independently establish that the model listed every material tender requirement. Multiple material requirements remain unverified, so the close gate fails independently of the worker defect.

## Verification metadata and scope

- **Repository HEAD:** `16b654383a0f6633f8eeaea4bc0bbe9b098bc883`
- **Remediation inspected:** working-tree changes described by `demo-evidence/prompt-14-remediation-report.md` and `demo-evidence/prompt-14-remediation.json`, especially `worker/main.py`, the coordinator and verdict integration changes, and added orchestrator tests. These remediation files were untracked/modified working-tree content at the start of this verification, not part of HEAD.
- **Verification time:** 2026-09-26 (UTC; run completed approximately 03:00 UTC).
- **Mode:** verification only. No product, test, configuration, migration, prompt, fixture, generated-code, dependency, or secret files were edited by this verification. No production credentials, real LLM calls, business email, or paid provider were used.
- **Artifacts created by this verification:** this report, `prompt-14-reverification-result.json`, and `prompt-14-reverification-results.txt` only. Temporary pytest directories were removed.
- **Pre-existing dirty tree:** substantial unrelated working-tree changes were present at the start, including changes to `.env.example`, README/docs/prompts, Admin and worker code, tests, prior evidence, and numerous pytest output directories. They were left unchanged. The remediation-related files were also already dirty/untracked before this verification.

## Source order and authority

The v1.1 source specification remains authoritative. I inspected it first, then `PROJECT_RULES.md`, then `prompts/14-ai-verdict-engine.md`, `docs/07-ai-verdict-spec.md`, `docs/04-pipeline-spec.md`, `docs/03-data-model.md`, `docs/10-security-spec.md`, and `docs/13-open-decisions.md`. I compared the previous independent report/result/text with the available remediation report/result/summary. The remediation report claims that `build_pipeline()` is the production worker path; that claim was checked against the callable entrypoint and complete repository search, not accepted on the report's assertion.

## Normal-runtime wiring investigation

### Repository search

Repository-wide search for `RunCoordinator(`, `verdict_handoff`, `VerdictEngine`, `no Stage B engine configured`, `stage_b`, `Stage B`, and `VerdictEngine.generate` found:

- `RunCoordinator.__init__` still declares optional `verdict_handoff=None` in `src/tender_intelligence/orchestrator/coordinator.py:187`.
- If absent, `_drive()` records Stage B skipped with the exact detail `no Stage B engine configured` at `coordinator.py:402-403`.
- `worker/main.py:37-109` defines `build_pipeline()` and wires `build_verdict_handoff()` into a coordinator.
- The actual `worker/main.py:115-176` `main()` path only loads settings, opens/seeds the database, logs bootstrap completion, returns, and disposes the engine. It never calls `build_pipeline()`, `Worker.run*()`, or `RunCoordinator`.
- The other production coordinator construction is Admin's `_compose_run_coordinator`; it is not the normal headless worker entrypoint.
- `tests/support/pipeline.py` conditionally injects a handoff for its own harness.
- The new test `test_build_pipeline_wires_stage_b_and_verdict_is_persisted` calls `build_pipeline()` at `test_orchestrator_pipeline.py:1077`, then explicitly creates a new mock-transport coordinator at lines 1111 onward and invokes `offline_coordinator.run_source()`. The coordinator returned by `build_pipeline()` is not the one that runs the demonstrated tender. The DISCARD/FAIL and provider-failure tests likewise manually build coordinators at lines 1215 and 1308.

### Demonstrated versus not demonstrated

The independent `test_verdict_engine.py` suite exercises the existing engine and provider abstraction directly, and passes. The orchestrator integration file passes, including triage and persisted verdict cases using the manually built offline coordinator. Those results show that the engine and a coordinator with a manually supplied handoff work. They do not exercise the normal worker entrypoint, and no test demonstrates `main()` → worker construction → coordinator → Stage A → Stage B → persisted Verdict. Thus the original defect remains: **normal worker runtime does not reach Stage B at all**. The remediation introduces no duplicate-generation behavior in the actual worker path because that path does not start processing; idempotency under the intended runtime remains unverified.

## Test execution

Commands used the repository `.venv` with `PYTHONDONTWRITEBYTECODE=1`, pytest cache provider disabled, and a workspace-local temporary base directory to avoid the sandbox-denied system temp directory.

| Run | Result |
|---|---|
| `tests/integration/test_verdict_engine.py` | 8 collected; 8 passed; 0 failed; 0 skipped. |
| `tests/integration/test_orchestrator_pipeline.py` | 24 collected; 24 passed; 0 failed; 0 skipped. |
| Full repository suite | 627 collected; 626 passed; 1 failed; 0 skipped. Exact failure: `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`. |
| Required isolated French WAHO regression | Failed again with `dl is None` for replacement-character French text (`d�p�t`, `�`); same exact test name. |

The full suite command was `.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=.tmp-p14-reverify -q`. The direct Stage B suite and orchestrator suite used the same options and their respective test file. The required regression was run separately by fully qualified node id. Collection counts were checked independently with `--collect-only`.

## Requirement classification

`PASS` means behavior was independently exercised; `FAIL` means behavior is demonstrably incorrect or missing; `PARTIAL` means some behavior is proven but acceptance coverage is incomplete; `BLOCKED` is reserved for genuine business/external input decisions; `UNVERIFIED` means evidence is insufficient; `N/A` means the requirement does not apply.

| Requirement | Status | Evidence / limit |
|---|---|---|
| Normal worker construction wires Stage B and invokes it | **FAIL — HIGH implementation defect** | `worker.main()` never invokes `build_pipeline()` or starts the worker. `build_pipeline()` test replaces the returned coordinator before running. Blocks closure. |
| Coordinator has a non-optional/default Stage B dependency | **FAIL — HIGH integration defect** | `verdict_handoff` remains optional; `None` produces `no Stage B engine configured`. Blocks closure. |
| Production-shaped PASS → Stage B → Verdict persistence | **FAIL** | Only the manually reconstructed offline coordinator executes; production worker path is not run. Provider abstraction and persistence work in direct/integration tests, but not through normal runtime. Blocks closure. |
| PASS/DISCARD/FAIL Stage A gating | **PARTIAL** | Direct engine test `test_triage_discard_and_failure_cannot_enter_stage_b` and pipeline tests exercise gating with a manually injected handoff. The three paths are not proven through the normal worker runtime. |
| Stage B durable provider failure, no false success, correlation/run information | **PARTIAL** | Provider-failure orchestrator case observes Stage B PARTIAL, failed tender status, failed LLMCall rows, and no Verdict using a manually assembled coordinator. Durable failure is shown there; production worker integration and alert delivery are not. |
| Stage B alert/failure hooks | **PARTIAL** | Failure logic calls `AlertManager`, and broader alert tests pass in the suite; no end-to-end Prompt 14 assertion verifies alert record linkage from normal worker execution. Deployment alert delivery is not exercised. |
| Verdict enum/schema, required structured fields and persistence | **PARTIAL** | Direct tests verify a persisted structured verdict and schema/prompt/provider/KB metadata; code rejects invalid enum/semantic values. The matrix of malformed/missing required fields and downstream formatting is not comprehensive. |
| All material tender requirements identified and assessed | **FAIL — implementation/evidence gap** | Validator at `verdict/service.py:713` checks only that every model-supplied requirement appears among model-supplied assessments. It has no independent completeness mechanism. It cannot establish the source-spec requirement that every material tender requirement is covered. Blocks closure. |
| Evidence citations and no-evidence wording | **PARTIAL** | Semantic checks validate supplied document locations/quotes and KB headings/quotes; tests exercise no-evidence wording and basic persistence. The adversarial evidence matrix, false negative claims, and citation coverage across realistic source evidence are incomplete. |
| Validation retry accounting | **PARTIAL** | `test_validation_retry_is_exactly_once_and_records_both_actual_calls` proves invalid output is followed by exactly one retry, two provider observations/call rows, and no successful Verdict after the second invalid answer. Accepted-first-attempt is covered. A valid-after-retry scenario is not independently shown. |
| Transport retry, provider failover, fallback approval | **PARTIAL** | `test_transport_retries_are_bounded_then_fallback_and_record_actual_model` proves timeout attempts are bounded at three then fallback succeeds; approval tests show unapproved profiles are not called and approved fallback receives KB. 429, 5xx, permanent error, fallback unavailable/rejected and each resulting failure state are not all tested. |
| Acquisition/extraction failure → incomplete input → safe verdict | **UNVERIFIED** | Existing test `test_incomplete_bundle_flag_is_forwarded_and_persisted` manually sets `bundle_incomplete=True`; it does not cause a real document acquisition/processing failure. Full required propagation path is not proven. |
| Oversized bundle map/reduce, coverage, provenance and bounded cost | **UNVERIFIED** | Implementation contains map/reduce logic and `map_reduce_used`; there is no adversarial oversized behavioral test in the Prompt 14 integration file or collected tests found by search. Code inspection alone is not PASS. |
| Deadline resolved/unresolved/conflicting states, display fields and urgency boundaries | **UNVERIFIED** | Verdict deadline semantics exist, but the required state/urgency matrix is not independently tested. Separate required WAHO parser check fails; ownership is Prompt 12.1/deadline, not Prompt 14 absent causal evidence. |
| KB version immutability and historical verdict linkage | **UNVERIFIED** | A verdict references a KB version id/hash-backed object in tested basic flow; no v1→v2 immutable-history test demonstrates old verdict provenance remains unchanged. |
| 80% warning, 100% gate and auditable monthly usage | **PARTIAL** | Direct test proves a 100% budget gate prevents calls and sets `awaiting_budget`. The 80% boundary is not behaviorally exercised. OPEX monthly amount remains the open business decision O9; no amount is invented. |
| Provider approval boundary for company KB | **PASS for tested mechanism** | `test_unapproved_profile_never_invoked_with_kb` and `test_unapproved_primary_routes_only_to_approved_fallback` inspect actual fake-client calls and KB payloads. Production provider/deployment approval remains outside this test. |
| Reconstruct successful Verdict and LLMCall without exposing secrets | **PARTIAL** | Tests inspect provider/model/profile, KB and prompt/schema versions, correlation, run association in failure path, usage call rows, and verdict reference. No complete reconstruction test covers every required source reference/timestamp/failure field and secret non-persistence. |
| Duplicate verdict/notification/LLM work on rerun | **UNVERIFIED** | No safe rerun/idempotency Prompt 14 fixture was identified; normal worker does not process runs. |
| Formatter consumes structured verdict and does not send email/LLM | **PASS for boundary tested** | `test_stage_b_persists_verdict_provenance_and_formats_without_sending` formats persisted structured data; formatter is a pure content builder, with no provider or mail call. |
| Admin dry-run, Test Mode, business-email safety and worker independence | **PARTIAL** | No real email/provider was used and full regression suite ran. This verification did not establish deployment-level Test Mode routing or admin-outage worker operation; moreover the current worker entrypoint only bootstraps. |
| Remediation did not duplicate generation | **UNVERIFIED** | No production-shaped repeated-processing/idempotency test. |

Business/configuration limitations distinct from defects:

- Monthly budget value is an open OPEX decision (O9); its absence is a genuine business/configuration block, not permission to invent a value.
- Approved production LLM selection/company-data approval and deployment alert delivery require external owner/deployment evidence. Fake-provider tests make no assertion about those external approvals.
- No live email, live LLM, or paid service was used.

## Findings

### F-14-01 — HIGH — Worker entrypoint does not use Stage B construction

- **Requirement:** normal source-to-verdict worker path must call the existing Stage B engine after Stage A PASS and persist the Verdict.
- **Reproduction:** inspect `worker/main.py:115-176`; observe `main()` seeds settings/recipients, logs startup completion and returns. Search finds no call to `build_pipeline()`. Run/inspect `test_build_pipeline_wires_stage_b_and_verdict_is_persisted`: after calling `build_pipeline()`, it creates `offline_coordinator = RunCoordinator(...)` with a hand-entered `verdict_handoff` and drives that replacement coordinator.
- **Evidence:** `coordinator.py:187,402-403` still permits absent Stage B and records the exact skipped reason. The integration tests pass but are not using the normal worker construction/run path.
- **Ownership:** Prompt 14 normal worker integration.
- **Blocks Prompt 14 closure:** yes. The original confirmed HIGH defect remains.

### F-14-02 — HIGH — No independent requirement-completeness mechanism

- **Requirement:** every material tender requirement needs a corresponding assessment; verdict must derive from the assessments/evidence.
- **Reproduction/evidence:** `_validate_semantics()` only compares the payload's own requirements set with its own assessments (`verdict/service.py:713+`). The model can omit a material source requirement and provide a self-consistent smaller requirements/assessment list; validation has no independent source requirement extraction/completeness check.
- **Ownership:** Prompt 14 evidence/requirement completeness.
- **Blocks Prompt 14 closure:** yes. This is a documented evidence concern that remains unaddressed.

### F-12-01 — Known separate deadline regression — French WAHO parser

- **Severity:** existing known regression; ownership remains Prompt 12.1/deadline.
- **Reproduction:** `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` fails because `_parse_deadline()` returns `None` for the test's replacement-character French fixture.
- **Evidence:** isolated rerun failed, and the full suite reports the same exact test. This is consistent with the previous verification. No evidence ties it to Prompt 14 remediation.
- **Blocks Prompt 14 closure:** no automatic Prompt 14 attribution; separately remains a repository regression and deadline-owned defect.

## Failure ownership and closure decision

The normal worker integration defect and requirement-completeness failure are implementation defects owned by Prompt 14. Other rows marked PARTIAL/UNVERIFIED are acceptance evidence debt; they are not claimed fixed merely because code appears to handle them. O9 and deployment-level provider/alert evidence are distinct business/external blocks. The WAHO French parser failure remains explicitly separate under Prompt 12.1.

The gate is **PROMPT 14: NEEDS FIXES** because a HIGH Prompt 14 integration defect remains, and the material requirement completeness contract has no independent enforcement or behavioral proof. Passing remediation tests and passing the engine's direct tests do not satisfy the normal runtime gate.


# Prompt 10 — Pipeline Orchestration Independent Verification Report

**Date:** 2026-09-23
**Gate Status:** `PROMPT 10: VERIFIED — READY FOR PROMPT 11`

---

## A. Implementation Inspected

| Module / File | Role |
|---|---|
| `src/tender_intelligence/orchestrator/worker.py` | `Worker.run_once` — due-source pass, explicit-id selection, `force`, `dry_run`, per-source isolation loop |
| `src/tender_intelligence/orchestrator/coordinator.py` | `RunCoordinator` — per-run config reload, RunHistory lifecycle, `_drive` over `STAGE_ORDER`, status derivation, source stamping, alert seam |
| `src/tender_intelligence/orchestrator/stages.py` | `StageRunner.run[T]` — order/attribution/error-translation/failure-containment per stage; JSON-safe stage map |
| `src/tender_intelligence/orchestrator/status.py` | `RunStatus`, `StageNumber`, `StageStatus`, `STAGE_ORDER`, `StageOutcome`, `derive_run_status` |
| `src/tender_intelligence/orchestrator/scheduler.py` | `SourceScheduler` — due/never-run/inactive decisions; no invented cadence |
| `src/tender_intelligence/orchestrator/registry.py` | `AdapterRegistry` — type→adapter mapping; `source_not_runnable` for unknown types |
| `src/tender_intelligence/orchestrator/retry.py` | `RetryPolicy` (attempts/backoff) + `run_with_retry` with injected sleeper |
| `src/tender_intelligence/orchestrator/alerts.py` | `AlertNotice` + `NullAlertHook.notices` seam |
| `src/tender_intelligence/orchestrator/errors.py`, `config.py`, `__init__.py` | `SourceNotRunnableError`, `ConfigLoader`, exports |
| `src/tender_intelligence/db/models/runs.py`, `db/repositories/runs.py` | `RunHistory` + `config_version`/`failed_stage`/`error_code`/`stages` (migration 0004) |
| `src/tender_intelligence/acquisition/service.py` | `_store` wraps put failures as retryable `STORAGE_FAILED` (retry loop consumed by the orchestrator) |
| `tests/integration/test_orchestrator_pipeline.py` | 15 integration tests — full real 04→09 path §19 A, J, K + §19 B–L failure/dry-run/secret scenarios |
| `tests/support/pipeline.py`, `tests/support/documents.py`, `tests/support/stubs.py` | Offline `Harness`: `OfflineSite`, `LocalFileSystemStorage`, migrated SQLite, `StubOcrEngine`, real WAHO fixtures, `mixed_pdf` |

---

## B. Tests Executed

| Command | Result |
|---|---|
| `uv run pytest tests/integration/test_orchestrator_pipeline.py -q` | **15 passed** (all §19 A–L) |
| `uv run pytest -q` (full suite, prompts 04–10) | **354 passed in 69.51s** |
| `uv run mypy src/tender_intelligence` | **Success: no issues found in 97 source files** |
| `uv run ruff check src tests` | **All checks passed** |

---

## C. Behavioral Results — §19 A–L

| §19 | Scenario | Result | Evidence (test in `test_orchestrator_pipeline.py`) |
|---|---|---|---|
| A | Full offline source run 04→09 with final counts | PASS | `test_full_offline_source_run_walks_04_to_09_and_persists_every_stage` — one `RunHistory` START→COMPLETED, all six stages COMPLETED in order, 3 tenders, listings/new/update counts, config version, storage artifacts |
| B | Mid-run crash after discovery | PASS | `test_crash_after_discovery_leaves_a_running_row_and_the_next_run_recovers` — `SystemExit` after 04 leaves the committed row RUNNING (no stages, no stamp); a fresh run completes it; rows `[RUNNING, COMPLETED]`; no duplicate tender identity; no stuck RUNNING |
| C | Configuration reload between runs | PASS | `test_configuration_change_is_observed_by_the_next_run` — `ensure_settings()==1` → run (`config_version 1`) → `change_configuration` → 2 → run again (`config_version 2`); rows `[1, 2]` |
| D | Admin app unavailable | PASS | `test_worker_runs_without_an_admin_http_app` — `run_once` with no admin app; every request starts with `BASE_URL`; ran 1, failed 0, COMPLETED |
| E | Source failure isolation (two test sources) | PASS | `test_source_isolation_and_mystery_type_failure` — mystery `source_type` fails only its own run (`source_not_runnable`, 04 FAILED, 05–09 PENDING, row ERRORED); the good source still completes with 3 tenders; `report.ran==2`, `failed==1`, `failures==()`, `skipped==()` |
| F | Stage failure → structured error, ERRORED row, no silent failure | PASS | `test_code_less_registry_failure_maps_to_stage_failed_and_alerts` — registry `build` raises code-less `RuntimeError` → `STAGE_FAILED`; run FAILED, stage 04 FAILED recorded; alert notice `STAGE_FAILED` reached the seam; row ERRORED, `error_count==1` |
| G | Document-level partial failure uses Prompt 09 semantics | PASS | `test_one_failed_document_does_not_fail_the_run` — `mixed_pdf(native=1, scanned=1)` with `ocr=None`; acquisition COMPLETED, processing PARTIAL (`failed_items==3`, one per tender); run PARTIAL, never FAILED, `error_count==0`; failed docs `extraction_status=="failed"`, `extraction_error_code==OCR_FAILED` |
| H | Retry/backoff — transient | PASS | `test_retryable_storage_failure_backs_off_once_and_completes` — one gated `/attachments/` put failure; `RetryPolicy(attempts=2, backoff 1.0–3.0)`; delays `[1.0]`; acquisition attempts 4 (165=2, 166/167=1), `failed_items==0`, COMPLETED, all rows `downloaded` |
| H | Retry/backoff — exhausted | PASS | `test_exhausted_storage_retries_stay_document_level` — persistent put failure; delays `[1.0,1.0,1.0]`; attempts 6 (3 tenders × 2), acquisition PARTIAL `failed_items==3`; run PARTIAL, never FAILED, `error_count==0`; failed rows `download_status=="failed"` |
| I | Dry-run | PASS | `test_dry_run_reports_the_plan_and_persists_nothing` — `DRY_RUN`, `run_history_id is None`, `planned_actions==3`; no RunHistory/Tender rows, `stored_keys()==[]`; listing fetched, no detail pages; live run after → 3 tenders |
| J | Repeated run | PASS | `test_repeated_run_reuses_state_and_records_a_separate_run` — second run records its own row, no duplicate tender identity, existing documents/artifacts reused, no refetch of attachment pages |
| K | Correlation / traceability | PASS | `test_a_run_is_traceable_from_row_to_stage_to_tender_to_document` — `RunHistory.correlation_id` → row stages → tender → document; persisted stage map reconstructs the run without timestamps |
| L | Secret safety | PASS | `test_secret_never_reaches_logs_reports_or_persisted_state` — injected fake token in `parser_config` absent from `caplog.text`, `str`/`repr` of report and each run, `repr(run.stage_map())`, row `stages`/`error_code`/`failed_stage`/`correlation_id`, and `Source.last_error` |

---

## D. Behavioral Results — §1–§18, §21

| Area | Result | Evidence |
|---|---|---|
| **§1 Worker independence** | PASS | Worker reads persisted `Source` + `Setting` rows through the DB; admin app never contacted (D above) |
| **§2 Scheduling / due logic** | PASS | `run_once` iterates due sources; explicit ids run regardless of due; no invented cadence (`realised inside stage 05`); verified through A, D, E |
| **§3 Configuration reload** | PASS | Config re-read per run via `_config_loader.load()` inside `_execute`, never cached; C above stamps fresh `config_version` per run |
| **§4 Run vs correlation identity** | PASS | Run is the per-source attempt; correlation id is the tracing value; distinct values asserted in A/K; `correlation_context` wraps each run |
| **§5 RunHistory lifecycle** | PASS | RUNNING row committed before stage 04 (`_open_run`); completed/errored via `_finalize_run`; PARTIAL completes with tallies; B/F show crash and error paths |
| **§6 Execution order** | PASS | `STAGE_ORDER` = 04 discovery → 05 dedup → 06 persistence → 07 detail → 08 acquisition → 09 processing; stage map order asserted in A |
| **§7 Incremental Phase-1 wiring** | PASS | Orchestrator composes existing stages; no second persistence/config/scheduler layer, no WAHO-specific behavior in generic code |
| **§8 Stage statuses** | PASS | COMPLETED / PARTIAL / FAILED / PENDING persisted per stage (E, F, G) |
| **§9 Failure semantics** | PASS | Stage failure contained; subsequent stages PENDING; document-level failures never become run failures (F, G, H) |
| **§10 Structured errors** | PASS | Claimant error code preserved (`source_not_runnable`, `OCR_FAILED`); code-less exception maps to `stage_failed`; message stays content-free (F, G, L) |
| **§11 Retry/backoff ownership** | PASS | Retry policy injected at the coordinator; no unbounded retries; storage-failure wrap is retryable only where the fetcher never owned the failure (H) |
| **§12 Crash safety** | PASS | `SystemExit` after discovery leaves a reconstructable RUNNING row; fresh run completes; no stuck state (B) |
| **§13 Idempotency/resumability** | PASS | Unchanged tenders skip detail refetch and persist reuse; separate run records (J) |
| **§14 Dry-run** | PASS | Inspects and plans only; writes no business state; sends nothing; seen/document state unchanged (I) |
| **§15 Source isolation** | PASS | A failing source errors only its own run and row; the worker iterates on (E) |
| **§16 Alert hook** | PASS | `NullAlertHook.notices` recorded a run-level `STAGE_FAILED` notice carrying `source_id` (F) |
| **§17 No AI / No email** | PASS | No AI or email code in the orchestrator (grep-inspected; email is a later prompt's job, `coordinator.py` module docstring) |
| **§20 Regression 04–09** | PASS | Full suite 354 passed; see D-table below |
| **§21 Constraints** | PASS | Compositional `Worker → RunCoordinator → config/scheduler/runner/retry/repo`; no framework added; no second persistence/config/scheduler; WAHO behavior stays in adapters |

---

## E. Regression — §20

| Earlier stage area | Status | Evidence |
|---|---|---|
| WAHO discovery (04) | PASS | Existing discovery tests green in full suite |
| Deduplication decisions (05) | PASS | `tests/integration/test_dedup_service.py` (14) green |
| Persistence ownership / tender identity (06) | PASS | `test_pipeline_04_06.py` (2), `test_prompt06_final.py` (8) green; no duplicate identity in A/B/J |
| Document discovery (07) | PASS | Detail pages discovered within the integrated run (A) |
| Document acquisition (08) | PASS | 46 acquisition tests green; acquisition page fetches traced (J) |
| Storage semantics + ZIP safety (08) | PASS | `test_storage.py`, `test_acquisition_zip.py` (17) green |
| Extraction + bundle persistence + reuse (09) | PASS | 119 processing tests green (52 integration + 67 unit) |
| Config seam (04 §4.1) | PASS | `test_config_loader.py` (3) green |

No earlier-stage behavior was modified by Prompt 10.

---

## F. Defects Found

| # | Symptom | Root Cause | File/Module | Fix | Regression Test |
|---|---|---|---|---|---|
| 1 | Ruff E501 in a new `§19 G` assertion line (one line > 100 cols) | Authoring slip during test drafting | `tests/integration/test_orchestrator_pipeline.py` | Re-wrapped the comprehension | `ruff check src tests` clean |
| 2 | Stage-08 test files had pre-existing lint drift (unused import, E501, missing final newlines, import order) | Accumulated during earlier acquisition pass | `tests/integration/test_acquisition_service.py`, `tests/unit/test_acquisition_fetcher.py`, `tests/unit/test_acquisition_zip.py` | Fixed during this verification | `ruff check src tests` clean |

No new implementation defects found during this verification pass.

---

## G. Files Changed During Verification

| File | Change |
|---|---|
| `tests/integration/test_orchestrator_pipeline.py` | Appended §19 B–L integration tests + helpers (`_CrashAfterDiscoveryRunner`, `_ExplodingRegistry`, `_flaky_put`); fixed an E501 |
| `tests/integration/test_acquisition_service.py` | Lint fixes (unused import, E501, final newline) |
| `tests/unit/test_acquisition_fetcher.py` | Lint fixes (import order, final newline) |
| `tests/unit/test_acquisition_zip.py` | Final newline |
| `docs/08-verification-report.md` | Created (Stage 08 report) |
| `docs/10-verification-report.md` | Created (this report) |

---

## H. Remaining TODOs / Open Decisions

| Item | Category | Notes |
|---|---|---|
| O18 Vision vs OCR default | Open decision (prompt 09 era) | Scanned pages route to OCR; no approved vision provider; normal OCR path verified |
| O22 Unsupported-format default | Open decision (prompt 09 era) | Skipped formats retained, no `incomplete_inputs`; flagged for OPEX |
| Email notification wiring on alert notices | Future prompt | `NullAlertHook` consumed alerts; a real transport is a later prompt's job |
| Update `docs/context-history.md` + `implementation/03-next-task.md` for Prompt 10 | Housekeeping | Done in this pass |

---

## I. Deviations

None. Prompt 10 composes the existing Prompts 04–09 modules through its own `orchestrator/` package; no earlier-stage ownership was moved and no second persistence/config/scheduler layer was introduced. The only cross-stage change is `migrations/0004` adding `config_version`, `failed_stage`, `error_code`, `stages` to `RunHistory` (prompt 10 §5, §8), which is additive.

---

## Final Gate

```
PROMPT 10: VERIFIED — READY FOR PROMPT 11
```

All §19 A–L required tests exist as real offline integration tests over the actual 04→09 path (real WAHO HTML fixtures, real `OfflineSite` bytes, real `LocalFileSystemStorage`, migrated SQLite, `StubOcrEngine`, injected sleeper/retry/registry/crash seams). Every claim is asserted against the database or the object store — the returned report, logs, and persisted `RunHistory` are cross-checked against each other. Full regression suite: **354 passed**; `mypy` clean (97 files); `ruff` clean. Prompt 10 is verified and ready for Prompt 11.
# Prompt 10 Context History

## Overview
This document captures the implementation history and state as of the Prompt 10 — Pipeline Orchestration verification pass.

## Prompt 08 → Prompt 09 Transition
- Prompt 08 (document acquisition) was completed with new package `src/tender_intelligence/acquisition/` (8 modules + `__init__.py`).
- Prompt 08 known issue — empty `AcquisitionResult` on re-run (`service.py:210`, `:259`) — is tracked in `docs/08a-acquisition-report.md`; it is unreachable from the orchestrator (already-stored attachments are never fed back into `acquire`) and pinned by `test_rerun_reports_existing_documents_not_empty`.
- Stage 08 lint drift (unused `pytest` import, one E501, missing final newlines, one import order) was fixed during the prompt-10 verification pass.

## Prompt 09 Implementation
New package `src/tender_intelligence/processing/` created with 10 modules; consume Prompt 08 `Document` rows, read bytes through the configured storage abstraction, extract text/tables/language, produce one reusable `TenderDocumentBundle` per tender. See `docs/09-document-processing-report.md` and `docs/09-verification-report.md`.

## Prompt 10 Implementation
New package `src/tender_intelligence/orchestrator/` (11 modules):

| Module | Key Functions |
|---|---|
| `worker.py` | `Worker.run_once` — due-source pass, explicit-id selection, `force`, `dry_run`, per-source isolation loop |
| `coordinator.py` | `RunCoordinator` — per-run config reload (`_execute` re-reads via `_config_loader.load()`), RunHistory lifecycle (`_open_run`/`_finalize_run`/`_stamp_source`), `_drive` over `STAGE_ORDER`, status derivation, alert seam |
| `stages.py` | `StageRunner.run[T]` — order/attribution/error-translation/failure-containment; JSON-safe stage map |
| `status.py` | `RunStatus`, `StageNumber`, `StageStatus`, `STAGE_ORDER`, `StageOutcome`, `derive_run_status` |
| `scheduler.py` | `SourceScheduler` — due/never-run/inactive decisions; no invented cadence |
| `registry.py` | `AdapterRegistry` — type→adapter mapping; `source_not_runnable` for unknown types |
| `retry.py` | `RetryPolicy` (attempts/backoff) + `run_with_retry` with injected sleeper |
| `alerts.py` | `AlertNotice` + `NullAlertHook.notices` seam |
| `errors.py`, `config.py`, `__init__.py` | `SourceNotRunnableError`, `ConfigLoader`, exports |

Cross-stage additive change: `migrations/versions/0004_run_history_stage_state.py` adds `config_version`, `failed_stage`, `error_code`, `stages` to `RunHistory` (prompt 10 §5, §8).

## Key Design Decisions (Prompt 10)

1. **Run vs correlation identity**: the run is the per-source attempt row; the correlation id is the tracing value; `correlation_context` wraps each run.
2. **Config reload**: re-read per run inside `_execute`, never cached; each `RunHistory` stamps the `config_version` it started under (§3).
3. **Crash safety**: the RUNNING row is committed before stage 04; `SystemExit`/crashes leave a reconstructable row; a fresh run completes it (§5, §12).
4. **Failure containment**: stage exceptions are captured into outcomes (§10); document-level failures never become run failures (§9, G/H).
5. **Retry ownership**: storage-failure wrap (`_store` → `STORAGE_FAILED`, retryable) is the stage-local retryable category; bounded attempts + backoff via injected sleeper (§11).
6. **Dry-run**: genuinely read-only — plans actions, fetches listing, writes no business state (§14).
7. **Alert seam**: `NullAlertHook.notices` records run-level failures; a real transport is a later prompt's job (§16).
8. **Test harness**: `tests/support/pipeline.py` wires `OfflineSite` + real `LocalFileSystemStorage` + migrated SQLite + `StubOcrEngine`; coordinator seams (stage_runner, registry, retry_policy, sleeper) injectable per test.

## Test Evidence (Prompts 08–10)
- **Prompt 08**: 46 tests (15 integration `test_acquisition_service.py` + 14 unit `test_acquisition_fetcher.py` + 17 unit `test_acquisition_zip.py`)
- **Prompt 09**: 119 tests (52 integration `test_processing_service.py` + 67 unit across pdf/docx/languages/store/representation)
- **Prompt 10**: 15 integration tests in `test_orchestrator_pipeline.py` covering §19 A–L (full offline run, crash recovery, config reload, admin independence, source isolation, stage failure, document-level failure, retry/backoff transient+exhausted, dry-run, repeated run, correlation, secret safety)
- **Full regression suite**: **354 passed in 69.51s** (prompts 04–10)
- **Lint/type**: `ruff check src tests` clean; `mypy src/tender_intelligence` clean (97 files)

## Files Introduced (Prompt 10 pass)
- `src/tender_intelligence/orchestrator/` (11 .py files + `__init__.py`)
- `migrations/versions/0004_run_history_stage_state.py`
- `tests/integration/test_orchestrator_pipeline.py`
- `tests/support/pipeline.py`, `tests/support/stubs.py`, `tests/support/documents.py`
- `docs/08-verification-report.md` (Stage 08 verification)
- `docs/10-verification-report.md` (Prompt 10 verification)
- `docs/08a-acquisition-report.md`, `docs/09-document-processing-report.md` (earlier completion reports)

## Committed State
- Branch `main` is ahead of `origin/main`.
- This pass commits Stages 08–10 (acquisition, processing, orchestration) + both verification reports.

## Current Status
- **Prompt 10 verification**: Independent gate passed; **PROMPT 10: VERIFIED — READY FOR PROMPT 11**
- **Next task**: Prompt 11 — test-mode email.

## Open Items (housekeeping)
- O18 (Vision vs OCR default) and O22 (unsupported-format default) remain open decisions from the Prompt 09 pass.

## Prompt 11 Implementation

Prompt 11 added the Test-Mode-safe notification layer without changing the 04→09 pipeline:

- `src/tender_intelligence/mail/`: provider-neutral `MailProvider`, Sendlib adapter, registry,
  capability planner, retry/failover chain, persisted circuit breaker, signed links, and
  deterministic templates.
- `src/tender_intelligence/notifications/`: recipient/Test Mode routing, durable notification
  service/outbox, update-event seam, red-banner fallback, recipient/provider/settings safety
  services, and signed archive access.
- Migrations `0005`–`0009`: notification correlation/dedupe, breaker failure persistence,
  outbox lifecycle/lease fields, provider-name attempt snapshots, and durable provider usage
  counters.
- `docs/11-email-notification-report.md`: architecture, safety evidence, limitations, and
  open decisions.

Test Mode remains ON by default. While ON, the service routes only to configured development
recipients, applies `[TEST]`, reserves a durable outbox row before provider I/O, re-checks the
mode before every send/retry, and records every attempt. Sendlib is the only concrete adapter;
providers 2/3 remain TBD. No AI, real provider endpoint, business delivery, or go-live switch
was exercised.

Final Prompt 11 verification on 2026-09-24: **469 passed, 1 warning in 158.97s**; Ruff and
mypy are clean; migration tests pass; Alembic is at `0009_provider_usage (head)`. The required
files `implementation/00-current-state.md`, `implementation/01-decisions.md`, and
`implementation/02-known-issues.md` were not present in the repository and were not recreated.

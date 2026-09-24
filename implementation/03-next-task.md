# Prompt 11 — Test-Mode Email

## Overview
Prompt 10 (Pipeline Orchestration) is verified. This next task implements test-mode email behaviour (docs/08 email-notification spec §? + prompt 11): in test mode the worker must demonstrate that it *would* send notifications without actually sending to real recipients.

## What Prompt 10 Delivered (context)
Prompt 10 verified a single integrated 04→09 path with real offline evidence:

- `src/tender_intelligence/orchestrator/` (11 modules) — `Worker`, `RunCoordinator`, `StageRunner`, `SourceScheduler`, `AdapterRegistry`, `RetryPolicy`, `NullAlertHook`, statuses/`STAGE_ORDER`.
- `tests/integration/test_orchestrator_pipeline.py` — 15 tests covering §19 A–L (full run, crash recovery, config reload, admin independence, source isolation, stage failure, document-level partial failure, retry/backoff transient + exhausted, dry-run, repeated run, correlation, secret safety).
- Migration `0004` adds `config_version`, `failed_stage`, `error_code`, `stages` to `RunHistory`.
- Test harness `tests/support/pipeline.py` wires `OfflineSite` + real `LocalFileSystemStorage` + migrated SQLite + `StubOcrEngine` with injectable coordinator seams.
- Full suite: **354 passed in 69.51s**; `mypy` clean (97 files); `ruff` clean.

## Prompt 11 Approach (test-mode email)
- Confirm the test-mode semantics required by prompt 11 (what must be demonstrated vs what must be suppressed) before implementing.
- Compose with existing prompts: settings row already carries `test_mode` (`config_version` stamping observed in Prompt 10 §19 C); email transport must be a seam, not a hardcoded dependency.
- Add §-lettered integration tests following the Prompt 10 pattern (real offline path, assertions against persisted state / logs / seam captures, no real sleeps, no `;` in Python, ≤100 cols, ruff/mypy clean).
- Update `docs/context-history.md`, `docs/11-*` completion/verification reports, and this file afterwards.

## Current Status
- **Prompt 10 gate**: `PROMPT 10: VERIFIED — READY FOR PROMPT 11`
- **Prompt 11 implementation**: notification layer, Test Mode routing, Sendlib adapter,
  provider chain/breaker, signed-link planner, templates, durable outbox, and safety tests are
  implemented in the working tree. See `docs/11-email-notification-report.md`.
- **Prompt 11 verification**: `uv run pytest` reports **469 passed** (one pre-existing
  Starlette/AnyIO deprecation warning); Ruff and mypy are clean, and Alembic is at
  `0009_provider_usage (head)`.
- **Safety boundary**: Test Mode remains ON by default; no real provider or business-recipient
  delivery was exercised.
- **Next task**: Prompt 12 remains untouched; do not start it automatically.

## Gate Verdict
```
PROMPT 11: VERIFIED — READY FOR NEXT PROMPT
```
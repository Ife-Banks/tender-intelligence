# 05a — Tender Deduplication: Task Report

> Companion record for `prompts/05-tender-deduplication.md`. This file is **not** a
> requirements spec (that is `docs/04` / `docs/03`); it records the dedup task's final report
> (prompt 05 — Report) exactly per the §Report template: files changed, decision matrix,
> seen-state transaction point, concurrency strategy, RunHistory lifecycle, tests + results,
> traceability, TODOs/risks, scope check.

## 1. Files changed

| File | Change |
| --- | --- |
| `src/tender_intelligence/dedup/__init__.py` | **New.** Public surface (prompt 05 §11): `DedupService`, `DedupResult`, `DedupOutcome`, `DedupError`, `NEW`/`UPDATE`/`UNCHANGED`, change-type constants, `RUNNING`/`COMPLETED`/`ERRORED`, `derive_run_status`, `compare`, `ChangePolicy`, `normalize_listing`/`normalize_tender`, `ADDENDUM_MARKER_KEY`. |
| `src/tender_intelligence/dedup/classify.py` | **New.** Deterministic change detection (prompt 05 §2): normalization (whitespace, UTC-aware datetimes), `Comparison` with `material_change` + `change_types`, `ChangePolicy`-scoped materiality encoding docs/04 §4.14. |
| `src/tender_intelligence/dedup/service.py` | **New.** `DedupService.run(source_id, listings)` → `DedupResult`: seen-state loads, per-candidate classification, NEW insert under savepoints, UPDATE persistence, run history, error handling, crash point. |
| `src/tender_intelligence/db/engine.py` | `_use_explicit_sqlite_transactions` + `build_engine` applies it to sqlite URLs — pysqlite's implicit `BEGIN` would make `RELEASE SAVEPOINT` silently **commit** (breaks `begin_nested` rollback); explicit `BEGIN` restores PostgreSQL-equivalent SAVEPOINT semantics (production dialect parity). |
| `src/tender_intelligence/db/models/runs.py` | `RunHistory` extended (prompt 05 §7): `update_count`, `unchanged_count`, `correlation_id` (run cid); lifecycle documented as **derived**. |
| `migrations/versions/0002_run_history_counts.py` | **New.** Additive migration: the three columns above + index on `correlation_id`; downgrade drops them. |
| `src/tender_intelligence/core/errors.py` | Four dedup error codes (prompt 05 §13) added to the machine-readable catalogue: `DEDUP_TRANSACTION_FAILED`, `DEDUP_MISSING_IDENTITY`, `DEDUP_INVALID_STATE`, `DEDUP_MALFORMED_CANDIDATE`. |
| `tests/conftest.py` | `sqlite_engine` fixture now uses `build_engine` (so explicit-BEGIN hooks apply to the shared in-memory DB used by migration/persistence tests). |
| `tests/unit/test_dedup_classify.py` | **New.** 13 unit tests for §2: normalization, identical→UNCHANGED, title/deadline/addendum marker changes, naive/aware instants, reference-only changes stay UNCHANGED by default, custom `ChangePolicy`, deterministic field order, multi-change aggregation. |
| `tests/integration/test_dedup_service.py` | **New.** 13 integration tests against a real (file + in-memory) SQLite via `build_engine` + alembic head: first run, idempotency, deadline/title/addendum updates, two-source non-collision, duplicate-in-run, crash mid-transaction, two-thread concurrency, RunHistory failure traceability, missing identity / malformed / unknown source errors, mixed run counts, metadata merge. |
| `docs/03-data-model.md` | `RunHistory` documented as extended (0002) with a **derived** lifecycle. |
| `prompts/05-tender-deduplication.md` | Line-ending fix only (pre-existing duplicated prompt text left as-is). |

## 2. Deduplication decision matrix

Existing seen-state key = `(source_id, external_id)` (docs/05; docs/03 `Tender` unique constraint).

| Existing seen state | Current listing | Result | Material change | Notes |
| --- | --- | --- | --- | --- |
| none | any valid listing | NEW | N/A | insert seen row under savepoint |
| exists | identical (after normalization) | UNCHANGED | false | skipped, counted |
| exists | title changed | UPDATE | true | `TITLE_CHANGED` |
| exists | deadline changed | UPDATE | true | `DEADLINE_CHANGED` (docs/04 §4.14) |
| exists | new addendum/change marker appeared | UPDATE | true | `ADDENDUM_ADDED` |
| exists | existing addendum/change marker changed | UPDATE | true | `CHANGE_MARKER_CHANGED` |
| exists | field changed but not in `ChangePolicy.material_fields` (e.g. `reference`, `url` alone) | UNCHANGED | false | incidental/parser diff (prompt 05 §1 "do not treat incidental differences as material"); fields recorded in `Comparison.changed_fields`, `material_change` false |
| exists | other explicitly configured material field changed | UPDATE | true | `OTHER_MATERIAL_CHANGE` via custom `ChangePolicy(material_fields=...)` |

Matrix adjustment vs the prompt's sample: the prompt's "other explicitly configured material
change" is realized as the configurable `ChangePolicy`; by default `url`/`reference`/
`deadline_timezone` are **non-material** per docs/04 §4.14. No contradicting materiality rule
was invented — ambiguity is a documented TODO (§8, gap G2).

## 3. Seen-state transaction point

- A candidate becomes **seen** inside `DedupService._dedup_transaction`
  (service.py:226), which is a single commit covering: all seen-state changes
  (`_dedup_classify`) **and** the run's `ended_at`. This is the standardized crash point
  (prompt 05 §3).
- The transaction protecting it is the unit-of-work transaction of a dedicated
  `Session` (fresh `self._maker()`), committed by `_dedup_transaction`. Each NEW insert is a
  `savepoint` (`session.begin_nested()`), so the unique-constraint race is caught per-candidate
  without aborting the whole run (prompt 05 §8).
- **Crash before commit:** the outer transaction rolls back (honest boundary — the crash test
  bypasses `except Exception` and still rolls back via `finally: session.close()`). No seen row
  persists; RunHistory stays RUNNING with `ended_at IS NULL`. The next scheduled run
  reclassifies the candidate and commits it exactly once — nothing is double-committed.
- **Crash after commit, before downstream:** the seen row is durable. The next run sees the
  identity and classifies `UNCHANGED` (or `UPDATE` on a material change) — never `NEW`, never a
  duplicate seen record — so no re-notification can occur (docs/04 §3, §4.7).
- pysqlite caveat handled: see §1 `db/engine.py` row (explicit `BEGIN` so SAVEPOINT release does
  not commit early).

## 4. Concurrency strategy

Two runs racing to insert the same identity cannot both persist:

1. DB-level uniqueness: the persisted `Tender` seen state carries the
   `(source_id, external_id)` unique constraint (docs/03).
2. `SELECT → if missing → INSERT` is done inside a **savepoint**; the loser's INSERT raises
   `IntegrityError`, the savepoint rolls back, and the code re-selects the winner and
   reclassifies against it (`_insert_new`, service.py:336) — never emitting a second NEW event.
3. If after the race no winner row is found (impossible in practice), the run fails loudly with
   `DEDUP_INVALID_STATE` rather than guessing.
4. Test: `test_concurrent_runs_do_not_double_create_seen_state` runs two barrier-synced threads
   against a file-backed SQLite (WAL + busy_timeout) and asserts exactly one persisted row.
   RunHistory rows are per-run (no shared row), so concurrent runs do not collide there either.

## 5. RunHistory lifecycle

Status is **derived** from persisted fields, never stored (docs/04 §4.8) —
`derive_run_status` (service.py:101):

```text
ended_at IS NULL                          → RUNNING   (in progress, or crashed mid-run)
ended_at set  AND error_count == 0        → COMPLETED
ended_at set  AND error_count  > 0        → ERRORED
```

- **Run start:** `_open_run` (service.py:182) inserts a row with `started_at=now`,
  `correlation_id=<run cid>`, all counts 0 and `ended_at IS NULL`, and commits in its own
  transaction — so an in-flight/crashed run is always visible and reconstructable.
- **Counts:** `listings_found`, `new_count`, `update_count`, `unchanged_count` are filled by
  `_dedup_classify` (service.py:292) and persisted in the single dedup commit (§3).
  `duplicate_count` is surfaced on `DedupResult` only (not a stored column — prompt requires
  `new/update/unchanged`).
- **Completion:** `_dedup_transaction` sets `ended_at=now(UTC)` in the same commit as the seen
  state.
- **Failure:** `_mark_errored` (service.py:205) rolls back, sets `ended_at`, increments
  `error_count`, appends the run cid to `failed_correlation_ids`, and commits in its own
  transaction — so the failed run is never silently discarded, and UNCHANGED ≠ error.
- Retried/next runs create fresh rows; no duplicated RunHistory just because a run is retried.

## 6. Tests

Commands (environment: Windows, `uv` project, Python 3.14.3, SQLAlchemy 2.0.54; dev DB SQLite,
production target PostgreSQL/psycopg — dialect parity for transaction semantics covered in §1):

```text
uv run pytest tests/unit/test_dedup_classify.py tests/integration/test_dedup_service.py -q
26 passed in ...s

uv run pytest -q
122 passed, 1 warning in ...s

uv run ruff check .
All checks passed!

uv run mypy src/tender_intelligence
Success: no issues found in 61 source files
```

The dedup suite covers every prompt-05 test type: first-seen (1), identical second run (2),
deadline change (3), addendum/change marker (4), title change (5), different source identity
(6), duplicate candidate in one run (7), crash safety (8), concurrency (9), RunHistory (10),
correlation IDs (11), full regression + lint + type check (12). No real WAHO network, email, or
LLM calls are required.

## 7. Traceability

| Requirement | Implementation | Test | Status |
| --- | --- | --- | --- |
| `source_id + external_id` identity | `_load_seen`/`_insert_new` keyed on the pair; DB unique constraint (docs/03) | `test_two_sources_with_same_external_id_are_independent` | PASS |
| NEW detection | no seen row → `_insert_new` under savepoint | `test_first_run_inserts_new_tender` | PASS |
| UPDATE detection | `_maybe_update` persists change + `is_update=True` | `test_title_and_reference_change` | PASS |
| UNCHANGED detection | `compare` → `UNCHANGED`, skipped + counted | `test_identical_second_run_is_unchanged_and_no_duplicate` | PASS |
| deadline change | `DEADLINE_CHANGED`, material, independent | `test_deadline_change_is_update` | PASS |
| addendum/change marker | `ADDENDUM_MARKER_KEY` seam → `ADDENDUM_ADDED`/`CHANGE_MARKER_CHANGED` | `test_addendum_marker_change_is_material`, classify unit tests | PASS (seam only — see gap G1) |
| crash safety | single commit boundary + honest rollback test | `test_crash_mid_transaction_leaves_no_seen_state_and_recovers` | PASS |
| concurrency | savepoint + unique-race reclassification | `test_concurrent_runs_do_not_double_create_seen_state` | PASS |
| idempotency | seen-state is source of truth; identical re-run → UNCHANGED | `test_identical_second_run_...` | PASS |
| correlation IDs | per-run row, per-tender row, `correlation_map`, stamped logs/errors | `test_run_history_correlation_and_failure_traceability` | PASS |
| RunHistory | RUNNING→COMPLETED/ERRORED derived; counts persisted | freshness + failure traceability tests | PASS |

## 8. TODOs / Risks

**Specification gaps (business policy unresolved — not implemented silently):**

- **G1 — Addendum seam unpopulated (documented gap, not a defect):** `ADDENDUM_MARKER_KEY`
  (`raw_metadata["addendum_marker"]`) is the reserved seam, but **Prompt 04's WAHO adapter never
  populates it today** (see `docs/04a`). `ADDENDUM_ADDED`/`CHANGE_MARKER_CHANGED` cannot fire for
  real WAHO data until discovery supplies that marker. Tests exercise the seam directly.
- **G2 — Materiality defaults:** default `ChangePolicy` encodes docs/04 §4.14 (title, deadline,
  addendum marker). No spec defines whether `reference`/`url`/`published_at` changes are
  material — defaults treat them as incidental (UNCHANGED), overridable via
  `ChangePolicy(material_fields=...)`. Flag for prompt-06/10 confirm.
- **G3 — Update re-run rule for AI verdict:** prompt-05 §5 says material updates requiring
  verdict re-evaluation should be exposed; doc `docs/07` (AI verdict spec) is out of this
  prompt's scope, so only `material_change`/`change_types` are exposed.

**Implementation risks (already mitigated):**

- **R1 — SQLite/psycopg transaction parity:** mitigated by `build_engine` explicit-BEGIN (crash
  test validates the honest boundary). Live PostgreSQL revalidation recommended when a real DB
  arrives.
- **R2 — Concurrency test DB constraint:** file-backed SQLite deadlocks two writers in rollback
  journal mode; test uses WAL + busy_timeout, which is SQLite-specific and not a production
  concern.
- **R3 — `Tender.status` overwritten on update** (`raw_metadata` merge + `is_update=True`):
  prompt-06 owns the final persistence model; the seen-row update here is deliberately narrow.
  No code, only the test suite, depends on `status="updated"` yet.

## 9. Scope Check

Prompt 05 implemented **only** the dedup/new-listing-detection stage. It did **not** implement:
WAHO crawling/HTML parsing/listing or detail/attachment discovery (04 — `sources/waho.py`
untouched); Tender/document persistence design (06 — only a narrow seen-state update of the
existing `Tender` row); document download/processing/OCR (07/08/09); AI triage/verdicts (07);
email/notification delivery or idempotency keys (08/11); pipeline orchestration (10); admin UI,
RAG, or other source adapters. Prompt 04's parser was not modified; no real network/email/LLM
calls are required by the automated suite.
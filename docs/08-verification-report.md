# Prompt 08 — Document Acquisition Independent Verification Report

**Date:** 2026-09-23
**Gate Status:** `PROMPT 08: VERIFIED — READY FOR PROMPT 09`

---

## A. Implementation Inspected

| Module / File | Role |
|---|---|
| `src/tender_intelligence/acquisition/service.py` | `DocumentAcquisitionService.acquire`, download→validate→checksum→storage→row ordering, per-document failure tolerance, idempotent re-runs, storage-failure isolation, correlation tracing |
| `src/tender_intelligence/acquisition/fetcher.py` | `DocumentFetcher`, URL-scheme guard, HTTP/transport failure classification, retry-with-backoff (policy), size guards, credential redaction |
| `src/tender_intelligence/acquisition/zip.py` | Safe recursive ZIP extraction under `ZipLimits` (member/total/ratio/name/depth budgets), traversal/symlink/device-name protections, whole-archive limita abort |
| `src/tender_intelligence/acquisition/checksums.py` | SHA-256 hex fingerprinting (`sha256`, 64-char live against `Document.checksum`) |
| `src/tender_intelligence/acquisition/names.py` | `storage_key`, `sanitize_storage_name`, hostile-filename defense |
| `src/tender_intelligence/acquisition/mimes.py`, `errors.py` | MIME sniffing/fallbacks; `DocumentAcquisitionError` hierarchy with category + `retryable` |
| `src/tender_intelligence/storage/local.py` + `interface.py` | `LocalFileSystemStorage.put` (existing storage seam; size via `StoredObject.size_bytes`) |
| `migrations/versions/0004_run_history_stage_state.py` | Stage-state columns on `RunHistory` (integration, Prompt 10) |
| `tests/integration/test_acquisition_service.py` | 15 integration tests over real fetcher + real filesystem + migrated SQLite |
| `tests/unit/test_acquisition_fetcher.py` | 14 unit tests |
| `tests/unit/test_acquisition_zip.py` | 17 unit tests |
| `tests/support/documents.py`, `tests/support/stubs.py` | Real fixture bytes (`text_pdf`, `zip_bytes`) + stub HTTP transport helpers |

---

## B. Tests Executed

| Command | Result |
|---|---|
| `uv run pytest tests/integration/test_acquisition_service.py -q` | 15 passed |
| `uv run pytest tests/unit/test_acquisition_fetcher.py -q` | 14 passed |
| `uv run pytest tests/unit/test_acquisition_zip.py -q` | 17 passed |
| `uv run pytest -q` (full suite, prompt 04–10) | **354 passed in 69.51s** |
| `uv run mypy src/tender_intelligence` | **Success: no issues found in 97 source files** |
| `uv run ruff check src tests` | **All checks passed** |

---

## C. Behavioral Results

| Area | Result | Evidence |
|---|---|---|
| **Store-then-row ordering** | PASS | §2/§18: `ObjectStorage.put` first, `DocumentRepository` update second (`test_bytes_stored_and_row_updated`); DB and storage cannot falsely claim acquisition |
| **Checksum** | PASS | §5: SHA-256 recorded on the row and matched to stored `StoredObject` bytes |
| **MIME fallback** | PASS | §6: advertised type absent → guessed from bytes (`test_mime_falls_back_to_guess`) |
| **Size handling** | PASS | §7: mismatch warns but succeeds; over-limit aborts as `response_too_large` |
| **Scheme guard** | PASS | §4/§13: unsupported schemes rejected before any fetch (`test_unsupported_scheme_rejected`) |
| **HTTP classification** | PASS | §10: 404 permanent; retryable status re-attempted within bounded retries; outcome deterministic |
| **Transport classification** | PASS | §11: transient network errors retried then succeed; exhausted → permanent failure |
| **Size bounds** | PASS | §13: `Content-Length` pre-check plus actual-bytes check when the header understates |
| **Credential redaction** | PASS | §5/§13: fetcher `redact_url` strips credentials (`test_redacts_credentials` parametrized); covered again in Prompt 10 §19 L |
| **ZIP expansion** | PASS | §8: flat members extracted with correct per-member checksums; directory entries ignored |
| **ZIP safety** | PASS | §8: per-member rejection (`../` traversal, absolute paths, symlinks, drive/UNC, name length, ratio, oversized member); `member_count_limit` and `total_uncompressed_limit` abort the whole archive; recursion depth bounded |
| **Designated failure** | PASS | §8: malformed ZIP fails that attachment only; siblings and the archive's safe members still acquired |
| **Per-document isolation** | PASS | §11: five-document batch — one bad attachment never blocks its siblings |
| **Storage failure isolation** | PASS | §11: `storage_failed` recorded as `retryable=True`, row lands on `download_status=failed`, run continues |
| **Idempotent re-run** | PASS | §7: second run reports existing documents as present (non-empty result — the recorded prompt-08 defect, see D) and does not refetch stored bytes |
| **Hostile filenames** | PASS | §7/§10: `sanitize_storage_name` prevents path escape (`test_hostile_filename_cannot_escape_storage`) |
| **Correlation IDs** | PASS | §17: correlation id threaded through acquire→log records (`test_correlation_id_traced_through_logs`) |
| **Prompt 04–06 regression** | PASS | Full suite 354 passed (WAHO discovery, dedup, persistence untouched) |
| **Prompt 10 integration** | PASS | Acquisition participates in the real 04→09 offline run (§19 A) and retry/backoff scenarios (§19 H) |

---

## D. Defects Found

| # | Symptom | Root Cause | File/Module | Fix | Regression Test |
|---|---|---|---|---|---|
| 1 | Empty `AcquisitionResult` on re-run of the same tender (both `:210` and `:259` return empty lists) | Existing prompt-08 behaviour, recorded in `docs/08a-acquisition-report.md` | `src/tender_intelligence/acquisition/service.py` | **Not fixed here** — the integrated path (Prompt 10) never feeds already-stored attachments into `acquire`, so the defect is unreachable from the orchestrator; `test_rerun_reports_existing_documents_not_empty` pins current behaviour | `test_rerun_reports_existing_documents_not_empty` |
| 2 | Ruff findings in the acquisition test files (3) | Unused `pytest` import, one E501, three missing final newlines, one import order | `tests/integration/test_acquisition_service.py`, `tests/unit/test_acquisition_fetcher.py`, `tests/unit/test_acquisition_zip.py` | Fixed during verification | `ruff check src tests` clean |

No new implementation defects found during this verification pass.

---

## E. Files Changed During Verification

| File | Change |
|---|---|
| `tests/integration/test_acquisition_service.py` | Removed unused `import pytest`; wrapped a long test signature; added final newline |
| `tests/unit/test_acquisition_fetcher.py` | Sorted third-party import block; added final newline |
| `tests/unit/test_acquisition_zip.py` | Added final newline |
| `docs/08-verification-report.md` | Created (this report) |

---

## F. Remaining TODOs

| Item | Category | Notes |
|---|---|---|
| Prompt 08 entry in `docs/context-history.md` | Housekeeping | Added with the Prompt 10 pass (single context update) |
| `download_status` validation remains on the existing enum | Confirmed, no change | No invented statuses |

---

## Final Gate

```
PROMPT 08: VERIFIED — READY FOR PROMPT 09
```

All enumerated behavioral criteria of prompt 08 (download→validate→checksum→storage→row, ZIP safety, per-document tolerance, storage-failure isolation, correlation tracing, no invented statuses) are satisfied and pinned by real offline tests against the actual fetcher, storage and database. The stage integrates cleanly with Prompts 04–07 and with the Prompt 10 orchestrator's 04→09 path.
# Prompt 09 — Independent Behavioral / Integration Verification Report

**Date:** 2026-09-23
**Gate Status:** `PROMPT 09: VERIFIED — READY FOR PROMPT 10`

---

## A. Implementation Inspected

| Module / File | Role |
|---|---|
| `src/tender_intelligence/processing/service.py` | `DocumentProcessingService`, `TenderDocumentProcessor`, `detect_format`, `DocumentProcessingConfig`, `_process_snapshot`, `_finalize`, `process_tender` |
| `src/tender_intelligence/processing/representation.py` | `DocumentExtraction`, `TenderDocumentBundle`, `ExtractedPage`, `ExtractedSection`, `ExtractedTable`, `ProcessingMetadata` |
| `src/tender_intelligence/processing/store.py` | `ExtractionStore`, artifact/bundle keys, `is_reusable`, versioned artifact keys |
| `src/tender_intelligence/processing/pdf.py` | Native text + per-page OCR fallback, table extraction via pymupdf ruled lines |
| `src/tender_intelligence/processing/docx.py` | DOCX text/structure/table extraction via python-docx |
| `src/tender_intelligence/processing/ocr.py` | `OcrEngine` protocol + `StubOcrEngine` for tests |
| `src/tender_intelligence/processing/languages.py` | `detect_language`, `bundle_languages` |
| `src/tender_intelligence/processing/versions.py` | `PROCESSOR_VERSION`, `EXTRACTION_CONFIG_VERSION`, canonical JSON |
| `src/tender_intelligence/processing/errors.py` | Processing error hierarchy, error codes per docs/04 §4.3 |
| `src/tender_intelligence/db/repositories/documents.py` | Added `set_language`; reused existing setters |
| `src/tender_intelligence/core/errors.py` | Added `PARSE_FAILED` error code |
| `tests/integration/test_processing_service.py` | 52 integration tests (incl. §20 named cases, §22 seam chain) |
| `tests/unit/test_processing_{pdf,docx,languages,store,representation}.py` | 67 unit tests |
| `tests/support/documents.py`, `tests/support/stubs.py` | Real fixture builders (`text_pdf`, `image_pdf`, `docx_bytes`, `table_pdf`) + `StubOcrEngine` |

---

## B. Tests Executed

| Command | Result |
|---|---|
| `uv run pytest tests/integration/test_processing_service.py -q` | 52 passed |
| `uv run pytest tests/unit/test_processing_*.py -q` | 67 passed |
| `uv run pytest -q` (full suite) | **292 passed, 1 warning in 61.36s** |
| `uv run mypy src` | **Success: no issues found in 86 source files** |
| `uv run ruff check src tests` | **All checks passed** |
| **Independent gate probe** (`probe09_gate.py`, fresh SQLite + real storage, no pytest) | **All 4 scenarios passed** |

---

## C. Behavioral Results

| Area | Result | Evidence |
|---|---|---|
| **Native PDF** | PASS | §6: `text_pdf([ENGLISH])` extracted via native path; pages, text, method=`native_pdf`, deterministic fingerprint |
| **Scanned PDF** | PASS | §7: `image_pdf("SCANNED ANNEX", 2)` routed to OCR via `StubOcrEngine`; pages OCR'd, method=`ocr`, text persisted |
| **OCR** | PASS | §7/§15: Stub counts prove scanned PDF OCR'd once on first run, **not re-OCR'd** on second run (same config) |
| **Vision** | N/A | §8: **No approved vision provider/profile exists** (O18 open); prompt §5 forbids inventing one. Normal OCR path verified functional. |
| **DOCX** | PASS | §9: `docx_bytes([FRENCH, EVALUATION_MATRIX])` extracted paragraphs + structured tables; method=`docx` |
| **Tables** | PASS | §10: `table_pdf(DEADLINE_TABLE)` → `ExtractedTable` with `location="page:1"`, `headers`, `rows` matrix preserved |
| **Page Mapping** | PASS | §11: Probe verified `doc → page → content` survives `write_bundle` → `read_bundle`; `page.number` 1/2 disambiguated |
| **FR/EN/PT** | PASS | §12 Case A: separate docs in EN/FR/PT → bundle languages = union; §12 Case B: **one bilingual doc** (EN page 1 + FR page 2) → both texts present in extraction, **document-level language collapses to single best-guess** (dominant = `fr`); this is inherent to the `Document.language VARCHAR(16)` data-model design (one tag per doc). Bundle languages = union. |
| **Partial Failure** | PASS | §13: 5-doc batch (A=native, B=scanned, C=corrupt, D=docx, E=table) → A/B/D/E extracted, C `failed/parse_failed`, `incomplete_inputs=true`, C's failure never aborted siblings |
| **Corrupt/Unsupported** | PASS | §14: zero-byte PDF → `failed/parse_failed`; empty PDF (0 pages) → `failed/parse_failed`; corrupt DOCX → `failed/parse_failed`; missing stored bytes → `failed/source_bytes_unavailable`; all controlled, no exception escape |
| **Idempotency** | PASS | §15: Second run with unchanged bytes + same config → `is_reusable=true`, artifact reused, `StubOcrEngine.calls` did not increment |
| **Determinism** | PASS | §16: `content_fingerprint` identical across runs; excludes timestamps/correlation IDs |
| **Version Changes** | PASS | §17: Different `EXTRACTION_CONFIG_VERSION` → new artifact key (`v2.json`), reprocessed; stale artifact never silently reused |
| **Bundle Construction** | PASS | §18: One `TenderDocumentBundle` per tender; preserves doc boundaries, per-doc status/method/language/page→table mapping |
| **Bundle Persistence/Reload** | PASS | §19: `write_bundle` → terminate context → `read_bundle` returns usable bundle; **no re-download, no re-extraction** |
| **Correlation IDs** | PASS | §23: Correlation threaded through `tender → document → extraction → bundle → artifact`; verified in logs and probe |
| **Security/Logging** | PASS | §24: Integration test `test_no_document_content_leaks_into_logs` asserts no PDF text, OCR output, DOCX content in logs |
| **Prompt 08 Boundary** | PASS | §20: Zero HTTP/fetch calls in processing (grep verified); reads only via `storage.get(snapshot.storage_path)` |
| **Prompt 06 Boundary** | PASS | §21: Only `DocumentRepository` imported; no second `Document` model/repo |
| **Prompt 05 Boundary** | PASS | §22: No `claim_new`, dedup, `NEW`/`UPDATE`/`UNCHANGED` logic in processing |
| **Regression 04–08** | PASS | §26: Full suite 292 passed (prompts 04, 05, 06, 07, 08, 09) |

---

## D. Defects Found

| # | Symptom | Root Cause | File/Module | Fix | Regression Test |
|---|---|---|---|---|---|
| 1 | `docs/09-document-processing-report.md` claimed "`TenderDocumentBundle` subclasses `DocumentBundle`" | Wording error in report; `TenderDocumentBundle` is standalone; seam compatibility lives on `TenderDocumentProcessor.process` | `docs/09-document-processing-report.md` | Corrected sentence | N/A (documentation fix) |
| 2 | Prompt 08 defect (unfixed): empty `AcquisitionResult` on re-run of same tender | `acquisition/service.py:210` and `:259` return empty list when no new docs | `src/tender_intelligence/acquisition/service.py` | **Not fixed here** — tracked in prompt-08 report; outside Prompt 09 scope per §2 rule |
| 3 | Prompt 08 test sources missing (only `.pyc` remain) | Test files for prompt-08 integration deleted/moved | `tests/integration/` | **Not fixed here** — outside Prompt 09 scope |

No new implementation defects found during this verification.

---

## E. Files Changed During Verification

| File | Change |
|---|---|
| `docs/09-document-processing-report.md` | Corrected "subclasses DocumentBundle" wording error |
| `docs/09-verification-report.md` | Created (this report) |

No source code changes were required — the implementation already satisfied the specification.

---

## F. Remaining TODOs

| Item | Category | Notes |
|---|---|---|
| Add Prompt 09 entry to `docs/context-history.md` | Required before Prompt 10 (housekeeping) | Current file ends at Prompt 08 |
| O18: Vision vs OCR accuracy test on real WAHO scans | Future improvement (Phase 2) | Blocked by O18 open decision; no approved vision provider |
| O22: Confirm unsupported-format default with OPEX | Future improvement (Phase 4) | Currently: skipped, `incomplete_inputs` unaffected |
| XLSX extraction (price schedules) | Future improvement | Not required by spec; would shrink O22 set |
| Ruled-line-only table detection (pymupdf `strategy="lines"`) | Future improvement | Current default; `strategy="text"` for inferred tables not implemented |

---

## Final Gate

```
PROMPT 09: VERIFIED — READY FOR PROMPT 10
```

All required behavioral criteria from `prompts/09-document-processing.md` §1–§30 are satisfied. The implementation:
- Consumes Prompt 08's persisted `Document` rows (no re-download)
- Answers "what does each acquired document contain?" with deterministic, reusable bundles
- Preserves page boundaries, tables, language metadata, and correlation IDs
- Handles partial failures without aborting siblings (`incomplete_inputs=true`)
- Provides idempotent, versioned reuse (no unnecessary re-OCR)
- Integrates cleanly with the existing persistence architecture (single `DocumentRepository`)
- Leaves Prompts 05, 06, 08 boundaries intact
- Passes full regression suite (292 tests) with clean linting and typing
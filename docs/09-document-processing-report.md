# Prompt 09 — Document Processing / Understanding — Completion Report

Scope: answer *"what does each acquired document contain?"* for one tender, and persist the answer
as a reusable bundle. No triage, no verdict, no scoring (prompt 09 §17).

Prompt 08's stored `Document` rows are the input. Nothing is re-downloaded, re-discovered or
re-OCR-ed to satisfy a later stage (§2, §3, §13).

---

## Files changed

### Added — production

| File | Purpose |
|---|---|
| `src/tender_intelligence/processing/__init__.py` | Package surface. |
| `src/tender_intelligence/processing/errors.py` | `ProcessingError` hierarchy and the `SKIP_*` reasons. |
| `src/tender_intelligence/processing/representation.py` | `ExtractedTable`, `ExtractedPage`, `ExtractedSection`, `ProcessingMetadata`, `DocumentExtraction`, `TenderDocumentBundle`. |
| `src/tender_intelligence/processing/pdf.py` | Native-text + per-page OCR extraction, table detection. |
| `src/tender_intelligence/processing/docx.py` | Paragraph/table extraction from OOXML, no Office install (§6). |
| `src/tender_intelligence/processing/ocr.py` | `OcrEngine` abstraction and the Tesseract implementation. |
| `src/tender_intelligence/processing/languages.py` | Language detection over extracted text (§10). |
| `src/tender_intelligence/processing/versions.py` | Processor/config/schema versions, library versions, canonical JSON + content fingerprint. |
| `src/tender_intelligence/processing/store.py` | `ExtractionStore` — artifact and bundle read/write, reuse decision (§13, §14). |
| `src/tender_intelligence/processing/service.py` | `DocumentProcessingService`, `TenderDocumentProcessor`, `detect_format`. |

### Added — tests

| File | Tests |
|---|---|
| `tests/unit/test_processing_pdf.py` | 20 |
| `tests/unit/test_processing_store.py` | 13 |
| `tests/unit/test_processing_docx.py` | 12 |
| `tests/unit/test_processing_representation.py` | 12 |
| `tests/unit/test_processing_languages.py` | 10 |
| `tests/integration/test_processing_service.py` | 52 |
| `tests/support/__init__.py`, `tests/support/documents.py`, `tests/support/stubs.py` | Fixture builders (real PDF/DOCX/ZIP bytes) and the deterministic OCR stub. |

### Modified

| File | Change |
|---|---|
| `src/tender_intelligence/db/repositories/documents.py` | Added `set_language` only. `set_extraction_status`, `set_checksum`, `set_storage_path` and `set_extracted_text_ref` were already present and are used unchanged. |
| `src/tender_intelligence/core/errors.py` | Added `PARSE_FAILED` (§16 needs to distinguish a document whose bytes are not its declared format from an OCR failure). docs/04 §4.3's catalogue is non-exhaustive, so this extends it rather than replacing `OCR_FAILED`. |
| `docs/13-open-decisions.md` | Added **O22** (unsupported-format skips vs `incomplete_inputs`) and updated both summary tables. |
| `pyproject.toml`, `uv.lock` | Added `pymupdf`, `python-docx`, `pillow`, `pytesseract`. |

No new `Document` model, no second repository, no acquisition responsibility moved here.

---

## Support matrix

| Capability         | Supported | Method | Verified |
| ------------------ | --------- | ------ | -------- |
| Native PDF         | Yes | `pymupdf` text layer; per-page `MIN_NATIVE_TEXT_CHARS_PER_PAGE = 25` threshold | Yes — real PDFs, `tests/unit/test_processing_pdf.py` + integration |
| Scanned PDF        | Yes | Rendered at 200 DPI and routed to the OCR engine per page | Yes — path only; the OCR engine itself is stubbed (see § Limitations) |
| OCR                | Yes | `pytesseract` behind `OcrEngine`; availability probed, never assumed | Path yes, engine **no** — no Tesseract binary in this environment |
| Vision             | No | Not implemented — no approved provider/profile exists | N/A — recorded, not fabricated (§5) |
| DOCX               | Yes | `python-docx`; paragraphs + tables as ordered sections, no Office install | Yes — real OOXML packages |
| Tables             | Yes | `find_tables(strategy="lines")` per page; DOCX tables via section kind | Yes — 3 tender table shapes round-trip exactly |
| FR                 | Yes | `languages.py` over extracted text | Yes |
| EN                 | Yes | `languages.py` over extracted text | Yes |
| PT                 | Yes | `languages.py` over extracted text | Yes |
| Page boundaries    | Yes | `ExtractedPage.number`, `location = "page:N"`; scanners reject zero-page PDFs | Yes — including a mixed document where only the scanned page is OCR'd |
| Bundle persistence | Yes | Canonical JSON at `tenders/{id}/extracted/bundle.json`; per-document artifacts under `.../extracted/{checksum}/v{config}.json` | Yes — reloaded through a fresh `ExtractionStore` |
| Idempotent reuse   | Yes | `ExtractionStore.is_reusable` on processor + config version; content fingerprint for change detection | Yes — proven by a counting OCR stub, not asserted |

---

## Scanned-PDF decision

**Selected default:** local OCR via Tesseract (`pytesseract`), per page, rendered at 200 DPI.

**Alternatives tested:** none. No approved vision provider/profile exists in this environment, so no
vision path could be exercised. Prompt 09 §5 is explicit for this case — *"If no approved vision
provider/profile is available, do not invent one. The implementation must remain functional using
the approved non-vision extraction path."* Vision fields therefore exist on `ProcessingMetadata`
(`vision_provider`, `vision_model`, `vision_profile`) and are recorded as `None` rather than filled
with a stand-in.

**Evidence:** the *selection* is not evidenced by a comparison, because none was possible. What is
evidenced is that the non-vision path works and is correctly wired:

- A scanned PDF is OCR'd and the document is recorded with `extraction_method = "ocr"`, not
  `"native_pdf"`.
- With no engine available, the document is recorded `failed` / `ocr_failed`, and the bundle sets
  `incomplete_inputs = true`. It is **not** silently recorded as empty or as extracted.
- An engine that runs but faults produces a recorded per-document failure and does not abort the
  tender.
- In a mixed document, only the scanned pages are sent to the engine: the native pages keep
  `method = "native_pdf"` and the OCR call count is asserted. A document-level choice would have
  been wrong in both directions.
- Reuse is real: a second processing run leaves the OCR call count at 1; a processor-version or
  config-version change raises it to 2.

**Limitations:** no Tesseract binary is installed (confirmed: `TesseractNotFoundError`), so every
OCR assertion runs against a deterministic stub (`tests/support/stubs.py`) whose output is fixed.
The *path* is verified end-to-end — the engine seam, the per-page routing, the status and metadata
written to the row and the artifact, the reuse decision. **OCR accuracy on a real scan is not
verified.** That question belongs to docs/13 **O18** (vision vs dedicated OCR, status OPEN), which
remains open and which this report feeds rather than resolves.

**Provider/model/profile:** not applicable — no vision provider is configured or approved.

**Why this default is appropriate for the tested workload:** the tested workload is native-text
PDFs, DOCX and ruled-line tables, all of which are handled without OCR at all. OCR is the fallback
for scanned WAHO annexes, and it is wired so that its absence produces a visibly partial bundle
rather than a plausible-looking empty one. That is the property that matters until O18 is decided
on real scanned documents.

---

## Bundle schema

How the implementation maps onto the existing data model (docs/03 §3.2). **No new entity and no new
column.** `Document` already carries every field prompt 09 needs; prompt 09 writes to them.

```text
Document (existing entity — docs/03 §3.2)
├── download_status          set by prompt 08
├── extraction_status        ← prompt 09: pending | extracted | failed | skipped
├── language                 ← prompt 09: detected code, or NULL when undetectable
├── checksum                 set by prompt 08; names the artifact key
├── storage_path             set by prompt 08; the ONLY way bytes are read
└── extracted_text_ref       ← prompt 09: key of this document's extraction artifact

Object storage (not the database — the archive prompt 08 already writes into)
├── tenders/{id}/attachments/{checksum}/{name}      prompt 08, unchanged
└── tenders/{id}/extracted/
    ├── {checksum or doc-{id}}/v{config_version}.json    ← DocumentExtraction, canonical JSON
    └── bundle.json                                      ← TenderDocumentBundle
```

`DocumentExtraction` is the per-document representation (§11): identity and provenance carried
forward unchanged (`document_id`, `tender_id`, `filename`, `source_url`, `checksum`, `storage_path`),
the outcome (`extraction_status`, `extraction_method`, `error_code`), the content (`text`, `pages`,
`sections`), the language guess with its confidence and source, and a `ProcessingMetadata` block
recording processor version, config version, library versions, OCR engine and version, vision
fields, timestamp and correlation id.

Two deliberate schema choices:

- **Tables live under their page or section, never at the top level.** A flat `tables` key alongside
  `pages` would duplicate every table in storage; a test asserts the flat key is absent.
- **`content_fingerprint` excludes run metadata.** It is computed over canonical JSON of the content
  only, so two runs that find the same content agree, while a changed document differs. This is what
  `bundle_fingerprint` aggregates to detect that a tender's inputs moved.

`TenderDocumentBundle` is the tender-level artifact (§12): every document including the ones that
produced nothing, `incomplete_inputs`, the language set, and bundle metadata. It is deliberately
standalone (not a `DocumentBundle` subclass) with the interface compatibility carried by
`TenderDocumentProcessor.process`, which returns the `interfaces.document.DocumentBundle` shape so a
later stage can consume the storage-driven seam without depending on prompt 09's internals.

---

## Tests

```text
uv run pytest
    → 292 passed, 1 warning in 56.21s

uv run pytest tests/unit/test_processing_representation.py tests/unit/test_processing_languages.py \
    tests/unit/test_processing_pdf.py tests/unit/test_processing_docx.py \
    tests/unit/test_processing_store.py tests/integration/test_processing_service.py
    → 119 passed in 29.34s

uv run ruff check .
    → All checks passed!

uv run mypy
    → Success: no issues found in 86 source files
```

The 1 warning is a pre-existing `starlette`/`anyio` deprecation, unrelated to prompt 09.

**Fixtures are generated, not checked in.** `tests/support/documents.py` builds genuine PDFs with
real text layers, genuine OOXML packages and genuine ZIPs in-process, so a fixture cannot silently
drift from the format the extractor claims to handle.

**Behavioral verification (§22), in order.** The integration suite runs the real service against a
migrated SQLite schema, the real `LocalFileSystemStorage` and real PDF/DOCX/ZIP bytes. Nothing about
the database, the storage keys, the extraction paths or the status transitions is mocked; only the
OCR engine is stubbed, and only because no Tesseract binary exists.

```text
stored Document          test_bundle_is_built_from_stored_documents
      ↓
storage read             test_missing_stored_bytes_are_an_explicit_failure_never_a_re_download
      ↓
processor                test_outcomes_are_persisted_on_the_document_rows
      ↓
extraction metadata      test_processing_metadata_records_versions_for_reproducibility
      ↓
persisted artifact       test_extracted_text_ref_points_at_a_readable_artifact
      ↓
tender bundle            test_bundle_is_persisted_at_a_key_derived_from_the_tender_id
      ↓
bundle reload            test_the_persisted_bundle_reloads_without_reprocessing
```

`test_bundle_is_consumable_without_re_reading_the_source_documents` closes the loop: the persisted
bundle is read from storage alone, with no access to the source URLs, and carries the content a
later stage needs.

**§20's named cases, each present:**

| §20 requirement | Test |
|---|---|
| Native PDF | `test_bundle_is_built_from_stored_documents` |
| Scanned PDF / OCR | `test_scanned_pdf_is_ocrd_and_recorded_as_such`, `…_without_an_ocr_engine_fails_with_ocr_failed` |
| DOCX | `test_a_docx_table_keeps_its_rows_and_columns` |
| Tables | `test_the_three_tender_table_shapes_survive_into_the_bundle` (deadline, eligibility, evaluation matrix) |
| Multilingual | `test_multi_language_tender_reports_its_languages` (EN/FR/PT) |
| Reprocessing / idempotency | `test_rerun_reuses_extractions_without_reprocessing`, `…_does_not_rewrite_the_artifact`, `test_processing_is_deterministic_across_runs` |
| Partial failure, the spec's exact five-document batch | `test_the_five_document_mixed_batch_from_the_spec` → `extracted, extracted, failed, extracted, extracted`, `incomplete_inputs = true` |
| Correlation | `test_correlation_id_propagates_into_the_bundle_and_its_artifacts` |
| §18 log safety | `test_logs_never_contain_document_content` |

**§18 was tested adversarially.** A real secret string was planted in both a successfully-extracted
PDF and a corrupt PDF, and the captured log records are asserted to contain neither the secret, nor
the corrupt bytes, nor the OCR stub's text. Errors identify the document and correlation id only.

**The `failed` / `skipped` distinction is tested as behaviour, not convention.** `failed` means the
document should have yielded content and did not — it sets `incomplete_inputs`. `skipped` means it
was never a content source — it does not. The one genuinely ambiguous case (a format the pipeline
does not extract) is asserted explicitly against the documented default, with a comment naming
**O22**.

---

## Regressions

Prompt 04–08: **no regressions.** The full suite is 292 passed, of which 173 are prompts 04–08 and
infrastructure, and all of them pass unchanged. Prompt 09 required no edit to prompt 08's code —
`DocumentRepository` already had every setter prompt 09 needed except `set_language`.

One caveat on what "prompt 08 passes" can mean here: **prompt 08's own test sources are absent from
the working tree** (see 2 below), so that stage's coverage cannot currently be run at all. The
no-regression claim covers the suite as it exists, plus the integration test added here, which
exercises prompt 08's stored rows as its input.

Two things about prompt 08 found while working, reported rather than fixed (out of scope, and
prompt 09 was instructed not to redesign it):

1. **`src/tender_intelligence/acquisition/service.py:210` and `:259` both `return AcquisitionResult()`**
   with no documents. A re-run of an already-acquired tender therefore reports zero documents and
   `incomplete_inputs = False`. Prompt 09 does not depend on this (it reads `Document` rows, which
   are present either way), but any caller that trusts the return value sees an empty acquisition
   where documents exist.

2. **Prompt 08's test sources are missing from the working tree.** Three orphaned bytecode files
   remain with no `.py` beside them:
   `tests/unit/__pycache__/test_acquisition_zip.cpython-314-pytest-9.1.1.pyc`,
   `tests/unit/__pycache__/test_acquisition_fetcher.cpython-314-pytest-9.1.1.pyc`,
   `tests/integration/__pycache__/test_acquisition_service.cpython-314-pytest-9.1.1.pyc`.
   The `.pyc` files prove those tests existed and ran. **As the tree stands, prompt 08's acquisition
   service has no test coverage**, and the "Prompt 08 tests pass" line above refers to the tests
   that are present. `src/tender_intelligence/acquisition/` itself is present and unmodified.

---

## TODOs

Unresolved, not hidden:

1. **Real OCR accuracy is unverified.** No Tesseract binary in this environment, so the scanned-PDF
   path is verified against a stub. Installing Tesseract and re-running
   `test_scanned_pdf_is_ocrd_and_recorded_as_such` against a real scan is the outstanding step.
2. **docs/13 O18 remains OPEN.** The vision-vs-OCR default cannot be decided here — no approved
   vision provider exists and §5 forbids inventing one. The metadata fields are in place for
   whichever way it is decided.
3. **docs/13 O22 is a documented default, not a confirmed decision.** Unsupported formats are
   `skipped` and do **not** set `incomplete_inputs`. If OPEX would call a spreadsheet annex a
   material gap, the email footer currently understates how partial the inputs were.
4. **XLSX is not extracted.** A price schedule is a plausible attachment on these sources; today it
   is skipped. Covered by O22 option (d).
5. **Prompt 08's two defects above** — the empty `AcquisitionResult` on re-run, and the missing test
   sources — are unfixed and belong to prompt 08.
6. **Table extraction is ruled-line only.** `find_tables(strategy="lines")` needs visible borders. A
   whitespace-aligned table in a real tender would not be detected. Untested against real documents.
7. **Language detection is heuristic.** It ASCII-folds and has a token floor, so a short or
   table-only document can yield no language rather than a wrong one. Not yet measured against real
   multilingual WAHO documents.
8. **docs/06 §6.1 ("record size + best-guess language") conflicts with docs/03 §3.2** (no size
   column). Prompt 09 recorded the language and left size alone, per the data model. Flagged, not
   resolved.

---

## Final status

```text
PROMPT 09: VERIFIED — READY FOR PROMPT 10
```

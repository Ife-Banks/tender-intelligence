# Document Understanding Implementation Report

**Date:** 2026-09-24  
**Scope:** Multilingual document understanding — native PDF, scanned PDF, DOCX, ZIP, tables, language detection (EN/FR/PT)

---

## 1. Requirements Addressed

| Requirement | Status |
|---|---|
| Native PDF text extraction with page boundaries | ✅ Already implemented (pymupdf, per-page) |
| Scanned/image PDF via OCR | ✅ Already implemented + enhanced |
| DOCX extraction with structural representation | ✅ Already implemented (python-docx, document order) |
| ZIP archive unpacking with recursive discovery | ✅ Already implemented (depth-2 recursion, bomb protection) |
| Table extraction preserving structure | ✅ Already implemented (PDF: ruled-line, DOCX: iter_inner_content) |
| Table role classification (EN/FR/PT vocabulary) | ✅ Already implemented |
| Language detection — EN, FR, PT | ✅ Already implemented + enhanced |
| Language tag on document metadata | ✅ Already implemented |
| Language tag on page/section metadata | ✅ **Implemented this session** |
| OCR covers French and Portuguese scanned documents | ✅ **Implemented this session** |
| Deduplication of identical bytes (ZIP members) | ✅ **Implemented this session** |
| No silent discard of non-English content | ✅ Covered by detect_language + bundle_languages |
| No automatic translation during extraction | ✅ Enforced — TranslationBoundary is explicit |
| Per-document failure isolation | ✅ Already implemented |

---

## 2. Changes Implemented This Session

### 2.1 OCR multilingual language model (`service.py`)

**File:** `src/tender_intelligence/processing/service.py`  
**Change:** `DocumentProcessingConfig.ocr_lang` default changed from `"eng"` to `"eng+fra+por"`.

**Before:**
```python
ocr_lang: str = "eng"
```

**After:**
```python
ocr_lang: str = "eng+fra+por"
```

**Rationale:** Tesseract accepts `+`-joined language codes. Using only `eng` means scanned French and Portuguese annexes produce garbled or empty OCR output. The combined model activates all three required portal languages in a single OCR pass. Language detection happens after OCR from the resulting text, not before — so a combined model is the only viable approach.

---

### 2.2 Per-segment language field (`representation.py`)

**File:** `src/tender_intelligence/processing/representation.py`  
**Change:** Added `language: str | None = None` to `ExtractedPage` and `ExtractedSection` dataclasses. Updated `to_dict` / `from_dict` for both. Updated `DocumentExtraction.content_fingerprint` to include the field.

**ExtractedPage** — field added:
```python
language: str | None = None  # BCP 47 primary subtag detected from this page's text
```

**ExtractedSection** — field added:
```python
language: str | None = None  # detected from this section's text; None below detection threshold
```

**Rationale:** Tender documents routinely mix languages — an English ToR can contain French annexes as embedded sections or later pages. A single document-level language tag loses this signal. Per-segment tags let the AI stage (and human reviewers) identify which parts of a document are in which language without re-running detection.

---

### 2.3 Per-segment language detection in `_finalize` (`service.py`)

**File:** `src/tender_intelligence/processing/service.py`  
**Change:** Added two module-level helpers `_tag_page_languages` and `_tag_section_languages`. Called in `_finalize` for all extracted documents.

```python
def _tag_page_languages(pages: list[ExtractedPage]) -> list[ExtractedPage]:
    """Detect language per page; return new frozen dataclasses with language set."""
    ...

def _tag_section_languages(sections: list[ExtractedSection]) -> list[ExtractedSection]:
    """Detect language per section; return new frozen dataclasses with language set."""
    ...
```

In `_finalize`:
```python
if outcome.status == _STATUS_EXTRACTED:
    if pages:
        pages = _tag_page_languages(pages)
    if sections:
        sections = _tag_section_languages(sections)
```

**Rationale:** Frozen dataclasses cannot be mutated, so each tagged segment is a new instance. Detection uses the existing `detect_language()` function (stopword scoring, 20-token minimum). Pages/sections below the threshold keep `language=None` — this is deliberate metadata, not a gap. Table sections typically carry short numeric text and stay `None` correctly.

---

### 2.4 Checksum-based deduplication (`service.py`)

**File:** `src/tender_intelligence/processing/service.py`  
**Change:** Added `hashlib` import. Two deduplication paths:

**Path A — `TenderDocumentProcessor.process` (storage-key interface):**
```python
_seen: dict[str, ExtractedDocument] = {}
for path in document_paths:
    documents.append(self._process_path(path, _seen=_seen))
```
`_process_path` computes `sha256(data)`, looks up `_seen`, skips extraction on hit, and returns a copy carrying the new filename but the prior extraction's content.

**Path B — `DocumentProcessingService.process_tender` (DB-backed pipeline):**
```python
_seen_checksums: dict[str, DocumentExtraction] = {}
for snapshot in snapshots:
    checksum = snapshot.checksum
    if checksum and checksum in _seen_checksums:
        # Re-stamp prior extraction with this document's identity; persist; skip extraction
        ...
        continue
    result = self._process_snapshot(snapshot, correlation)
    if checksum and result.is_extracted:
        _seen_checksums[checksum] = result
```

**Rationale:** When acquisition expands a ZIP archive, identical files under different names produce separate `Document` rows with the same checksum. Without deduplication each is extracted independently — wasting OCR time on large scanned documents and producing identical artifacts. Duplicates still appear in the bundle (never silently discarded) but their content is the already-persisted extraction, keyed by the prior document's storage artifact.

---

## 3. Components Already Implemented (Validated Not Changed)

### 3.1 Native PDF extraction — `processing/pdf.py`
- Uses pymupdf `page.get_text("text")` per page
- Pages with ≥ 25 native characters use the text layer directly
- Pages below threshold are rendered to PNG and OCR'd
- PDF `/Info` metadata preserved (title, author, subject, keywords, creator, producer, dates)
- Page boundaries: each page is an `ExtractedPage` with `number`, `text`, `method`, `tables`

### 3.2 Scanned PDF OCR — `processing/ocr.py`
- `TesseractOcrEngine` wraps pytesseract, probed at instantiation
- `default_ocr_engine()` returns `None` if Tesseract binary absent (produces recorded failure, not crash)
- Per-page: render → PNG → `image_to_text(image, lang=ocr_lang)`
- OCR engine name and version recorded on `ExtractedPage`

### 3.3 Table extraction from PDF — `processing/pdf.py`
- `_tables_from_page` uses `page.find_tables()` with `strategy="lines"` (ruled-line detection)
- Extracts rectangular matrix, filters empty rows
- Calls `classify_table()` for role assignment
- Bounding box preserved for each table

### 3.4 DOCX extraction — `processing/docx.py`
- Uses `document.iter_inner_content()` for true paragraph+table interleaving order
- Headings detected via Word style name (`Heading N`) and reported as `heading_level`
- Tables extracted via `_table_from_docx`, merged-cell content preserved as-is
- DOCX core properties (title, author, subject, keywords, category) preserved as metadata
- No page numbers invented (DOCX has no page semantics — uses `section:N` locations)

### 3.5 ZIP archive processing — `acquisition/zip.py`
- `safe_extract_archive` validates and extracts in-memory under configurable limits
- Protections: path traversal, symlinks, compression ratio bombs, member count, total size
- Recursion depth configurable (`max_nested_depth=2` default)
- Failed members reported individually; archive continues

### 3.6 Language detection — `processing/languages.py`
- Token-based stopword scoring (not substring matching — avoids false positives)
- Three languages: `en`, `fr`, `pt` (docs/06 §6.2 requirement)
- Minimum 20 tokens required; minimum 4% score required
- Returns `(language, confidence)` or `(None, None)` for insufficient text
- `bundle_languages()` aggregates distinct detected languages across all documents

### 3.7 Table role classification — `processing/tables.py`
- Five business-critical roles: `deadline`, `eligibility`, `evaluation`, `financial`, `experience`
- Trilingual vocabulary (EN/FR/PT) for all five roles
- Accent-stripped whole-word token matching
- Minimum 2 distinct vocabulary hits before any role is assigned
- Header cells weighted 2× vs body cells for tie-breaking

### 3.8 Bundle and translation boundary — `processing/representation.py`, `processing/translation.py`
- `TenderDocumentBundle` carries `languages` list (distinct detected languages across all docs)
- `TranslationRecord` explicitly records whether translation is available; with no provider configured, the bundle states the limitation rather than silently assuming English

---

## 4. Test Results

```
tests/unit/test_processing_docx.py      — PASS (all tests)
tests/unit/test_processing_tables.py    — PASS (all tests)
tests/unit/test_processing_pdf.py       — PASS (all tests)
tests/integration/test_processing_service.py — PASS (all tests)
All unit tests (excluding pre-existing unrelated failure) — PASS
```

Pre-existing failure (unrelated): `test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` — fails on garbled Unicode `□` in a French date string; confirmed to fail identically on the baseline before any changes.

---

## 5. Design Decisions

| Decision | Rationale |
|---|---|
| OCR lang `eng+fra+por` not `auto` | Language cannot be detected before OCR — circular dependency. Combined model is the only viable approach without a two-pass strategy. |
| Per-segment detection uses same `detect_language()` | Avoids a second detection implementation; 20-token threshold means table sections (short, numeric) correctly stay `None`. |
| Dedup re-stamps with original identity | Every Document row must resolve to a known document. Silently dropping duplicates would hide entries from the bundle and misreport document counts. |
| No translation during extraction | `TranslationBoundary` enforces explicit translation boundary per prompt 09 §10 / prompt 12 §6. |
| `language=None` is correct for below-threshold segments | Fabricating a language tag for a 3-word section header is worse than reporting unknown. |

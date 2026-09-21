# 06 — Document Processing Specification

> Source of truth: v1.1 §5.3, §5.4, §5.6. Covers document acquisition (fetcher) and document understanding (extraction). Vendor/framework names are **proposed choices** unless the source names them; no specific OCR vendor is mandated by the source.

## 6.1 Acquisition (Document Fetcher)

Confirmed from v1.1 §5.3:

- Download **every** linked document on a tender's detail page: RFPs, TORs, annexes, EOI forms, procurement plans, **ZIP archives (unzip and recurse)**.
- Preserve original filenames and source URLs; store a local/cloud copy so the tender never depends on the source URL staying valid.
- Record: `mime_type`, size, best-guess `language`, `checksum`.
- Per-document failure handling: one broken link must not block the other documents. Flag missing documents in the verdict/email instead of failing silently.
- `Projected storage` *[proposed]*: object storage or a well-organised filesystem/bucket; raw files referenced from a `Document.storage_path`.

## 6.2 Understanding (Document Understanding)

Confirmed from v1.1 §5.4 — the "read and understand" requirement must work across:

### Native PDFs
- Direct text extraction.
- *[Proposed]*: established PDF text-extraction libraries; verify on real WAHO PDFs.

### Scanned / image PDFs
- OCR is **required** (WAHO TORs and AMI notices are frequently scanned or mixed).
- **Vision option [NEW in v1.1]:** if the assigned LLM profile has `supports_vision = true`, page images may optionally be sent to the model instead of, or as a fallback to, a separate OCR engine. **Test both on real scanned WAHO documents before choosing a default.**
- *[No OCR vendor is mandated by the source.]*

### DOCX
- Direct text extraction (WAHO explicitly publishes `.docx` TORs).
- *[Proposed]*: a DOCX parser library.

### ZIP
- Unzip and recurse; treat inner files as additional documents with their own download/extraction status.

### Tables
- Deadlines, eligibility criteria, and evaluation matrices frequently live in tables; a naive text dump loses structure that matters for the verdict. Extraction must preserve structure.
- *[Proposed]*: table-aware extraction (e.g. per-page tables as structured rows), then serialised into the document bundle.

### Multilingual documents
- WAHO publishes French, English and Portuguese within a single notice.
- Extraction must **preserve/tag language** on each document (or segment) so the AI stage can work in the right language or translate to a working language before assessment.
- Output `Document.language` best-guess + per-bundle language tags.

### Page boundaries
- Keep page boundaries available so the vision/OCR fallback and the verdict step can map content back to pages. *[Confirmed as useful for the vision path; page mapping is a proposed mechanism.]*

### Metadata
- Per document: filename (original), source URL, mime type, size, language (best guess), checksum, download and extraction status.
- Bundle-level: which documents present, which failed, extraction method used (native/OCR/vision/DOCX).

### Checksums
- Recorded per document; used to (a) avoid refetching/re-sending duplicates and (b) detect changed attachments on update detection.

## 6.3 Output: the document bundle

- One structured, **reusable** "document bundle" per tender: plain text + metadata per document, ready to hand to the AI stage.
- **Reusable:** do not re-OCR the same PDF twice if the AI stage is re-run.
- *[Proposed]*: bundle persisted/referenced from `Document.extracted_text_ref`, with an extraction-method and status per document.

## 6.4 Partial failures

- **Attachment-level errors:** per-document `download_status` / `extraction_status`; a failing attachment is a warning, not a pipeline failure.
- The verdict records `incomplete_inputs = true` and the email footer lists the failed documents.
- **Oversized document bundles:** if tender bundle + KB exceeds the profile's context window, summarise per document (map), then run the verdict on summaries (reduce); record that this happened.

## 6.5 Incomplete-input handling

- Any attachment that failed to download or extract ⇒ `incomplete_inputs = true` (v1.1 §5.6), flagged to humans in the email.
- Blocked/errored documents never block other documents.
- A tender whose docs are all missing still gets triaged and (if it passes) verdicted on the notice text with `incomplete_inputs = true`.

## 6.6 Proposed library stack (v1.1 §9 guidance, not mandate)

| Need | Proposal | Status |
|---|---|---|
| HTTP | `requests`/`httpx` | Proposed |
| HTML parse | BeautifulSoup; Playwright only for JS/form sources | Proposed |
| Native PDF text | PDF text library | Proposed |
| Scanned PDF OCR | OCR engine (vendor undefined); vision-capable model option | Proposed/conditional |
| DOCX | DOCX parser | Proposed |
| Storage | object storage or filesystem/bucket | Proposed |

## 6.7 Confirmed constraints vs proposed choices

- **Confirmed:** every attachment captured; ZIP unzip+recurse; original names/URLs preserved; checksums; per-file type/size/language; per-document failure tolerance; native+OCR+DOCX+multi-language+tables; reusable bundle; language tagging.
- **Proposed/conditional:** specific libraries, OCR vendor, storage backend, vision-default decision (must be decided by testing), page-boundary mechanism.
- **Historical (v1.0):** identical to v1.1 for this stage except the vision option (new in v1.1) and explicit per-document status fields (new in v1.1).
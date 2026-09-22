# Prompt 07 — Document Discovery

> Paste `prompts/00-master-context.md` first.

## Read

* `PROJECT_RULES.md`
* `docs/05-source-adapter-spec.md` (§5.1–5.3: detail-page and attachment discovery)
* `docs/06-document-processing-spec.md` (§6.1: acquisition gate)
* `docs/04-pipeline-spec.md` (§4.2: stage 4)
* `prompts/01-architecture.md` (interface / DTO shapes)
* `prompts/06-tender-persistence.md` and the actual Prompt 06 implementation, especially the `Document` entity/repository/status model

## Task

Implement the WAHO adapter's:

* `get_detail(identifier)`
* `get_attachments(identifier)`

These stages answer:

> **What is the tender detail page, and what documents/links does the source page expose?**

They do **not** download document bytes, extract document contents, perform OCR, process ZIP contents, or invoke AI.

The implementation must remain source-neutral at the pipeline boundary. WAHO-specific parsing belongs inside the WAHO adapter.

---

## 1. `get_detail(identifier)`

Implement `get_detail(identifier)` so it retrieves and parses the tender's detail page into the neutral `TenderDetail` DTO defined by the project architecture.

The returned detail must contain, where present on the source page:

* full title
* description/body
* reference number(s)
* deadline date
* deadline time
* original deadline timezone string
* normalized UTC deadline representation required by the DTO/spec
* applicant/stakeholder information
* other fields explicitly defined by the `TenderDetail` contract
* raw HTML, if the DTO/spec requires it for downstream processing/audit

### Timezone rule

Never infer a timezone from the server, browser, machine, or crawler environment.

If WAHO exposes an explicit timezone, preserve that original timezone representation and normalize the deadline to UTC according to the project's documented rules.

If the source does not expose enough information to establish the timezone:

* do not silently invent one;
* preserve the available original values;
* represent the missing/unknown state according to the existing DTO contract;
* do not create a naive local timestamp.

Do not invent new deadline fields merely to accommodate parsing.

---

## 2. `get_attachments(identifier)`

Implement `get_attachments(identifier)` so that it discovers **all document/file links exposed by the tender detail page** and returns them as the neutral `TenderAttachment` DTO.

For each discovered attachment, capture only information supported by the source page or existing project contracts, such as:

* original filename, when available
* source/download URL
* MIME type or MIME guess, when determinable without downloading
* advertised size, when available
* whether the link is a top-level attachment
* whether the link is a ZIP/bundle
* stable identity/checksum target information required by the downstream acquisition stage

Do not download the attachment merely to populate metadata unless `docs/06-document-processing-spec.md §6.1` explicitly requires a fetch to establish a field.

Do not claim a final checksum before bytes have actually been acquired.

If the source provides no checksum, represent that as unknown/not-yet-available rather than inventing one.

---

## 3. ZIP awareness

Identify links that are ZIP/bundle candidates using evidence available from the detail page, such as:

* `.zip` filename extension
* source-provided MIME type
* other explicit source metadata

A ZIP discovered here is still **one top-level attachment**.

Do not:

* download the ZIP;
* inspect its contents;
* enumerate inner files;
* calculate hashes of inner files;
* create fake `TenderAttachment` rows for files that have not yet been observed.

Prompt 08 owns downloading the ZIP.

Prompt 09 owns extraction/processing and any recursive inner-document discovery required after acquisition.

If the source page explicitly exposes information about files contained inside a ZIP, preserve that information only if the existing DTO/model has a defined place for it. Otherwise report it as source evidence/TODO rather than inventing a new model field.

---

## 4. Complete enumeration

`get_attachments()` must surface **all attachment/document links that the source page exposes**.

Do not silently:

* keep only PDFs;
* discard DOC/DOCX/XLS/XLSX/PPT/PPTX/ZIP or other supported formats;
* discard unfamiliar extensions;
* deduplicate links unless the existing contract explicitly requires deduplication.

If the same source URL appears more than once on the page, follow the existing DTO/repository contract rather than inventing a new deduplication rule.

Document discovery is not document deduplication.

---

## 5. Source boundary

The WAHO adapter may contain WAHO-specific:

* selectors
* URL handling
* HTML parsing
* language-specific labels
* filename extraction
* source quirks

But nothing WAHO-specific may leak into:

* `TenderDetail`
* `TenderAttachment`
* pipeline orchestration
* repository interfaces
* downstream document-processing code

The pipeline should consume neutral DTOs.

Do not create WAHO-specific DTOs where the existing architecture already defines a neutral contract.

---

## 6. Configuration and safeguards

Use the same adapter safeguards established in Prompt 04:

* configuration-driven URLs/selectors where the architecture requires them;
* polite request behavior;
* existing rate-limit configuration;
* correlation IDs;
* structured adapter errors;
* no secret values in logs;
* dry-run support where applicable;
* no database persistence merely as a side effect of adapter discovery.

Use the existing structured error conventions, including:

* `source_unreachable`
* `parser_mismatch`

Do not invent a second incompatible error taxonomy.

A missing or malformed detail page must produce a structured failure rather than an unhandled exception.

---

## 7. Persistence boundary

Prompt 07 is responsible for **discovery**, not ownership of tender persistence.

Do not create a second persistence mechanism for documents.

Use the repository/entity seams created by Prompt 06 only where the pipeline contract explicitly requires document discovery metadata to be persisted.

In particular:

* do not create a second `Document` model;
* do not create a second document repository;
* do not bypass the Prompt 06 repository interface with direct ORM/database calls;
* do not invent document statuses;
* do not mark a document as downloaded, processed, extracted, or evaluated during discovery.

If the current Prompt 06 `Document` contract requires a document row to exist when a link is discovered, use that existing repository seam and populate only fields legitimately known at discovery time.

If the current contract instead creates the row during acquisition, preserve that boundary.

Do not guess. Follow the actual implemented Prompt 06 contract and the authoritative project specification.

---

## 8. Offline fixtures

Add deterministic fixtures for at least:

### Detail pages

* English detail page
* French detail page
* Portuguese detail page
* complete deadline
* deadline with explicit timezone
* missing deadline component
* missing optional metadata
* malformed/unexpected detail structure
* 404/missing detail

### Attachment pages

Include a page exposing:

* PDF
* DOC/DOCX
* spreadsheet if supported by the source
* ZIP
* unfamiliar/unsupported extension
* multiple attachments
* duplicate-looking filenames with different URLs
* attachment with no advertised size
* attachment with no obvious MIME type

The fixtures must represent actual observed/verified source structures where available. Do not invent unsupported WAHO markup merely to make a test pass.

All tests must run without network access.

---

## 9. Tests

At minimum, verify:

1. `get_detail()` returns the correct neutral `TenderDetail`.
2. English, French, and Portuguese fixture pages parse correctly.
3. Deadline date/time/timezone components are preserved according to the DTO contract.
4. No naive timezone assumption is introduced.
5. Missing optional fields do not crash parsing.
6. Missing/404 detail produces the expected structured error.
7. `get_attachments()` returns **all** exposed document links.
8. ZIP links are correctly identified as top-level ZIP/bundle candidates.
9. No ZIP contents are enumerated during discovery.
10. MIME/size metadata is preserved only when actually available.
11. No final checksum is fabricated before bytes are downloaded.
12. Unfamiliar attachment extensions are not silently discarded.
13. Correlation ID is present on every returned result/error.
14. No WAHO-specific types leak through the adapter boundary.
15. No network access is required by the unit/fixture tests.
16. Prompt 04 discovery tests still pass.
17. Prompt 05 deduplication tests still pass.
18. Prompt 06 persistence/migration tests still pass.
19. Full lint/type-check/test suite passes.

---

## 10. Explicit non-goals

Do NOT implement:

* document downloading
* byte storage
* checksum calculation from downloaded bytes
* ZIP extraction
* recursive inner-file enumeration after download
* OCR
* text extraction
* table extraction
* document classification
* AI triage
* AI verdicts
* email notifications
* deduplication logic
* tender persistence logic outside the existing Prompt 06 repository seams

---

## Report

At completion, report:

1. files changed
2. implementation of `get_detail()`
3. implementation of `get_attachments()`
4. mapping to `TenderDetail`
5. mapping to `TenderAttachment`
6. exact Prompt 06 `Document` persistence seam used, if any
7. fixture files added
8. verified WAHO attachment behavior
9. unknown/unverified WAHO behavior
10. structured errors implemented
11. tests added
12. test/lint/type-check results
13. any TODOs or open decisions
14. confirmation that Prompt 08 can consume the output without requiring WAHO-specific knowledge

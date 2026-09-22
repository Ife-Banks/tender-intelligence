# Prompt 08 — Document Download

> Paste `prompts/00-master-context.md` first.

## Read

* `PROJECT_RULES.md`
* `source-docs/tender-intelligence-spec-v1.1.md`
* `docs/06-document-processing-spec.md` (§6.1 acquisition, §6.2 checksums, §6.5 failure handling)
* `docs/04-pipeline-spec.md` (§4.2 stage 4, §4.5, §4.6, §4.7)
* `docs/10-security-spec.md` (storage and untrusted-file handling)
* `docs/03-tender-data-model.md` (`Document` fields and exact statuses)
* `docs/02-technical-architecture.md` (storage/connection/seam requirements)
* `prompts/06-tender-persistence.md` and the actual Prompt 06 implementation
* `prompts/07-document-discovery.md` and the actual Prompt 07 implementation

## Task

Implement the **document acquisition/download stage**.

Input:

```text
TenderAttachment
    ↓
Prompt 08
    ↓
stored document bytes + Document metadata/status
```

Prompt 08 owns acquisition of document bytes exposed by Prompt 07.

It does NOT own document text extraction, OCR, table extraction, AI processing, or verdict generation.

The implementation must use the existing neutral `TenderAttachment` and `Document` contracts. Do not create WAHO-specific document models or a second document persistence mechanism.

---

# 1. Download every discovered attachment

For each `TenderAttachment` supplied by Prompt 07:

* fetch the source URL;
* preserve the original source URL;
* preserve the original filename where available;
* determine/store the actual downloaded size;
* determine/store the MIME type according to the existing project contract;
* calculate the content checksum after successful acquisition;
* store the bytes through the configured storage abstraction;
* associate the resulting storage location with the existing `Document.storage_path` field.

The tender must not depend on the remote source remaining available after successful acquisition.

Do not silently skip an attachment because:

* the extension is unfamiliar;
* MIME type is missing;
* advertised size is missing;
* filename is unusual.

If the source exposes a valid document link, attempt acquisition unless the existing specification explicitly excludes it.

---

# 2. Storage abstraction

Do not hardcode filesystem paths into the document fetcher.

Use the project's storage seam so the implementation can support the configured storage backend, such as:

* local filesystem;
* object/bucket storage.

The fetcher should depend on an interface/abstraction rather than directly embedding storage-specific implementation throughout the pipeline.

Storage writes must be safe against partial downloads.

A failed or interrupted download must not leave a partially written file referenced as a successfully acquired `Document`.

Prefer:

```text
download temporary bytes
        ↓
validate successful acquisition
        ↓
calculate checksum
        ↓
atomically commit to storage
        ↓
update Document acquisition state
```

rather than exposing the destination before the download is complete.

---

# 3. Filename and path security

Never trust a remote filename as a local path.

Sanitize or safely map filenames so that:

* `../` cannot escape the configured storage root;
* absolute paths cannot escape the storage root;
* Windows path separators cannot escape the root;
* null/control characters are handled safely;
* duplicate filenames do not overwrite unrelated documents;
* generated storage keys cannot collide unexpectedly.

Preserve the original filename as metadata where the `Document` contract permits it.

Do not replace the original filename merely because the local storage key must be safe.

---

# 4. Remote URL and HTTP safety

Treat remote document URLs and downloaded content as untrusted input.

Follow the existing security specification for:

* allowed URL schemes;
* connection timeout;
* read timeout;
* redirect behavior;
* maximum redirects;
* maximum response size;
* request headers;
* TLS verification;
* rate limiting;
* retry behavior.

Do not follow unsafe schemes such as local filesystem URLs unless explicitly permitted by the project architecture.

If the security specification requires protection against requests to private/internal network destinations, enforce that at the HTTP boundary.

Do not log:

* authorization headers;
* cookies;
* credentials;
* signed URLs containing sensitive credentials;
* other secrets.

---

# 5. Response/content validation

Where supported by the existing specification:

* distinguish HTTP failure from successful acquisition;
* capture actual response MIME type;
* compare advertised size with actual size where appropriate;
* detect obviously invalid responses;
* do not treat an HTML error page as a successfully downloaded PDF merely because the URL ended in `.pdf`.

Do not introduce content-validation rules that conflict with the authoritative document-processing specification.

If validation cannot establish a fact, represent it as unknown rather than inventing metadata.

---

# 6. Checksums

Calculate the content checksum from the **actual acquired bytes** according to the checksum algorithm specified by `docs/06-document-processing-spec.md`.

The checksum must be:

* deterministic;
* stable for identical bytes;
* stored with the `Document`;
* calculated before marking the acquisition successful.

### Important sequencing rule

Prompt 05 runs before Prompt 08.

Therefore, a checksum calculated by Prompt 08 cannot retroactively be the initial Prompt 05 deduplication input for that same pipeline run.

Do not modify Prompt 05's deduplication logic here.

Instead:

* Prompt 05 remains responsible for tender-level deduplication/change detection defined by its contract;
* Prompt 08 records the document content checksum after acquisition;
* later document-level comparisons may use the stored checksum if the authoritative specification requires that behavior.

If the source provides a checksum before download, preserve and validate it according to the existing specification rather than assuming that a source-provided checksum is equivalent to a locally calculated checksum.

Do not claim that an unknown remote document has a matching checksum before its bytes are acquired.

---

# 7. Idempotency and existing documents

Acquisition must be safe to retry.

If the existing `Document` already has a successfully stored copy, follow the exact existing repository/status contract to determine whether acquisition can be skipped.

A "same checksum" optimization is valid only when the checksum being compared is already known without requiring the download being avoided.

Valid examples may include:

```text
known source checksum
        ↓
compare with stored checksum
        ↓
same → skip acquisition
```

or:

```text
existing successfully acquired document
        ↓
same acquisition identity according to project contract
        ↓
reuse existing stored bytes
```

Do NOT implement:

```text
unknown remote checksum
        ↓
assume it matches stored checksum
        ↓
skip download
```

If the only way to know whether the remote bytes changed is to download them, perform the download according to the specification.

Repeated execution must not create unnecessary duplicate `Document` rows or overwrite a valid stored copy incorrectly.

---

# 8. ZIP acquisition and extraction

If a discovered `TenderAttachment` is a ZIP/archive and the authoritative specification requires Prompt 08 to unzip and recurse:

```text
top-level ZIP attachment
        ↓
download ZIP
        ↓
validate archive
        ↓
extract safely
        ↓
inner documents
        ↓
Document records
```

The top-level ZIP remains traceable to the source attachment.

Each acquired inner document must have its own `Document` identity/status and checksum according to the existing data model.

Return/propagate a flat document set to downstream processing, while retaining whatever parent/archive relationship the existing model explicitly supports.

Do not invent parent-child fields if the existing `Document` model does not contain them.

---

# 9. ZIP security

ZIP files are untrusted input.

Extraction must protect against:

* `../` path traversal;
* absolute paths;
* Windows drive-letter paths;
* symlinks or equivalent archive entries;
* excessive decompression size;
* excessive compression ratio where the security specification requires it;
* excessive number of archive members;
* nested archives beyond the configured recursion limit;
* excessive filename/path length;
* malformed/corrupt archives.

Use configurable resource limits from the existing project configuration/specification.

Never allow an extracted archive member to escape the configured storage boundary.

A malicious ZIP must not be able to exhaust memory, disk, CPU, or file descriptors without being contained by configured limits.

---

# 10. ZIP recursion

"Recursive" means recursion supported by the project specification and configured safety limits.

Do not recurse indefinitely.

For nested archives:

* follow the documented recursion limit;
* stop safely when the limit is reached;
* record the appropriate document/extraction failure state;
* do not crash the entire tender.

If the project specification does not require nested ZIP recursion, do not invent it merely because the implementation could support it.

---

# 11. Per-document failure tolerance

One failed document must not prevent other documents from being acquired.

Example:

```text
5 attachments
    ├── A → success
    ├── B → success
    ├── C → failed
    ├── D → success
    └── E → success
```

Expected outcome:

```text
A → acquired
B → acquired
C → failed
D → acquired
E → acquired
```

The tender-level pipeline must continue according to the failure semantics in `docs/06-document-processing-spec.md` and `docs/04-pipeline-spec.md`.

A completely missing document set must remain distinguishable from a successfully acquired document set.

Do not convert complete acquisition failure into false success.

---

# 12. Structured errors

Network/acquisition failures must use the existing structured error taxonomy.

At minimum support the project's:

* `document_download_failed`

and any additional error codes explicitly defined by the authoritative specification.

Capture useful diagnostic information such as:

* document identity;
* source URL reference where safe;
* HTTP/status category where available;
* failure category;
* retryability;
* correlation ID.

Never put secrets into error details.

---

# 13. Retry/backoff

Implement polite acquisition behavior consistent with the project's HTTP/source configuration.

For transient failures:

* retry according to configured limits;
* use backoff;
* respect rate limits;
* do not retry permanent failures indefinitely.

Examples of potentially transient conditions include:

* connection reset;
* temporary DNS/network failure;
* selected 5xx responses;
* timeout.

Examples of potentially permanent conditions include:

* 404;
* unsupported scheme;
* invalid URL;
* deterministic parser/content validation failure.

Follow the actual project configuration/specification rather than inventing arbitrary retry counts.

---

# 14. Document statuses

Use the **exact `Document` statuses and transition rules already defined by Prompt 06 and `docs/03-tender-data-model.md`**.

Do not invent:

* new acquisition statuses;
* new extraction statuses;
* new terminal states.

The fetcher may advance a document only through transitions that the existing model permits.

If the model has separate download/acquisition and extraction statuses, do not mark extraction successful merely because a ZIP was downloaded.

---

# 15. Language metadata

Only populate `Document.language` if the existing specification defines acquisition-stage language detection.

Do not perform document text/OCR extraction merely to guess language if that belongs to Prompt 09.

If language cannot be established at acquisition time:

* leave it unknown;
* do not invent a language from filename alone unless explicitly allowed by the specification.

Prompt 09 owns content-based language detection if that is where the authoritative architecture places it.

---

# 16. Correlation IDs and auditability

Every document acquisition write must be correlation-stamped according to the existing architecture.

Verify that:

* the correlation ID is propagated from the pipeline;
* document rows are queryable by correlation ID where required;
* retries do not create unrelated correlation identities unnecessarily;
* errors contain the appropriate correlation ID.

Do not use correlation IDs as document business identity.

---

# 17. Concurrency

Document acquisition must behave correctly if two workers attempt to acquire the same document concurrently.

Use the existing repository/database/storage uniqueness and transaction mechanisms.

Do not rely solely on:

```text
SELECT
if not exists:
    INSERT
```

without concurrency protection.

Avoid duplicate storage records and inconsistent status updates.

---

# 18. Persistence and transaction boundaries

Use the repository interfaces established by Prompt 06.

Do not:

* create another `Document` model;
* bypass repositories with raw ORM calls from pipeline code;
* rewrite Prompt 06 persistence rules;
* modify Tender deduplication logic.

Ensure database state and storage state cannot falsely claim that a document is successfully acquired when its bytes were never committed.

If storage and database operations cannot be made fully atomic, implement the project's documented compensation/reconciliation strategy rather than silently accepting inconsistent state.

---

# Out of scope

Do NOT implement:

* tender listing discovery;
* detail-page parsing;
* attachment enumeration;
* AI triage;
* AI verdict;
* email notification;
* document text extraction;
* OCR;
* table extraction;
* semantic document analysis;
* final tender verdict;
* changes to Prompt 05 deduplication logic.

---

# Tests

Implement tests covering at minimum:

## Basic acquisition

* fixture attachment → bytes stored;
* original filename preserved;
* source URL preserved;
* actual size recorded;
* MIME behavior verified;
* checksum correct and stable;
* `Document.storage_path` points to the committed stored object.

## ZIP

* ZIP fixture acquired;
* members extracted;
* inner documents become the expected `Document` records;
* inner checksums are correct;
* inner filenames are preserved safely;
* flat downstream document set is produced;
* archive relationship follows the existing model.

## ZIP security

Test:

* `../` traversal;
* absolute paths;
* Windows-style traversal;
* symlink entry if supported by the archive library;
* oversized archive;
* excessive member count;
* malformed ZIP;
* nested archive beyond configured recursion limit.

Expected behavior must be safe failure, not process crash.

## Failure tolerance

Five-document fixture:

```text
A → success
B → success
C → failure
D → success
E → success
```

Verify that:

* A/B/D/E are acquired;
* C has the correct failure status;
* the tender pipeline continues;
* the failure is recorded;
* the overall result distinguishes partial acquisition from complete acquisition.

## Retry

Mock transient failures and verify:

* retry occurs;
* backoff is applied;
* retry limit is respected;
* eventual success works;
* eventual failure is recorded correctly.

## Idempotency

Test:

* repeated acquisition of the same successfully stored document;
* known source checksum matching stored checksum, if supported;
* unchanged stored document;
* failed previous attempt followed by successful retry.

Verify no unnecessary duplicate `Document` rows or corrupted storage.

## Correlation

Verify that all writes and failures are correlation-stamped.

## Path security

Verify malicious filenames cannot escape the configured storage boundary.

## HTTP safety

Test:

* timeout;
* redirect behavior;
* unsupported scheme;
* oversized response;
* HTTP 404;
* HTTP 5xx;
* malformed response where applicable.

## Regression

Run:

* Prompt 04 tests;
* Prompt 05 tests;
* Prompt 06 tests;
* Prompt 07 tests;
* Prompt 08 tests;
* full suite;
* lint;
* type check;
* migration validation.

---

# Rules

* Follow the authoritative v1.1 specification.
* Follow the actual Prompt 06 `Document` model/status/repository implementation.
* Do not invent fields, statuses, transitions, checksum algorithms, retry policies, or storage behavior.
* Conflicts between documents must be surfaced rather than silently resolved.
* Treat all remote files and archive contents as untrusted.
* Never log secrets.
* Never expose partial files as successfully acquired documents.
* Keep WAHO-specific behavior out of the generic document acquisition layer.
* Do not modify earlier prompt behavior merely to make Prompt 08 easier.

---

# Report

Report:

1. files changed;
2. `TenderAttachment` → `Document` mapping;
3. exact document status transitions used;
4. checksum algorithm and when it is calculated;
5. idempotency strategy;
6. storage abstraction/seam;
7. ZIP extraction and recursion behavior;
8. ZIP security controls;
9. retry/backoff behavior;
10. per-document failure behavior;
11. concurrency/transaction approach;
12. correlation-ID handling;
13. tests added;
14. tests/results;
15. TODOs;
16. open decisions;
17. any assumptions that could not be verified from the authoritative project documents.

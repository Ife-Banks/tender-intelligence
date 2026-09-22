# Prompt 04 — WAHO Source Discovery

> Paste `prompts/00-master-context.md` first.

## Role

You are implementing **Phase 1 — WAHO listing discovery** for the Tender Intelligence system.

This task implements only the **discovery half of the WAHO source adapter**.

Do not implement later pipeline stages.

The goal is to establish a reliable, testable source adapter that can enumerate WAHO tender listing rows and return them in the project's neutral `SourceAdapter` representation.

---

# 1. Read Before Coding

Read these files before making any changes:

### Required

* `PROJECT_RULES.md`
* `docs/05-source-adapter-spec.md`

  * §5.1 SourceAdapter interface
  * §5.2 discovery/pagination/listing extraction
  * §5.3 WAHO
* `docs/04-pipeline-spec.md`

  * §4.1 stage 2
  * §4.2
  * §4.11 configuration reload
* `docs/02-technical-architecture.md`

  * §2.9 source adapter abstraction
* `docs/11-testing-strategy.md`
* `docs/14-acceptance-criteria.md`
* `docs/13-open-decisions.md`

  * O14 scraping/ToS

Also read:

* `prompts/01-architecture.md`
* `prompts/03-infrastructure.md`

Do not modify the specification documents to make implementation easier.

If the existing implementation differs from the documentation, stop and report the discrepancy before silently changing architecture.

---

# 2. Task

Implement the **listing-discovery portion** of the WAHO source adapter.

The implementation must operate through the existing generic:

```text
SourceAdapter
```

interface.

Implement the WAHO adapter's:

```text
list_new_tenders()
```

method, or the exact equivalent defined by the existing `SourceAdapter` interface.

### Important semantic rule

Despite the method name `list_new_tenders()`, this method must **not determine whether a tender is new relative to the database**.

It should:

1. crawl the configured WAHO listing pages;
2. discover listing rows;
3. normalize them into the generic adapter representation;
4. return the discovered candidates.

Determining whether a candidate is:

* NEW
* EXISTING
* UPDATED

belongs to the deduplication stage implemented separately by:

`prompts/05-tender-deduplication.md`

The adapter must therefore remain stateless with respect to seen-tender state.

---

# 3. Expected Discovery Flow

The implementation should support this flow:

```text
Configured WAHO listing URL(s)
        ↓
HTTP fetch
        ↓
Parse listing page
        ↓
Extract listing rows
        ↓
Normalize candidates
        ↓
Follow pagination
        ↓
Repeat
        ↓
Return discovered candidates
```

The adapter must not:

* write tender records;
* query seen-tender state;
* perform deduplication;
* fetch tender detail pages;
* download attachments;
* process documents;
* call an LLM;
* send email.

---

# 4. WAHO Listing Adapter

Implement WAHO as a:

```text
Paginated HTML list
```

source type.

For each listing row, extract the fields required by the neutral adapter contract, including where available:

* `external_id`
* title
* listing/detail URL
* published date
* deadline
* raw/source metadata

Do not introduce WAHO-specific fields into shared domain models unless the existing specification explicitly requires them.

If WAHO exposes additional useful source metadata, preserve it through the adapter's designated raw metadata mechanism rather than contaminating generic domain types.

---

# 5. Pagination

Implement pagination according to the source-adapter specification.

The adapter must:

* follow valid next-page links;
* normalize relative and absolute URLs correctly;
* stop when there is no next page;
* stop when the page produces no new listing rows according to the adapter's crawl-level safeguards;
* prevent infinite pagination loops;
* track visited listing-page URLs or an equivalent deterministic pagination guard;
* enforce a configurable maximum page limit if the architecture supports one;
* avoid repeatedly fetching the same page.

Do not use database "seen tender" state to determine pagination termination.

Pagination termination must be safe even if the source behaves incorrectly.

Add a test proving that a cyclic or repeated next-page link cannot cause an infinite loop.

---

# 6. Polite Crawling

Follow the crawling requirements documented in:

`docs/05-source-adapter-spec.md`

The implementation must support the project's configured:

* robots.txt behavior;
* rate limiting;
* request timeout;
* retry policy;
* exponential/backoff behavior;
* realistic user-agent identification;
* HTTP error handling.

Do not hammer WAHO.

Tests must use offline fixtures and mocked HTTP responses.

Do not make the test suite depend on live WAHO availability.

---

# 7. Configuration

WAHO-specific site details must be configuration-driven wherever the specification allows it.

Do not hard-code undocumented site-specific behavior into business logic.

Where appropriate, keep configurable:

* listing URL;
* pagination behavior/selectors;
* request settings;
* rate limit;
* timeout;
* user agent;
* parser selectors;
* page limits.

Do not invent configuration values that represent business decisions.

Use existing project configuration conventions.

If a value is not specified, choose only a technical default where safe and document it as such.

---

# 8. Verification of WAHO Structure

Do not assume undocumented WAHO behavior.

Before encoding selectors or pagination semantics:

1. inspect the available project documentation;
2. inspect any existing WAHO fixtures;
3. if live verification is available and appropriate, inspect the live source;
4. distinguish verified behavior from assumptions.

Record:

### Verified

Facts supported by the source/fixture.

### Unknown

Behavior that could not be established.

### Risk/TODO

Behavior that may change or requires further verification.

Do not fabricate selectors, URLs, fields, pagination rules, or deadline semantics.

If selectors cannot be reliably established, fail safely with a structured parser mismatch rather than silently returning incorrect data.

---

# 9. Parser Configuration

Keep site-specific parsing configuration separate from generic adapter logic where the architecture supports this.

Use a `parser_config` or equivalent configuration mechanism for:

* CSS selectors;
* XPath selectors if the implementation uses them;
* pagination selectors;
* field mappings;
* other site-specific parsing details.

Do not turn `parser_config` into an uncontrolled escape hatch for business logic.

---

# 10. Dates and Deadlines

Parse published dates and deadlines into the project's neutral representation.

Preserve the source timezone information where available.

Do not silently convert a date into UTC and discard the original timezone.

If the source does not provide enough information to establish a reliable timezone:

* preserve the original source value;
* mark the timezone as unknown/unspecified according to the project's data model;
* do not invent a timezone.

A missing deadline must not cause the entire listing to fail.

---

# 11. Multilingual Content

The discovery parser must handle the multilingual listing fixtures required by the specification.

At minimum, include fixtures representing:

* English
* French
* Portuguese

Do not assume that field extraction depends on English text labels unless the source specification explicitly establishes that.

Preserve the original title/text where required.

If the project has a language field or detection mechanism, populate it according to the documented contract.

Do not introduce an external translation service.

---

# 12. Raw Metadata

Preserve useful source-level metadata through the adapter's designated raw metadata field.

Examples may include:

* source labels;
* original date strings;
* category/type;
* listing-page URL;
* other source fields required for debugging.

Do not duplicate source-specific metadata into generic domain fields merely for convenience.

Raw metadata must remain serializable and safe to persist/log.

Do not include credentials or secrets.

---

# 13. Dry Run / Test Source Support

Support the source-level dry-run behavior required by:

`docs/04-pipeline-spec.md §4.10`

A "Test this source" operation must be capable of:

* fetching/parsing the configured listing pages;
* showing/returning what would be discovered;
* reporting parser/network errors;
* returning normalized candidates.

It must NOT:

* write seen-tender records;
* mark tenders as processed;
* persist business tender records;
* send business emails;
* mutate business pipeline state.

If the dry-run mechanism belongs to a higher orchestration layer rather than the adapter itself, implement only the adapter contract required to support it and clearly report that boundary.

Do not duplicate orchestration logic inside the adapter.

---

# 14. Error Handling

Implement structured source errors consistent with the existing error model.

At minimum distinguish:

### Source unreachable

Example code:

```text
source_unreachable
```

### Parser mismatch

Example code:

```text
parser_mismatch
```

### Invalid/unsupported listing data

Use the project's documented error classification if one exists.

Do not crash the entire process because one listing row is malformed when the remaining page can be processed safely.

Do not silently discard systemic parser failures.

---

# 15. Correlation IDs

Every discovery operation must participate in the project's correlation-ID mechanism.

Correlation information must be available to:

* source requests;
* parser errors;
* discovery results/logging;
* retry events;
* failure reporting.

Do not create an unrelated correlation-ID system if the project foundation already provides one.

---

# 16. Fixtures

Create offline fixtures sufficient to test the parser and pagination.

At minimum provide:

1. A normal WAHO listing page.
2. A second paginated listing page.
3. A listing containing a missing deadline.
4. A French-language notice.
5. A Portuguese-language notice.
6. A listing edge case.
7. A repeated/cyclic pagination scenario.
8. A parser-mismatch/changed-structure scenario if practical.

The fixtures should represent the actual structure established during verification.

Do not invent fictional HTML structures merely to make tests pass if real source structure is available.

Tests must not require live network access.

---

# 17. Tests

Implement tests alongside the adapter.

At minimum test:

### Basic extraction

`list_new_tenders()` returns normalized candidates containing the required fields.

### Pagination

The adapter follows the second fixture page and combines the results correctly.

### Pagination termination

The adapter terminates when:

* no next page exists;
* the next page is empty;
* a page repeats;
* pagination forms a cycle;
* the configured maximum page limit is reached, if supported.

There must be no infinite loop.

### Missing deadline

A listing without a deadline is returned successfully.

The missing value must follow the project's nullable/unknown representation.

### Timezone

A source deadline containing timezone information preserves that information according to the documented model.

### Multilingual

French and Portuguese fixtures parse successfully.

### Malformed listing

One malformed row does not necessarily destroy the entire page when safe partial processing is supported.

### Parser mismatch

A materially changed page structure produces a structured `parser_mismatch` failure/risk rather than silently returning corrupt data.

### Network failure

An unreachable source produces the appropriate `source_unreachable` error.

### Retry/backoff

Retry behavior follows the configured policy without causing uncontrolled requests.

### Dry run

The discovery test path performs no business-state writes and no email delivery.

### Correlation ID

Discovery operations and emitted records/errors contain the expected correlation identifier.

---

# 18. No Database Deduplication

This task must not query the tender database to determine whether a tender is new.

Do not implement:

```text
SELECT ...
WHERE external_id = ...
```

for the purpose of classifying a listing as new.

That belongs to:

`prompts/05-tender-deduplication.md`

The adapter returns discovered candidates only.

---

# 19. Out of Scope

Do NOT implement:

* detail-page discovery;
* `get_detail()` unless required only as an existing interface stub;
* `get_attachments()`;
* attachment downloading;
* document processing;
* OCR;
* tender persistence;
* database deduplication;
* update detection;
* pipeline orchestration;
* RunHistory integration;
* email;
* AI;
* knowledge base;
* LLM providers;
* admin UI;
* additional source adapters;
* RAG/vector search;
* production deployment;
* resolution of the scraping/ToS business decision.

The scraping/ToS issue identified as O14 must be recorded/reported, not decided by the coding agent.

---

# 20. Security

Do not introduce:

* hard-coded credentials;
* API keys;
* passwords;
* session cookies;
* secrets in fixtures;
* secrets in logs.

Do not log complete authorization headers or other sensitive request information.

---

# 21. Code Quality

Follow `PROJECT_RULES.md`.

In particular:

* keep WAHO-specific behavior inside the WAHO adapter;
* do not leak WAHO-specific concepts into generic pipeline/domain code;
* use existing abstractions;
* avoid unrelated refactoring;
* add tests with implementation changes;
* keep functions/components focused;
* prefer deterministic parsing;
* fail safely when source structure is unknown.

---

# 22. Implementation Process

Before coding:

1. Inspect the existing repository.
2. Read the required documentation.
3. Inspect the existing `SourceAdapter` interface.
4. Inspect the existing configuration system.
5. Inspect existing HTTP/network utilities.
6. Inspect existing error and logging infrastructure.
7. Inspect existing test conventions.
8. Inspect any existing WAHO fixtures.

Then provide a concise implementation plan.

Only after that should you modify the repository.

---

# 23. Acceptance Criteria

This task is complete only when:

* WAHO listing discovery is implemented behind `SourceAdapter`.
* Discovery is stateless with respect to seen-tender state.
* Pagination is deterministic and cannot loop forever.
* Required listing fields are normalized.
* Missing deadlines are handled safely.
* Multilingual fixtures pass.
* Network failures are classified correctly.
* Parser mismatches fail safely.
* Rate limiting/retry behavior follows the documented policy.
* Dry-run behavior does not mutate business state or send email.
* Correlation IDs are propagated.
* WAHO-specific logic remains isolated.
* Offline tests pass.
* No later pipeline stages have been implemented.

---

# 24. Final Report

After implementation, report:

## Files Changed

List every created/modified file relevant to this task.

## Implementation

Summarize what was implemented.

## Documentation Mapping

For each major implementation area, identify the relevant requirement/document section.

Example:

```text
Pagination → docs/05-source-adapter-spec.md §5.2
```

## Verified WAHO Behavior

List what was actually verified:

* listing structure;
* selectors;
* pagination;
* URL structure;
* published date;
* deadline representation;
* multilingual behavior.

## Unknown / Unverified Behavior

List anything that could not be established.

## Risks / TODOs

List parser fragility, source changes, ToS concerns, or other issues.

## Tests

Report:

* tests run;
* number passed/failed;
* lint/type-check results;
* whether any tests require network access.

Confirm that normal tests do not require live WAHO access.

## Scope Check

Explicitly confirm that the following were NOT implemented:

* deduplication;
* persistence;
* document downloading;
* document processing;
* email;
* AI;
* pipeline orchestration.

## Recommended Next Task

Identify the next implementation prompt.

Do not implement it as part of this task.

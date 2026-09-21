# 05 — Source Adapter Specification

> Source of truth: v1.1 §5.1, §5.2. WAHO is the first implementation source (Phase 1). The WAHO adapter must remain **fully isolated behind the `SourceAdapter` interface** — no WAHO-specific logic may leak into the pipeline. Do **not** assume undocumented WAHO behaviour; anything not verified is an open question to confirm during implementation.

## 5.1 The `SourceAdapter` interface

From v1.1 §5.1 (design note):

```text
list_new_tenders()
get_detail(tender_id)
get_attachments(tender_id)
```

- One adapter per `source_type`.
- Adding a new **site** of an already-supported type = a new config entry only (no new code).
- Adding a genuinely new **type** of site = one new adapter class only.

**Source types (day-one support):**

| Type | Example | Notes |
|---|---|---|
| Paginated HTML list | WAHO Tenders, TenderDetail | Follow "next page" links until no new items found |
| Filtered/faceted HTML list | All Business Africa (`?status=open`) | Respect query-string filters defined in config |
| Search-form-driven | UNGM | Per-adapter form fill/submit, or the site's API/export |
| RSS/Atom feed | (future) | Trivial — own type |
| JSON API | (future, OCDS portals) | Prefer over HTML scraping where exposed |

## 5.2 Generic adapter responsibilities

### Discovery
- Enumerate listing rows from the configured listing URL per `parser_config`.
- Report listing "new" items only against the seen-record maintained by the pipeline; the adapter itself is stateless w.r.t. seen state `[PROPOSED]`.

### Pagination
- Paginated HTML: follow "next page" links until no new items are found.
- Respect configured per-page/limit and polite pacing.

### Listing extraction
- Extract per-listing: `external_id` (stable identifier), title, URL, published date, deadline (if listed), any raw metadata.

### Detail-page extraction
- Full detail per `get_detail(tender_id)`: title, procuring body, ref/grant numbers, scope, requirements hints, deadlines.
- Preserve raw HTML/metadata in `Tender.raw_metadata`.

### Attachment discovery
- Enumerate every document link on the detail page (RFPs, TORs, annexes, EOI forms, procurement plans, ZIP archives).
- Return download URLs + suggested filenames; let the fetcher handle download/persist.

### Deadline extraction
- Extract deadline date and time; return **UTC** plus the **original timezone string** (v1.1 §5.6). Emails show WAT and the notice's stated timezone.

### Timezone handling
- Store both values; never assume the notice's timezone equals WAT unless verified in the notice.
- Nigerian-time display is the presentation rule, not an assumption about source data.

### Deduplication identifiers
- `external_id`/URL is the natural dedupe key per source (`(source_id, external_id)` unique).
- If a source lacks stable IDs, define the dedupe identifier in `parser_config` explicitly `[NEEDS_DECISION per source]`.

### Update detection
- Adapter re-reads a tender's detail and attachments; the pipeline diffs against previously-stored documents/deadlines to decide "update" (new addendum, extended deadline) vs "no change."

### Retries
- Polite backoff on transient network errors; respect `Retry-After`/rate-limit headers where present (v1.1 §5.2 back off on errors).

### Rate limiting
- Configurable per-source; obey it strictly. Respect robots.txt. Use a real user agent.

### Error handling
- Classify failures to map to structured error codes (`source_unreachable`, `parser_mismatch`, `document_download_failed`).
- A source-level failure logs detail and raises a throttled system alert.

### Fixtures / test data
- Provide offline fixtures (saved HTML/JSON pages) so adapter tests run without network and without hammering the source.
- Fixtures must include: listing page(s), a paginated second page, detail page(s), attachment links, and edge cases (missing deadline, French/Portuguese content, scanned documents).

### Test / dry-run behaviour
- Admin "Test this source" runs the adapter in **dry-run**: fetch and display what it would find, write nothing to seen-tenders, send nothing.

## 5.3 WAHO — first implementation source

- **Type:** Paginated HTML list.
- **Purpose in validation:** the most structured, clearest example to prove `crawl → detect new → fetch docs → extract text` end-to-end (v1.1 §10 Phase 1).
- **Language reality (confirmed by source):** WAHO publishes in French, English and Portuguese, often in the same notice.
- **Document reality (confirmed by source):** PDF (frequently scanned/mixed), DOCX TORs, ZIP archives.

> **Rule:** Only behaviours verified against the actual WAHO site may be encoded. Anything unverified (exact DOM structure, pagination markers, deadline formats, whether deadlines appear on listing vs detail pages) must be resolved by confirming against the live site or fixtures before hard-coding — and captured in `parser_config`, not in adapter code where avoidable.

### WAHO adapter requirements (as confirmed by v1.1)

1. Implement `list_new_tenders()`, `get_detail()` and `get_attachments()` for the WAHO Paginated HTML source type.
2. Crawl politely: robots.txt, rate limit, real UA, backoff.
3. Capture the deadline with timezone (the exact reason the 18 Sep 2026 EOI was missed).
4. Discover every attachment on the detail page, including attachments inside ZIPs (handled by the fetcher/unpacker, §06).
5. Support French/English/Portuguese content in listings and details.
6. Provide fixtures + dry-run support as above.

### WAHO non-requirements (do not invent)

- No assumption of URLs, DOM selectors, or API endpoints — confirm against the live site/docs.
- No assumption of a JSON API for WAHO (not claimed by the source).
- No assumption about WAHO rate limits beyond "be polite" (v1.1 §5.2).

## 5.4 Other sources (Phase 3, listed for context)

| Source | Type | Notes |
|---|---|---|
| TenderDetail | Paginated HTML | Paid/registration may apply — open question (`docs/13-open-decisions.md`); may scrape public free-tier pages only |
| All Business Africa | Filtered/faceted HTML | `?status=open`; high volume — load-test triage |
| UNGM | Search-form-driven | The "hard" one — must be one new adapter, not a rewrite |
| Any OCDS portal | JSON API | Prefer API over scraping |

These are **not** v1 Phase 0–2 deliverables.

## 5.5 Legal/ToS note (developer-owned)

Before building any adapter: read the site's terms re: automated scraping; prefer official APIs/feeds where they exist. Record findings per source (v1.1 §12.3 #11). This does not block WAHO development but is a standing duty per source.
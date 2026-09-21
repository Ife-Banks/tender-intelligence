# Prompt 04 — WAHO Source Adapter

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/05-source-adapter-spec.md` (full — the WAHO section §5.3 especially)
- `docs/04-pipeline-spec.md` (§4.2 stage 2–4, §4.3–4.11)
- `docs/02-technical-architecture.md`
- `docs/13-open-decisions.md` (O14 scraping/ToS — record your finding, don't decide policy)

## Task

Implement the **WAHO source adapter** fully isolated behind the `SourceAdapter` interface, plus its fixtures. This proves the Phase 1 path for `docs/12-deployment.md` (crawl → detect new → fetch docs → extract text).

1. `SourceAdapter` interface (`list_new_tenders()`, `get_detail(tender_id)`, `get_attachments(tender_id)`) implemented for WAHO as a **Paginated HTML list** source type.
2. Adapter must:
   - Crawl politely: robots.txt respect, rate limit, real user agent, backoff on errors (`docs/05` §5.2).
   - Paginate: follow "next page" links until no new items are found.
   - Extract per-listing: `external_id`, title, URL, published date, deadline (if present), raw metadata.
   - Extract details including deadline (UTC + original timezone string, per `docs/07` §7.2 and `docs/05` §5.2) and attachments (every document link, incl. ZIPs for the fetcher to handle).
   - Return data in the neutral adapter shape, not WAHO-specific types.
3. **Do not assume undocumented WAHO behaviour.** Verify selectors/URLs/pagination semantics against the live site or captured fixtures before encoding; anything unverified must be surfaced as a TODO/risk, and encoded in `parser_config` where it is site-specific, not in adapter logic.
4. Provide **offline fixtures** (saved listing/detail pages covering: a listing page, a second paginated page, a detail page, attachment links, a missing deadline, a French/Portuguese notice, an HTML/listing edge case). No network in tests.
5. **Dry-run support:** a flag so a "Test this source" run fetches and displays what it would find without writing to seen-tenders and without emailing anyone.
6. Wire the adapter into the crawl runner so new-listing dedup against the DB (`docs/04` §4.1 stage 3) uses a persistent per-source seen-record and produces RunHistory entries.

## Out of scope

- AI, email, document understanding (later prompts).
- Other source adapters (TenderDetail/ABA/UNGM are Phase 3).
- Settling the scraping/ToS question (record it; see O14).
- Hard-coding official/unofficial WAHO URLs in code — put site specifics in config.

## Tests

- Fixture-driven: list_new_tenders returns expected items; pagination terminates (no infinite loop); detail parse test; attachment discovery list test; missing-deadline handling.
- Dedup test: re-running the crawl against identical fixtures produces zero duplicate processing/notifications and stable seen-tenders.
- Dry-run test: no DB writes to seen-tenders, no email output.
- Failure tests: unreachable site → structured error + throttled-alert hook; changed structure → `parser_mismatch` risk surfaced.
- Correlation ID present in all records emitted by the crawl path.

## Rules

- Keep WAHO-specific code inside the adapter module; nothing WAHO-specific may leak into DB models, pipeline, or email code.
- Preserve Test Mode semantics if the crawl runner touches notification later (it should not yet).
- Add tests alongside the implementation.

## Report

Files changed; mapping to `docs/05` requirements; verified-vs-unknown WAHO behaviours; tests run + results; ToS/legal note; TODOs.
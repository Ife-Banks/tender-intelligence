# Source types implementation report

**Gate: SOURCE TYPES: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION**

All five v1.1 source strategies are implemented, registered by configuration, and tested
independently. This is an implementation report; independent verification is not claimed here.

## 1. What was built

| `source_type` | Adapter | Module |
| --- | --- | --- |
| `paginated_html_list` | `PaginatedHtmlAdapter` | `src/tender_intelligence/sources/waho.py` |
| `filtered_html` | `FilteredHtmlAdapter` | `src/tender_intelligence/sources/filtered_html.py` |
| `search_form` | `SearchFormAdapter` | `src/tender_intelligence/sources/search_form.py` |
| `rss_atom` | `FeedAdapter` | `src/tender_intelligence/sources/feed.py` |
| `json_api` | `JsonApiAdapter` | `src/tender_intelligence/sources/json_api.py` |
| `wahoo` | `PaginatedHtmlAdapter` | legacy compatibility alias |

These are peer strategies reached by configuration, not a hierarchy. None is a fallback for
another: each reads a different transport, and choosing one is a decision about what a source
*is*, not a degraded path when another one fails.

`AdapterRegistry.default().source_types` returns exactly the six keys above, and every member of
`SourceType` reports `supported=True`.

The previous `wahoo`-only state is gone. What was `WahoPaginatedAdapter` is now
`PaginatedHtmlAdapter` (paginated HTML is not a WAHO-shaped concept), with
`WahoPaginatedAdapter` retained as an alias because existing fixtures and seeds still use that
name and it must not become a stage-04 failure.

## 2. Why this is one implementation, not five adapters

The point of the work is that adding a fifth source shape cost no new notion of correctness.

**One normalization path.** `sources/normalize.py` holds the single implementation of text
cleaning, whitespace collapsing, reference extraction, absolute-URL resolution, timestamp
parsing and identity construction. Two strategies cannot disagree about what a title is or how
an `external_id` is derived, because there is only one function that decides.

**One polite transport.** Every HTTP method of every strategy goes through the injected
`PoliteHttpClient` / `Requester`, so robots, user-agent, pacing, timeout, retry/backoff and
`Retry-After` cannot be bypassed by a strategy that happens to issue an unusual request. This
required real work rather than a convention: a form submission, an authenticated JSON GET, a
JSON POST body, a feed read, and the HTML detail and attachment reads that `JsonApiAdapter`
inherits from the paginated adapter all had to be routed through the same injected transport.
Two bugs of exactly that shape were found and fixed during testing — the JSON adapter's
inherited HTML detail path and the search-form adapter's follow-up requests were both reaching
for a default client instead of the injected one.

**Provenance everywhere.** Every listing and detail record carries the source, the URL it came
from, the correlation id and the crawl timestamp.

**Bundle discipline.** An archive is recorded as one attachment. Inner files are never
enumerated (prompt 07 §3), which is a correctness rule about what a tender record claims, not a
formatting preference.

**Dedupe has one owner per layer.** Per-crawl `external_id` deduplication is unconditional
inside each adapter, so a source that repeats a row across pages cannot emit it twice.
`stop_when_no_new_items` only controls *termination*; it is not what makes deduplication work.
Persisted dedupe and update classification stay in `DedupService`, so a repeated crawl still
produces a real update decision rather than a silent no-op.

## 3. Per-strategy behaviour worth naming

**Paginated HTML.** Cross-page dedupe, a page cap, a visited-URL set, optional stopping when a
page yields nothing new, selector lists, and a reusable `_crawl` that the filtered and
search-form strategies share.

**Filtered HTML.** The configured facet combination is applied through query parameters, then
the same page cap, visited set and cross-page dedupe as the paginated strategy. This is the
strategy whose whole content is "the same listing, filtered" — which is why it is a
configuration of the paginated adapter rather than a copy of it.

**Search form.** Submission goes through an injected `FormDriver`. A source configured
`requires_browser` fails explicitly with `SOURCE_NOT_RUNNABLE` unless a browser-capable driver is
supplied. This distinction is load-bearing: a plain `Requester` is not a browser, and letting it
silently satisfy `requires_browser` would turn a hard, honest failure into a mysterious parse
failure against a page a browser never rendered. No browser dependency was added.

**RSS / RDF / Atom.** Feed reading including `content:encoded`, `dc:creator`, `dc:date`,
`category` and `enclosure`. A feed entry with no link falls back to the feed URL for identity,
because a feed is allowed to publish a title with nowhere to click.

**JSON API.** `items_path` plus per-field paths that accept a list of candidates, nested
`detail_paths` for every detail field, `page` / `offset` / `cursor` pagination bounded by a page
cap, a visited-URL set and a declared total, a short-page stop, `GET` or `POST` with an optional
body, auth resolved through `AuthResolver` and cleared after the crawl, and
`detail_response_kind` of `json` or `html`.

Two JSON decisions deserve naming because a silent alternative existed:

- **Naive deadlines are rejected, not assumed.** A deadline without an explicit offset, `Z`, or
  UTC marker is refused rather than read as UTC. Reading it as UTC would be a plausible guess
  that is wrong by hours for most of the world, and a wrong deadline on a public tender is worse
  than a visible failure.
- **A configured field is used only for its own job.** `attachment_name_field` supplies
  filenames and is never tried as a link. Preferring the name field as a URL candidate was a real
  bug found by test: it produced URLs built out of file names.

## 4. Testing

Everything below runs offline against an injected in-process transport. No test opens a socket,
and no live source was added, configured or contacted.

| Suite | Tests | Result |
| --- | --- | --- |
| `tests/unit/test_filtered_html_source.py` | 8 | passed |
| `tests/unit/test_search_form_source.py` | 12 | passed |
| `tests/unit/test_feed_source.py` | 16 | passed |
| `tests/unit/test_json_api_source.py` | 32 | passed |
| `tests/unit/test_waho_discovery.py` | 26 | passed |
| `tests/unit/test_waho_detail.py` | 21 | passed |
| `tests/integration/test_source_extensibility.py` | 1 | passed |
| **Focused source suite** | **116** | **passed** |

The configuration-only acceptance test
(`test_two_paginated_sources_with_different_selectors_reach_dedup`) proves the central claim
directly: two configured source rows of one `source_type` are built as the same adapter class
with independent selectors, URLs, detail templates and query parameters, and dedup remains
source-scoped. Extensibility is a configuration property here, not an architectural claim.

## 5. Gate results

**Ruff** — passed on `src/tender_intelligence/sources`, `interfaces/source.py`,
`orchestrator/registry.py` and all four new test files.

**Mypy** — no new errors. Three pre-existing `_parse_deadline` inference errors remain in
`waho.py` (lines 608, 609, 628). Each was verified to sit outside every added or changed diff
hunk, so they were neither introduced nor modified by this work.

**Full suite** — `703 passed, 1 failed, 1 warning` in 353.9s.

The single failure is
`tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`,
the known out-of-scope Prompt 12.1 regression. The test's own source carries replacement
characters, so the French accented pattern cannot match. It was not modified.

## 6. Limitations

- **A browser-needing source still cannot run.** It fails with `SOURCE_NOT_RUNNABLE` until a
  browser-capable `FormDriver` is supplied. This is deliberate: no browser dependency may be
  added, and a loud failure beats a silent degradation.
- **A JSON API that declares no page size cannot know its own end.** Without `page_size`,
  `limit` or `per_page` there is no short-page signal, so termination relies on the page cap or
  an empty page. Declaring the size is strongly preferred.
- **Structural difference in detail pages may still need configuration or a new strategy.** The
  five strategies cover the shapes the v1.1 brief names; they are not a claim that every
  conceivable portal shape is reachable by configuration.
- **Auth is reference resolution only.** `AuthResolver` resolves references; no new credential
  store or secret lifecycle was added. Plaintext secrets never enter configuration, logs, listing
  metadata or retained adapter state, and resolved credentials are cleared once the crawl ends.

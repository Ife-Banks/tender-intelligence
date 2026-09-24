# Source Extensibility Audit — Pre-Prompt 13

**Audit date:** 2026-09-24  
**Scope:** Read-only source architecture audit plus one focused offline integration proof. No
source adapters, Admin UI, AI stages, or production behavior were implemented or changed.

## A. Executive finding

```text
EXTENSIBILITY PARTIALLY VERIFIED
```

The worker, source table, scheduler, normalized DTOs, and deduplication are multi-source. Two
independently configured records can use the registered paginated adapter with different
listing URLs/selectors and flow to per-source deduplication. However, the only built-in
adapter is `WahoPaginatedAdapter`, and its detail fetch URL is hard-coded to
`/tenders/tenders/{id}/list`. Consequently the current schema and factory do not guarantee
configuration-only addition of TenderDetail. Filtered HTML can only be represented as a
static query-bearing URL under the paginated type and compatible markup; no filter model or
filtered type exists. UNGM search form, RSS/Atom, and JSON API are unsupported.

The concrete question's answer is: **configuration-only for another paginated HTML source
only when its detail URL and parsing behavior fit the existing WAHO adapter's fixed
conventions; not generally verified for TenderDetail.** If detail paths differ, source code
must change. The Task 13 AI work can begin on the working WAHO pipeline without source
architecture changes:

```text
PROMPT 13 UNBLOCKED
```

That does not mean the planned source expansion is verified. Resolve the adapter/detail URL
gap before claiming TenderDetail is config-only.

## B. Actual architecture

```text
Source DB row (`Source`, `parser_config`, URLs, schedule, active)
  -> `SourceScheduler.decisions()` / `SourceSpec.from_row()`
  -> `Worker.run_once()` selects each due source independently
  -> `RunCoordinator.run_source(source_id)` loads a fresh runtime settings snapshot
  -> `AdapterRegistry.build(spec)` maps `source_type` to factory
  -> default registry creates `WahoPaginatedAdapter`
  -> `list_new_tenders()` / `get_detail()` / `get_attachments()`
  -> neutral `TenderListing` / `TenderDetail` / `TenderAttachment`
  -> `DedupService.run(source_id, listings)` keyed by `(source_id, external_id)`
  -> persistence, acquisition, processing and run history use source/tender IDs
```

Actual code locations:

| Responsibility | Actual implementation |
|---|---|
| Contract and neutral DTOs | `src/tender_intelligence/interfaces/source.py`: `SourceAdapter`, `TenderListing`, `TenderDetail`, `TenderAttachment` |
| Source model/config record | `src/tender_intelligence/db/models/sources.py`: `Source` |
| Bootstrap source configuration | `src/tender_intelligence/config/seed.py`: `seed_from_yaml()`; input sections include `sources` |
| Source reads and active selection | `src/tender_intelligence/db/repositories/sources.py`: `SourceRepository.list_runnable()` |
| Scheduling and immutable per-source snapshot | `src/tender_intelligence/orchestrator/scheduler.py`: `SourceSpec`, `SourceScheduler` |
| Source-type registry/factory | `src/tender_intelligence/orchestrator/registry.py`: `AdapterRegistry.default()`, `build()` |
| Only built-in adapter | `src/tender_intelligence/sources/waho.py`: `WahoPaginatedAdapter` |
| Crawl policy and HTTP transport | `src/tender_intelligence/sources/policy.py`: `CrawlPolicy`; `sources/polite.py`: `PoliteHttpClient` |
| Per-source run loop | `src/tender_intelligence/orchestrator/worker.py`: `Worker.run_once()` |
| Stages and adapter invocation | `src/tender_intelligence/orchestrator/coordinator.py`: `_stage_discovery()`, `_stage_detail()`, `_stage_acquisition()`, `_stage_processing()` |
| Per-source dedup/persistence boundary | `src/tender_intelligence/dedup/service.py`: `DedupService.run()`; `db/repositories/tenders.py` |

`AdapterRegistry.default()` registers `paginated_html_list` and the legacy alias `wahoo`, both
to the same WAHO-named builder. Unknown types fail that source's discovery stage instead of
being silently skipped. Other pipeline stages consume neutral DTOs and source IDs; they do
not branch on WAHO.

## C. Source-type matrix

| Source | Source Type | Existing Adapter | Config-Only Possible? | Code Change Required? | Missing Capability | Evidence |
|---|---|---|---|---|---|---|
| WAHO | Paginated HTML | `WahoPaginatedAdapter` | Yes for current configured WAHO instance | No | Live source structure is only partly verified; default selectors remain source-specific | `sources/waho.py`; WAHO unit and 04–09 integration tests |
| TenderDetail | Paginated HTML | Same registry type maps to `WahoPaginatedAdapter` | **Not verified; false under strict criteria** | Yes if detail route or supported parser conventions differ | Detail URL reconstruction is fixed to WAHO route; no URL template setting; ToS/payment is open decision O8 | `_detail_url()` in `sources/waho.py`; O8 in `docs/13-open-decisions.md` |
| All Business Africa | Filtered/faceted HTML | No distinct filtered type; `paginated_html_list` is the nearest adapter | Only conditionally, if a fixed `?status=open` URL and markup/detail routes fit WAHO parser | Yes for a distinct filtered type, configurable filter semantics, or incompatible markup/routes | No `filters` field/model; unknown source type fails; static query may be embedded in `listing_url` | `docs/05-source-adapter-spec.md` calls it filtered/faceted; registry has only paginated type and `wahoo` alias |
| UNGM | Search-form-driven | None | No | Yes, one adapter/capability required | GET/POST form submission, session/CSRF, result pagination/extraction | No browser/form/session interface in `SourceAdapter` or fetcher; unknown type fails |
| Future RSS/Atom | RSS/Atom | None | No | Yes, new adapter and registration | Feed parsing and feed item normalization | Registry lists only WAHO mappings |
| Future JSON API | JSON API | None | No | Yes, new adapter and registration | JSON response mapping/auth/pagination semantics | Registry lists only WAHO mappings |

The matrix does not assert that the named external sites' current markup or terms were checked.
TenderDetail compatibility is hypothetical; no live source was fetched.

### TenderDetail hypothetical configuration test

The existing config could hold a hypothetical `Source` with `source_type="paginated_html_list"`,
distinct `base_url`, `listing_url`, and parser JSON values for `row_selector`,
`title_selector`, `detail_href_pattern`, `next_page_selector`, `deadline_patterns`, and
attachment selectors. `list_new_tenders()` can consume the listing selectors and return the
same `TenderListing` DTO; the generic coordinator and dedup code need no TenderDetail branch.

But this is not enough to satisfy the complete source contract. `get_detail()` and
`get_attachments()` call `_detail_url(tender_id)`, which always joins the ID to
`/tenders/tenders/{id}/list`; there is no configured detail URL template, and the worker uses
`get_attachments()` for stage 07. Detail parsing also expects fixed structural conventions
(`div.trix-content`, immediate label/value children with `<strong>`, hard-coded ID-to-route
construction), some of which selectors/label lists can only partly override. Therefore a
different detail route/layout needs an adapter/code change. This is the exact blocker to
claiming TenderDetail config-only.

## D. WAHO leakage findings

| Location | File | Class/function | Finding | Severity | Why it matters | Recommended owner |
|---|---|---|---|---|---|---|
| Lines containing `build_waho` and `WAHO_SOURCE_TYPE` | `src/tender_intelligence/orchestrator/registry.py` | `AdapterRegistry.default()` | The default supported source type constructs a WAHO-named concrete class; the registry is the intended adapter binding point, but only this one binding exists. It makes no generic paginated implementation available independently of WAHO. | BLOCKING for generic claim | A type name alone does not prove source compatibility; new markup/routes can fail despite the same high-level interaction pattern. | Source adapter owner (Prompt 17/source expansion) |
| `_detail_url()` | `src/tender_intelligence/sources/waho.py` | `WahoPaginatedAdapter._detail_url()` | Hard-coded WAHO path and ID route, notwithstanding configurable `detail_href_pattern`. This is inside the adapter boundary, not a pipeline leak, but prevents some same-type sites being config-only. | BLOCKING for TenderDetail if route differs | Worker stage 07 obtains attachments through this constructed route. | Source adapter owner |
| `_parse_iso()` UTC fallback | `src/tender_intelligence/deadline/extractor.py` | `_parse_iso()` | Generic document deadline extraction defaults a timezone-less machine timestamp to UTC with the comment “WAHO always publishes UTC.” | LEAKED | Other sources may publish local timestamps; global extraction can create a false canonical deadline. | Deadline/source contract owner |
| `_norm_dt()` naive-time normalization | `src/tender_intelligence/dedup/classify.py` | `_norm_dt()` | Treats naive datetimes as UTC and justifies it with WAHO’s UTC behavior. | LEAKED | Another adapter emitting a naive time can be silently compared as UTC. | Source/deadline contract owner |

No WAHO-specific source-name condition was found in generic worker, scheduler, repository,
dedup persistence, document acquisition/processing, notification, or audit/timeline behavior.
The registry's adapter mapping is an expected source-selection responsibility; the concrete
binding to the WAHO adapter is the limitation, not an accidental source-ID branch.

## E. Configuration capability matrix

| Capability | Supported without production code change? | Actual seam / limitation |
|---|---|---|
| Display name | Yes | `Source.name`, unique; source ID is DB-generated integer, not operator-selected |
| Source ID | No, generated | `Source.id` primary key; no external ID field |
| Source type | Partly | `Source.source_type`; only `paginated_html_list` and `wahoo` registered in default registry |
| Base/listing URL | Yes | `Source.base_url`, `listing_url`; WAHO adapter uses them for base/list discovery |
| Detail URL pattern | No, despite a similarly named field | `detail_href_pattern` extracts ID from listing href; `_detail_url()` then reconstructs a fixed WAHO path |
| Pagination | Partly | `next_page_selector`, `max_pages`; follows an anchor href only, no numbered/page-param strategy beyond links |
| Row/title selectors | Yes | `row_selector`, `title_selector` in `parser_config` |
| External ID selector | Partly | Regex `detail_href_pattern` with named `id` group; no arbitrary attribute/text field mapping |
| Deadline selector | Partly | Configurable regex list `deadline_patterns`; parsing expects the adapter's named capture groups and fixed month/timezone tables; no general field mapping |
| Detail title/body selectors | Partly | `detail_title_selectors`, `detail_body_selector`; additional rich text selector `div.trix-content` is hard-coded |
| Attachment selector/rules | Partly | Attachment container selectors, markers, exclusions, extensions, size pattern configurable; all links are still evaluated under fixed extraction rules |
| Enabled/disabled | Yes | `Source.active`; scheduler excludes inactive records |
| Crawl frequency | Yes | `crawl_frequency_minutes`; scheduler applies it |
| Per-source rate limit/timeout/retry | No | `CrawlPolicy` supports these values but default registry injects one shared policy; none are fields on `SourceSpec` or DB `Source` |
| User agent | No per source | `CrawlPolicy.user_agent` exists, injected globally; not configured from each source row |
| Custom headers/cookies | No | `PoliteHttpClient` GET interface accepts only URL; no header/session configuration is passed |
| Language | Partly | `expected_languages` is stored on `Source`, but is not copied into `SourceSpec` or passed to adapter; WAHO language guessing is hard-coded heuristic |
| Source timezone | No | No source timezone field; parser recognizes a fixed abbreviation-to-offset map |
| Parser settings | Partly | JSON `parser_config` is copied to `SourceSpec` and merged over defaults, but only keys explicitly read by `WahoPaginatedAdapter` have effect |
| Filtering | Partly | A static GET query can be embedded in `listing_url`; no typed `filters` property, form/POST filters, or generic query merge seam |
| Source-specific parameters | Partly | Arbitrary JSON can be stored but adapter only consumes its own known keys; no per-source request-policy/auth fields are consumed |
| Authentication | No usable adapter seam | `Source.auth_encrypted` exists and YAML seed encrypts it, but `SourceSpec` omits it and the default adapter/fetcher receives no credentials/headers |
| Created/updated timestamps | Yes | `TimestampMixin` on `Source` |
| Source change audit | Partly | YAML seed logs creation metadata; no source configuration admin/service or API exists to audit ordinary edits yet |

Source configuration today is primarily database-backed, with YAML bootstrap and limited
`TI_DEV_ALERT_EMAIL` environment seeding. It is not currently managed through an Admin UI or
source configuration API. The worker reads persisted `Source` rows; runtime settings reload
does not itself reload an in-memory source registry because the scheduler queries the DB.

## F. Admin UI readiness

The database `Source` model is already the natural shared configuration model and supports
multiple independent rows, JSON parser config, enablement, schedule, and audit timestamps.
The Admin UI should edit those rows and use the same registry/factory. A second source model
is not needed. However, source CRUD/audit service, validation, and fields for per-source crawl
policy/detail URL/auth are absent. Admin UI can expose current fields, but cannot make an
unsupported adapter capability work by itself.

The `Source` table has integer primary key, unique display `name`, `source_type`, URLs,
parser JSON, schedule, active flag, last-run/error fields, encrypted auth storage, languages,
and recipient scope. It is multi-source-capable and does not contain a WAHO-only DB column.
It lacks typed per-source rate limit, timeout, retry policy, headers/session settings, source
timezone, query filters, and a stable external source key. `(source_id, external_id)` is the
tender identity boundary, so same external IDs in distinct sources are independent.

## G. Prompt 13 impact

```text
PROMPT 13 UNBLOCKED
```

Prompt 13 can consume current persisted tender/document bundles from the WAHO pipeline without
changing source architecture. The source expansion gap does not alter the normalized DTO and
database inputs needed by the next AI triage stage. TenderDetail should not be represented as
configuration-only until its detail URL/parser conventions are verified or made configurable.

## H. Security/configuration findings

- Source/listing URLs are operator-configurable without URL/host allow-list validation in the
  source model or `PoliteHttpClient`.
- `PoliteHttpClient.build()` follows redirects; no same-host or private-network redirect guard
  was found. Arbitrary configured hosts and redirect targets therefore need review for SSRF
  before untrusted source configuration is exposed in an Admin UI.
- Document acquisition validates HTTP/HTTPS schemes, bounds redirects and response size, and
  redacts credential-like URL query parameters in logs; it does not enforce public-address or
  configured-host restrictions.
- Source auth is encrypted at rest and not logged by the seed path, but it is not delivered to
  the adapter today. There is no configured header/cookie support in discovery.
- `WahoPaginatedAdapter` logs fetched URLs and some hrefs; URL query credentials may appear
  unless source URLs are constrained/redacted. No API keys or explicit auth headers are
  currently configured by its fetcher.
- Active sources are run when due; no separate startup/config-reload reachability validation
  pass for every active source was found.

These are audit findings only. Prompt 17 security hardening was not implemented.

## I. Test coverage and behavioral proof

Existing coverage inspected:

1. Two configured sources in one worker pass: `test_source_isolation_and_mystery_type_failure`
   runs one good source and one unregistered source; good completes while the other fails.
2. One source failure while another succeeds: same test proves per-source isolation.
3. Source-specific configuration: prior suite tested WAHO parser config; this audit adds the
   focused two-source configuration proof below.
4. Adapter selection: the unknown-type integration case proves unsupported types fail at stage
   04; no direct default-registry registration contract test existed before this audit.
5. Source registration: runtime `AdapterRegistry.register()` exists; there is no dynamic
   plugin discovery or Admin-managed registration. Default production registration is fixed.
6. Duplicate external IDs across different sources: `test_two_sources_with_same_external_id_are_independent` passes.
7. Same external ID within a source: dedup suite covers rerun/idempotency.
8. Enable/disable: scheduler filters inactive rows; orchestrator tests cover inactive/refusal
   behavior, but not a source CRUD API.
9. Source-specific selectors: WAHO unit tests exercise parser settings; the new test uses two
   distinct selector sets.
10. Configuration reload: orchestration test proves runtime Settings are refreshed; source
    rows are queried afresh through the scheduler.

New test `tests/integration/test_source_extensibility.py` constructs two DB `Source` rows with
different paginated listing URLs and selectors, resolves both with the production default
registry, parses each with an offline fetcher into `TenderListing`, then sends each list to
`DedupService` under its own source ID. Result: **1 passed**. It does not exercise stage 07
detail routes, real website compatibility, admin changes, or a full two-source Worker run.

## J. Verification

- Focused behavioral test: `uv run pytest -p no:cacheprovider --basetemp=.pytest-tmp tests/integration/test_source_extensibility.py` — **1 passed**.
- Full suite: `uv run pytest -p no:cacheprovider --basetemp=.pytest-tmp` — **577 passed, 1 failed**, one existing Starlette/AnyIO deprecation warning.
- Failure: `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`; its test input contains replacement characters (`d�p�t`) instead of the accented French text, so expected parsing returned `None`. This was not changed because it is unrelated to the audit and the working tree contains pre-existing user modifications.
- No live TenderDetail, UNGM, or All Business Africa requests were made.
- No production files were changed by this audit. The existing worktree was already dirty; unrelated pre-existing modifications were preserved.

## K. Recommended next action

Before claiming source-type extensibility, make the paginated adapter's detail URL construction
configurable and separate source-generic paginated HTML behavior from WAHO-specific defaults,
then validate a second paginated fixture through stages 04–09. Add typed per-source request
policy and safe URL/redirect restrictions before exposing source configuration to operators.
Treat filtered HTML as configuration-only only when a fixed URL query and current adapter
semantics demonstrably cover the site's behavior. UNGM/RSS/JSON remain new adapter work.

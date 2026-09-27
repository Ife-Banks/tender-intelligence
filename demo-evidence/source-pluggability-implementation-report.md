# Source Pluggability — Implementation Report

**Gate: SOURCE PLUGGABILITY: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION**

This report records an implementation and its test evidence. It does not claim independent verification, and no new live source was added.

## Previous architecture

The repository already had a neutral `SourceAdapter` contract, a `SourceSpec` snapshot containing `source_type`, `listing_url`, and `parser_config`, a registry, and a WAHO-derived paginated parser. The registry selected the `WahoPaginatedAdapter` for the `paginated_html_list` type and the legacy `wahoo` alias. Its listing selectors and detail parser had configurable parts, and source configuration was already persisted in `Source.parser_config` and exposed through the Admin API. However, the registered implementation was still named as WAHO-specific, its detail URL path was fixed in code, it did not apply configured query parameters, and it did not expose the five v1.1 source-type concepts as a shared type definition.

## Implemented architecture

`SourceType` in `interfaces/source.py` now defines the v1.1 strategy values:

| Strategy concept | Stored value | Runnable in this implementation |
|---|---|---|
| Paginated HTML | `paginated_html_list` | Yes |
| Filtered HTML | `filtered_html` | No, requires an implementation/registration |
| Search form | `search_form` | No, requires an implementation/registration |
| RSS/Atom | `rss_atom` | No, requires an implementation/registration |
| JSON API | `json_api` | No, requires an implementation/registration |

The registry dispatches by `source_type`. The paginated HTML type now resolves to `PaginatedHtmlAdapter`; legacy `wahoo` rows continue to resolve to the same implementation. Registry `source_types` continues to mean runnable/registered types, so the Admin API does not advertise strategies that cannot yet run.

The adapter contract remains the existing `SourceAdapter` methods: `list_new_tenders()`, `get_detail(tender_id)`, and `get_attachments(tender_id)`. The adapter receives the source's own listing URL, base URL, and parser configuration through `SourceSpec`. Existing row, title, detail link, next-page, and attachment selectors remain configurable. Added configuration handling includes a detail URL template (`{id}`), configured listing query parameters (preserved across pagination), optional independent detail-link/reference selectors, and an optional attachment link selector. The existing Source model/API also carries crawl frequency, active state, expected languages, and encrypted authentication metadata; these existing fields were not duplicated.

## WAHO migration and safety

The former implementation is now named `PaginatedHtmlAdapter`, with `WahoPaginatedAdapter` retained as a compatibility alias for current imports. Existing WAHO parser defaults remain in place, including its configured selectors, deadline patterns, multilingual heuristics, attachment behavior, and detail URL template. The registry no longer uses source name or host to choose the adapter. The existing polite HTTP client, robots handling, retry/backoff, source error mapping, deduplication, persistence, worker failure isolation, and correlation logging remain in their existing paths. No WAHO deadline logic or the known French deadline parser regression was changed.

## Configuration-only extensibility demonstration

`tests/integration/test_source_extensibility.py::test_two_paginated_sources_with_different_selectors_reach_dedup` constructs two hypothetical source rows with the same `paginated_html_list` source type but different listing URLs, row/title selectors, detail URL templates, and query parameters. The registry builds both with the same adapter class; each returns its own listing IDs/titles and both proceed through the existing dedup service. No source name or domain branch is used. This demonstrates the configuration-only path for the supported paginated HTML strategy; it does not claim arbitrary site markup can be parsed without suitable selector/config support.

## Test evidence

| Check | Result |
|---|---|
| Source extensibility + WAHO discovery/detail focused suite | 45 passed |
| Admin API + orchestration suite | 44 passed, 1 existing Starlette deprecation warning |
| Ruff on `interfaces/source.py`, `sources/waho.py`, `orchestrator/registry.py`, and `test_source_extensibility.py` | Passed |
| Final full suite | 632 passed, 1 failed, 1 existing Starlette deprecation warning in 211.80s |

The sole full-suite failure is `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`: the parser returns `None` for the French date fixture. This is the explicitly out-of-scope Prompt 12.1 regression; it was not changed. The final full-suite command was `.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=.tmp-source-plug-final-full -o addopts='' -q`.

## Remaining limitations

Only paginated HTML is implemented and registered. Filtered HTML, search-form, RSS/Atom, and JSON API are represented as strategy concepts but intentionally have no adapters. Authentication metadata remains supported by existing source persistence/API surfaces, but this change does not add an authentication handler. Detail parsing still uses the existing HTML extraction behavior and may need configuration or additional parser capability for structurally different detail pages. No external source was introduced or live-crawled.

## Final gate

**SOURCE PLUGGABILITY: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION**

# 04a — WAHO Discovery: Verification Notes & Task Report

> Companion record for `prompts/04-waho-discovery.md`. This file is **not** a requirements
> spec (that is `docs/05` / `docs/04`); it records the verification state of the WAHO
> listing structure (prompt 04 §8) and the task's final report (prompt 04 §24), including
> the dry-run boundary (prompt 04 §13) and the scope check (prompt 04 §19).

## 1. WAHO structure — Verified vs Assumed

`prompts/04-waho-discovery.md` §8 requires that selectors, URLs, fields, pagination rules,
and deadline semantics be **distinguished as verified or assumed** rather than fabricated,
and that anything unverified fail safely. The structure encoded in
`src/tender_intelligence/sources/waho.py` `DEFAULT_PARSER_CONFIG` is established from this
repository's **offline fixtures** (`tests/fixtures/sources/waho/`, self-consistent). It has
**not** been re-confirmed against the live site as part of this task.

### Verified (against the project offline fixture set)

The following holds consistently across the fixture pages in this repository:

- Listing rows are card wrappers `div.col-md-6` containing a `div.card`.
- Each card's title link lives at `div.card-header h5 a` with a text title.
- Detail links follow the local path `/tenders/tenders/{id}/list` (numeric `id`).
- Pagination marker: `a[rel="next"]` with an `href` that is relativised via `urljoin`.
- Published dates appear as `Start Date: YYYY-MM-DD HH:MM:SS UTC|GMT`.
- Deadline conventions covered by the fixtures:
  - EN: `Deadline for submission of applications: <day> <Month> <year> at <h>.<mm> <am|pm> <TZ>`.
  - FR: `Date limite de dépôt des candidatures : le <day> <Month> <year> at <h>.<mm> <am|pm> <TZ>`.
  - PT: `Date limite de apresentação das propostas: <day> <Month> <year> ...`.
- The source is trilingual (EN / FR / PT) and deadlines may be absent from a listing.

These statements are grounded in `listing_page_1.html`, `listing_page_2.html`,
`listing_missing_deadline.html`, `listing_french.html`, `listing_portuguese.html`,
`listing_edge_case.html`.

### Assumed (NOT live-verified)

- That the live `data.wahooas.org` tenders listing currently renders the exact structure
  above. No captured live HTML is stored in this repository; fixtures were authored to the
  claimed structure. Confirming against the live site (and refreshing the fixtures from a
  real capture) remains an open task before production reliance — see
  `docs/05-source-adapter-spec.md` §5.3 ("Only behaviours verified against the actual WAHO
  site may be encoded") and §5.5 (O14 legal/ToS duty).
- That the site does not additionally use JS-rendered pagination or alternative row
  wrappers for some listing types.
- That deadlines always appear on the listing row (vs detail-only) for every tender.

## 2. Unknown / Unverified behaviour

- Exact live selectors and pagination markers at the time of writing (see §1, Assumed).
- Full set of timezone abbreviations the live source emits for deadlines. The fixtures
  show `GMT`/`UTC`. `WAT` is mapped as a technical default only — whether the source
  emits it (and with which regional meaning) is not established.
- Whether any listing row can carry an additional title/landing structure not covered by
  `div.card-header h5 a`.
- Live robots.txt contents and any `Retry-After`/rate-limit behaviour of the real host.

## 3. Risks / TODOs

- **Parser drift risk:** if the live structure differs, the adapter reports a structured
  `parser_mismatch` and raises rather than returning corrupt data — safe, but discovery
  halts until `parser_config` is corrected.
- **TODO:** capture live WAHO listing HTML (first page + a paginated second page) and
  store as fixtures; then update `DEFAULT_PARSER_CONFIG` and remove the "Assumed" labels.
- **O14 (scraping / terms-of-service) remains OPEN** (`docs/13-open-decisions.md`). It is
  recorded here, not resolved, per PROJECT_RULES #5 / prompt 04 §19.

## 4. Dry-run boundary (prompt 04 §13)

The higher-layer dry-run (`admin "Test this source"`, a later admin prompt —
`prompts/15-admin-api.md`) is **not** implemented by this task. The adapter contributes
only the contract: `WahoPaginatedAdapter.list_new_tenders()` performs
fetch → parse → return normalized candidates and performs **no** writes to seen-tender
state, **no** business tender persistence, and **no** email. Any dry-run orchestration
lives outside the adapter (prompt 04 §13: "If the dry-run mechanism belongs to a higher
orchestration layer rather than the adapter itself, implement only the adapter contract
required to support it").

## 5. Scope check (prompt 04 §19)

Explicitly **NOT** implemented by this task:

- detail-page discovery; `get_detail()` / `get_attachments()` are `NotImplementedError`
  stubs only; attachment downloading; document processing; OCR; tender persistence;
  database deduplication; update detection; pipeline orchestration; RunHistory
  integration; email; AI; knowledge base; LLM providers; admin UI; additional source
  adapters; RAG/vector search; production deployment; O14 scraping/ToS resolution.

## 6. Implementation mapping (prompt 04 §24)

| Area | Location | Requirement |
| --- | --- | --- |
| Discovery adapter | `src/tender_intelligence/sources/waho.py` | `docs/05` §5.2–§5.3; prompt 04 §2–§4 |
| Pagination | `sources/waho.py` `list_new_tenders` | `docs/05` §5.2; prompt 04 §5 |
| Polite crawling | `sources/policy.py`, `sources/polite.py` | `docs/05` §5.2; prompt 04 §6 |
| Neutral interface | `interfaces/source.py` | `docs/02` §2.9; `docs/05` §5.1 |
| Structured errors | `core/errors.py` (`source_unreachable`, `parser_mismatch`) | `docs/04` §4.3; `docs/05` §5.2 |
| Correlation IDs | `core/correlation.py` | `docs/02` §2.14; PROJECT_RULES #15 |
| Fixtures + tests | `tests/fixtures/sources/waho/`, `tests/unit/test_waho_discovery.py` | `docs/11`; prompt 04 §16–§17 |

## 7. Tests (prompt 04 §24)

Run in the project venv (`uv run`), fully offline (SQLite + fixtures + `MockTransport`):

- `uv run pytest tests/unit/test_waho_discovery.py -v` → **22 passed**
- `uv run pytest` → **96 passed**
- `uv run ruff check .` → **All checks passed**
- `uv run mypy src/tender_intelligence` → **Success: no issues found in 58 source files**

No live WAHO requests are required by the normal suite; no real LLM calls; no email sends.

## 8. Recommended next task

`prompts/05-tender-deduplication.md` — seen-tender state, new/update detection, and
`RunHistory` (stage 3 of the pipeline). The WAHO adapter remains stateless: it keeps
returning candidates only, and dedup is owned by the pipeline against
`(source_id, external_id)`.
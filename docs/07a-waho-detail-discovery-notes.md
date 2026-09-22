# 07a — WAHO Detail Discovery: Verification Notes & Task Report

> Companion record for `prompts/07-document-discovery.md`. This file is **not** a
> requirements spec (that is `docs/05` / `docs/06`—§6.1 maps the discovery stage's output);
> it records the verification state of the WAHO **detail page** structure (prompt 07 §8),
> the exact Prompt 06 persistence seam used (prompt 07 §7), the scope boundary (prompt 07
> §10), and the task's final report (prompt 07 §11).

## 1. WAHO detail structure — Verified vs Assumed

Prompt 07 §8 requires every assumption about the detail page to be recorded as
**assumed / not live-verified** and to fail safely on mismatch. The detail selectors encoded
in `src/tender_intelligence/sources/waho.py` `DEFAULT_PARSER_CONFIG` are established from
this repository's **offline fixtures** (`tests/fixtures/sources/waho/detail_*.html`,
self-consistent, and sharing the documented listing vocabulary from `docs/04a`). They have
**not** been re-confirmed against the live site as part of this task.

### Verified (against the project offline fixture set)

- A tender detail page is reachable at `/tenders/tenders/{id}/list` (same path as the
  listing tickets), derived from the configured listing base via `urljoin`.
- The page renders a `div.card` with a title in `div.card-header` (an `<h*>` heading).
- The body is a rich-text area (`div.card-body div.trix-content`) of
  `<div class="elementToProof"><strong>LABEL: value</strong></div>` blocks. Labels covered:
  EN (`REFERENCE`, `Start Date`, `Deadline for submission of applications`, `Client`,
  `Description`, `Requirements`), FR (`Date limite de dépôt des candidatures`, `Client`,
  `Objet`, `Exigences`), PT (`Referência`, `Entidade Contratante`, `Descrição`, `Requisitos`).
- Attachments are anchors with Bootstrap ``btn`` styling grouped under
  `div.attachments`; a *submission* anchor `/tenders/tenders/{id}/submissions/new`
  also appears on the page and must be excluded.
- Advertised file sizes may appear next to a link (e.g. `(1,2 MB)`, `(3.4 MB)`) with
  units `GB|MB|KB|Go|Mo|Ko`.
- A ZIP attachment is announced as a whole archive (one link, `*.zip`), never with
  per-file links enumerated.
- Deadline semantics reuse the prompt 04 patterns (EN / FR prefixes); a PT detail page
  states the deadline with the EN structural label. Timezones seen: `GMT`.

### Assumed (NOT live-verified)

- That live detail pages use exactly these wrappers/selectors today. Fixtures were authored
  to the claimed structure; confirming against the live site (and refreshing fixtures from a
  real capture) remains an open task before production reliance — per `docs/05` §5.3 and
  §5.5 (O14 legal/ToS duty), unchanged.
- That files are served from `/uploads/…` paths, document extensions cover
  `pdf|docx?|xlsx?|pptx?|zip|rar|7z|od[tsp]|rtf|txt|csv`, and the submission anchor remains
  `/tenders/tenders/{id}/submissions/new`.
- That every detail page carries a recognisable heading; the adapter raises
  `parser_mismatch` if it does not (safe failure, prompt 07 §6).

## 2. Unknown / Unverified behaviour

- Live detail-page markup at the time of writing (§1, Assumed).
- Full set of attachment path prefixes and any non-`trix-content` body layouts.
- Whether the live site announces `application/zip` archives with inner links on the page
  (fixtures: never; the adapter would simply enumerate them as links, it does not download).
- Live robots.txt / `Retry-After` behaviour — carried by `polite.py` unchanged.

## 3. Risks / TODOs

- **Parser drift risk:** detail structure mismatch raises `parser_mismatch` (safe), and
  discovery for that tender halts until `parser_config` is corrected.
- **TODO:** capture one live detail page per language (EN/FR/PT) and update fixtures and
  `DEFAULT_PARSER_CONFIG`, removing the "Assumed" labels.
- **O14 remains OPEN** (`docs/13-open-decisions.md`). Recorded, not resolved, per
  PROJECT_RULES #5 / prompt 07 §10.

## 4. Prompt 06 persistence seam used (prompt 07 §7)

**None at discovery time.** The adapter (`get_detail`, `get_attachments`) is stateless: it
performs no database access, no seen-state query, no `Document` creation, and no
persistence side effect. The Prompt 06 seam `DocumentRepository.create(...)`
(`src/tender_intelligence/db/repositories/documents.py`) is the intended consumer of the
attachments returned here and belongs to **prompt 08 (document acquisition)** — it is not
invoked by this task.

## 5. Scope check (prompt 07 §10)

Explicitly **NOT** implemented by this task:

- Downloading / caching attachment bytes; checksum computation (no checksum is fabricated
  or exposed on the discovery DTOs); ZIP content enumeration — a ZIP is discovered as one
  top-level attachment (`is_zip=True`) and its contents are left to prompt 08/09.
- Dedup rules on duplicate-looking links: a `TenderAttachment` list keeps **every** exposed
  link verbatim (two `Annexe 1.pdf` at different URLs are both returned).
- Speculative extraction of any field not enumerated in the neutral `TenderDetail`.
- AI / OCR / document processing; pipeline orchestration (`worker/main.py` wires nothing);
  RunHistory integration; email; admin UI.
- Any change to the prompt 05 dedup status matrix (see O21 — `verdict_failed` is terminal
  and documented, not changed here).

## 6. Behaviour decisions recorded

- **Deadline / timezone rule (prompt 07 §9.3):** a parsed date is only emitted with a
  timezone offset when the page states a timezone; otherwise `deadline_at` is `None` and the
  original text + stated timezone are preserved in `raw_metadata` (`deadline_raw`,
  `deadline_timezone`). The adapter never invents a zone and never emits a naive timestamp.
- **Source-neutral boundary (prompt 07 §9.14):** detail/attachment results carry only the
  neutral DTOs; no WAHO-specific type leaves the adapter, and unknown file extensions are
  retained with `mime_type=None` rather than dropped or guessed.
- **Assumed selectors are config-driven** (prompt 07 §6) and fail safely via
  `SourceError(parser_mismatch)`.

## 7. Implementation mapping (prompt 07 §11)

| Area | Location | Requirement |
| --- | --- | --- |
| Dict-/attachment DTOs | `interfaces/source.py` (`TenderDetail`, `TenderAttachment`) | `docs/02` §2.9; `docs/05` §5.1 |
| WAHO detail adapter | `sources/waho.py` (`get_detail`, `get_attachments`, `_parse_*`) | prompt 07 §2–§4 |
| Config-driven quirks | `DEFAULT_PARSER_CONFIG` detail/attachment keys | prompt 07 §6 |
| Structured errors | `core/errors.py` (`source_unreachable`, `parser_mismatch`) | prompt 07 §6 |
| Correlation IDs | `core/correlation.py`; `_fetch` error context + INFO records | prompt 07 §6, §9.13 |
| Fixtures + tests | `tests/fixtures/sources/waho/detail_*.html`, `tests/unit/test_waho_detail.py` | prompt 07 §8–§9 |
| Prompt 06 seam (next prompt) | `db/repositories/documents.py` `DocumentRepository.create` | `docs/06` §6.4; prompt 06 |

## 8. Tests (prompt 07 §11)

Run in the project venv (`uv run`), fully offline (fixtures + fake fetcher, no network):

- `uv run pytest tests/unit/test_waho_detail.py -v` → **19 passed**
- `uv run pytest` → **172 passed** at last clean full run; ruff/format/mypy clean
- `uv run ruff check .` → **All checks passed**
- `uv run mypy src/tender_intelligence` → **Success: no issues found in 68 source files**

Notes: `test_request_interval_respected` and
`test_concurrent_runs_do_not_double_create_seen_state` are load/timing-sensitive and flake
only when the whole suite is run under load on this machine; both pass reliably in
isolation and were green at the prompt 06 baseline (pre-existing, unrelated to prompt 07).

## 9. Recommended next task

`prompts/08-document-acquisition.md` — download the discovered attachments, verify their
bytes (checksums), unpack ZIPs where intended, and persist `Document` rows through the
Prompt 06 seam (`DocumentRepository.create`). The WAHO adapter continues to perform pure
metadata discovery.

## 10. Corrections from the behavioural pass and independent verification

Applied after the Prompt 07 behavioural test report (VERDICT: read the 17 §1–§17 checks,
all passing) and the independent verification findings. All changes are behaviour-preserving
for the documented contract; `test_waho_detail.py` / `test_waho_discovery.py` remained green
(**172 passed**, only the pre-existing `test_concurrent_runs_do_not_double_create_seen_state`
flake on full-suite load, unrelated — §8).

1. **Advertised size attribution:** `_advertised_size` now reads only the link's own text and
   the text of its closest ancestors whose *only* document link is that link
   (`_single_link_wrappers`). A group-level size shared by several files is no longer
   mis-attributed to any single file (new fixture `detail_grouped_size.html`; shared `(5 MB)`
   yields `None`). Canonical sizes: PDF `1,2 MB` → `1_258_291` B, ZIP `3,4 MB` → `3_565_158` B.
2. **No naive deadline on any path:** the no-timezone guard moved into the shared
   `_parse_deadline` (used by both listing and detail), so a date-only deadline such as
   `30 November 2026` yields `deadline_at=None` / `deadline_timezone=None` with the original
   text preserved in `deadline_raw` (new fixture `listing_deadline_no_tz.html`).
3. **Interface docstring:** `SourceAdapter.get_attachments` now states that ZIPs/bundles are
   one top-level attachment and are never unpacked at discovery time.
4. **Language heuristic:** removed the ambiguous `"organisation"` French hint (English
   "Organisation" matched it); the EN detail fixture now classifies `"en"` while the FR/PT
   fixtures keep `"fr"`/`"pt"`.
5. **Malformed detail page:** `get_attachments` now raises `parser_mismatch` when the page
   has no recognisable title instead of silently returning `[]`.
6. **Multiple attachment sections:** `_attachment_containers` returns *all*
   `div.attachments`-alike blocks (deduplicated by identity), so documents in a second
   section are enumerated, not silently dropped.
7. **Test rigour:** the boundary test asserts exact DTO identity (`type(...) is …`), size
   assertions use the canonical byte constants, and the ZIP test asserts the exact
   `source_url` set (8 URLs, no substring matching).
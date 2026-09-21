# 04 — Pipeline Specification

> Source of truth: v1.1 §4.2, §5, §6.1. This document specifies the complete processing pipeline: stages, ordering, failure/retry behaviour, idempotency, and observability.

## 4.1 Pipeline diagram

```text
Source Registry
    ↓
Crawler / Watcher Engine
    ↓
New-Listing Detection / Deduplication        (new tenders → process; updates → lower-priority event)
    ↓
Document Fetcher                             (every attachment; per-document tolerance)
    ↓
Document Understanding                       (native text / OCR / tables; multi-language)
    ↓
Stage A — AI Triage                          (cheap filter; admin-configurable rules)
    ↓
Stage B — AI Verdict                         (KB + document bundle → structured verdict)
    ↓
Verdict Formatter                            (exact §8 template)
    ↓
Email Dispatcher                             (attachment planner + mail provider chain)
    ↓
Notification Log / Audit Trail               (statuses, correlation IDs, alerts on breakage)
```

## 4.2 Stage responsibilities

| # | Stage | Responsibility | Failure behaviour |
|---|---|---|---|
| 1 | Source Registry | Configurable list of active sources; validated reachable on config load | Unreachable source → warning log, not a crash |
| 2 | Watcher / Crawler Engine | Runs each source on its schedule; polite crawling (robots.txt, rate limit, UA, backoff) | Source failure → structured log + system alert (dev list), throttled |
| 3 | New-Listing Detection / Dedup | Persist seen tender IDs per source; identify new vs updated | Must survive crashes; re-run must not reprocess/re-notify |
| 4 | Document Fetcher | Download every linked document; unzip ZIPs and recurse; preserve filename/URL; local copy; checksum | Per-document failures recorded; missing docs flagged, not fatal |
| 5 | Document Understanding | Text/OCR/table extraction; language tagging; build reusable document bundle | Per-document failure recorded (`ocr_failed`, etc.); continue with what succeeded |
| 6 | Stage A — Triage | Cheap relevance filter; rules admin-configurable | Triage failure → alert; tender not dropped silently (see 4.6) |
| 7 | Stage B — Verdict | Structured JSON verdict from bundle + KB version | Invalid output retried once → `verdict_failed` + alert + raw notice still emailed |
| 8 | Verdict Formatter | Populate the §8 template exactly | Pure composition; failure → alert |
| 9 | Email Dispatcher | Attachment planner + provider chain + recipient routing | Transient retry w/ backoff; permanent → next provider; all-fail → `pending_retry` + red banner |
| 10 | Notification Log / Audit | Statuses, recipients snapshot, attached-vs-linked, correlation timeline | — |

## 4.3 Failure handling

- **Crashed run safety:** a crashed run must not lose seen-records or block the next scheduled run. `RunHistory` rows are created at run start and completed/errored at run end.
- **No silent failures:** every stage above a configurable threshold raises an alert (see 4.9).
- **Machine-readable error codes** (from v1.1 §6.1, non-exhaustive): `source_unreachable`, `parser_mismatch`, `document_download_failed`, `ocr_failed`, `ai_call_timeout`, `ai_invalid_output`, `budget_exceeded`, `email_send_failed`, `provider_failover`.

## 4.4 Retry behaviour

| Concern | Retry | Source |
|---|---|---|
| Crawl network errors | Polite backoff per §5.2 (no hammering) | v1.1 §5.2 |
| LLM calls | Default **2 retries** with backoff on timeout/rate-limit/5xx, then **fallback profile**; fallback obeys data policy | v1.1 §5.9.3 |
| Invalid AI output | Retried **once** | v1.1 §5.6 |
| Mail transient errors (timeout, 429, 5xx) | Default **2 retries** with exponential backoff | v1.1 §5.10.2 |
| Mail permanent errors (bad creds, invalid payload, quota) | Skip to next provider immediately **and** raise an alert | v1.1 §5.10.2 |
| Whole mail chain down | Message stays `pending_retry`, retried on schedule; health view red banner | v1.1 §5.10.2 |

## 4.5 Partial failures

- Documents: "one broken link shouldn't block the other nine documents on the same tender." Per-document download/extraction status recorded; missing documents flagged in the final email and in the verdict (`incomplete_inputs = true`).
- Stages record **status, not just result** (e.g. "stage 3 succeeded for 4 of 5 attachments, which failed, and why").

## 4.6 Incomplete inputs

- The verdict carries `incomplete_inputs = true` when any attachment failed to download or extract.
- The email says so explicitly (notes footer, §8.1 additions).
- A tender is never silently proceeded past an input failure; the human is told.

## 4.7 Idempotency

- Re-running a crawl must never send a duplicate notification for the same tender.
- Dedupe key: tender + verdict + recipient-set hash; stored and sent as an email header.
- Narrow, logged exception: ambiguous provider timeouts during failover may rarely duplicate → flagged `possible_duplicate`. Accepted trade-off: a rare duplicate is better than a missed tender.

## 4.8 Correlation IDs & run history

- Each tender and each pipeline run gets a unique correlation ID at first detection.
- Every log line, DB row and error is stamped with it.
- Per-tender timeline reconstructable in under a minute: seen → documents fetched → extraction → triage → verdict → email, including which provider/profile was used.
- `RunHistory` independent table: start/end, listings found, new count, error count, failed correlation IDs.

## 4.9 Alerting

- Alerts go to the **dev alert list**, through the same provider chain.
- **Throttled:** one alert when a source/stage becomes unhealthy, one on recovery, reminders at a configurable interval.
- Triggers (configurable thresholds): source fails N runs in a row; parser mismatch; AI call fails outright or invalid output after retry; budget 80%/100%; email fails on every provider; breaker opens; daily health digest.
- Alerts carry the correlation ID + deep link to the timeline.
- **Chain-wide failure fallback:** if the whole mail chain is down, the persisted **red banner in the health view** is the fallback signal (email may itself fail).
- The system refuses to deactivate/delete the **last active dev recipient**; the first dev recipient is seeded from an environment variable.

## 4.10 Admin-independent worker behaviour

- Worker re-reads configuration at the start of every run; never caches across runs.
- If the admin app is down, the worker keeps running on the last saved configuration.
- Admin actions are dry-runs only (no inline crawls, nothing written to seen-tenders, nothing sent to business recipients).

## 4.11 Configuration reload behaviour

- On startup and on each config reload, every active source is validated as reachable; unreachable sources log a warning (not a crash).
- Profile/role changes take effect on the **next call**, no redeploy.
- All configuration changes are audit-logged in `ConfigChangeLog` (never secret values).
- Test Mode, triage rules, urgency window, alert thresholds and monthly AI budget are all reloadable settings.

## 4.12 Oversized-bundle handling (Stage B)

- If tender bundle + knowledge base exceeds the profile's `context_window_tokens` budget (token-budget guard):
  1. Summarise each document first (map step).
  2. Run the verdict on the summaries (reduce step).
  3. Record that this happened (verdict metadata/audit).
- Admin shows a KB token-budget warning when exceeding the configurable share of the window (default 40% [PROPOSED]).

## 4.13 Budget guard (Stage B)

- Usage logged per call (`LLMCall`: tokens, latency, est cost).
- Configurable monthly AI budget: alert at 80%; pause Stage B at 100% (triage continues; tenders queue `awaiting_budget`).
- Actual budget value = open business decision (`docs/13-open-decisions.md`).

## 4.14 Update events (tender updates)

- Updates to previously-seen tenders (new addendum, extended deadline) are distinct, lower-priority events.
- Update email follows §8.2 template: what changed, current deadline, whether this changes the verdict (re-run verdict only if the change is material).
- Attachments: the new/changed documents only; the earlier assessment is referenced by date.

## 4.15 Historical note

v1.0 described the pipeline as "six stages" with no Triage/Stage-A separation detail beyond recommendation, no Test Mode, no provider chain, and alerting to OPEX via email. v1.1 separates two-stage AI, adds Test Mode, dev-recipient routing and the mail chain, and revises alerting from "email OPEX" to "dev list". v1.1 governs.
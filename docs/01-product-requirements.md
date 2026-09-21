# 01 — Product Requirements

> Source of truth: `source-docs/tender-intelligence-spec-v1.1.md`. Historical (v1.0) material is labelled `[v1.0 — historical]` where it is still useful. All classifications below derive from the source text. Where classification is unclear, the requirement is marked `NEEDS_DECISION` (do not invent the answer — see `docs/13-open-decisions.md`).

**Classification key**

| Mark | Meaning |
|---|---|
| **MUST** | Explicitly required by the source. |
| **SHOULD** | Recommended by the source ("should", "recommended"); including items marked [PROPOSED] that the source treats as the intended design but not externally confirmed. |
| **OPTIONAL** | Possible / "may", genuinely optional. |
| **DEFERRED** | Explicitly postponed by the source. |
| **NEEDS_DECISION** | Open business/technical question; the coding AI must not decide. |
| **[PROPOSED]** / **[DEFERRED]** | Status labels used in v1.1 itself. |

---

## Source management

| Req | Classification | Notes |
|---|---|---|
| Sources must not be hardcoded; each source is a configurable record (name, base_url/listing_url, source_type, crawl_frequency, language(s), optional auth, active flag, parser_config, optional recipient_scope). | **MUST** | v1.1 §5.1; v1.0 §5.1. |
| Sources must be addable, editable, and disable-able **without a code deployment** (config file minimum; admin screen is the target). | **MUST** | v1.1 §5.1, §5.8. |
| On startup and on each config reload, validate every active source is reachable and log a warning (not a crash) for any that isn't. | **MUST** | v1.1 §5.1. |
| Support these source types from day one: Paginated HTML list, Filtered/faceted HTML list, Search-form-driven, RSS/Atom feed, JSON API. | **MUST** (adapter capability per type), sites beyond WAHO deferred by phase | v1.1 §5.1. Only WAHO is due in Phase 1; the other named sites are Phase 3. |
| Provide a `SourceAdapter` interface (`list_new_tenders()`, `get_detail(tender_id)`, `get_attachments(tender_id)`) with one adapter per `source_type`. | **MUST** | v1.1 §5.1 design note. |
| Adding a new site of a supported type requires only a new config entry, not new code. | **MUST** | v1.1 §5.1. |
| Developer side: check each site's terms of service / scraping restrictions before building an adapter; prefer official APIs/feeds. | **SHOULD** | v1.1 §12.3 #11; v1.0 open questions. |

## Tender discovery

| Req | Classification | Notes |
|---|---|---|
| A watcher runs each active source on its configured schedule. | **MUST** | v1.1 §5.2. |
| Crawler must be polite: respect robots.txt, rate-limit, real user agent, back off on errors. | **MUST** | v1.1 §5.2. |
| Detect **new** listings only, against a persistent per-source seen-record; re-runs must not reprocess/re-notify the same tender. | **MUST** | v1.1 §5.2, §6 NFR idempotency. |
| Detect **updates** to previously-seen tenders (new addendum, extended deadline) as distinct, lower-priority events. | **MUST** | v1.1 §5.2, §8.2. |
| On source-level failure, log with enough detail to debug and raise a system alert (dev list). | **MUST** | v1.1 §5.2, §5.10.4. v1.0 said "notify OPEX via email" — revised in v1.1 to dev list (§5.2 note). |

## Deduplication / update detection

| Req | Classification | Notes |
|---|---|---|
| Re-running a crawl must never send a duplicate notification for the same tender (narrow, logged exception for ambiguous provider timeouts — `possible_duplicate`). | **MUST** | v1.1 §6 NFR, §5.10.2(5), §11. |
| A crashed run must not lose track of what's already been seen or block the next scheduled run. | **MUST** | v1.1 §6 NFR reliability. |
| Store a checksum per document so duplicate attachments aren't refetched or re-sent. | **MUST** | v1.1 §5.3. |

## Document acquisition

| Req | Classification | Notes |
|---|---|---|
| For every new/updated tender, download **every** linked document (RFPs, TORs, annexes, EOI forms, procurement plans, ZIP archives — unzip and recurse). | **MUST** | v1.1 §5.3. |
| Preserve original filenames and source URLs; store a local/cloud copy. | **MUST** | v1.1 §5.3. |
| Record file type, size, language (best guess), checksum. | **MUST** | v1.1 §5.3. |
| Handle failures per-document (one broken link must not block the others); flag missing documents in the verdict/email rather than failing silently. | **MUST** | v1.1 §5.3, §6.1. |

## Document understanding

| Req | Classification | Notes |
|---|---|---|
| Support native PDF text extraction. | **MUST** | v1.1 §5.4. |
| Support scanned/image PDFs via OCR; optionally use a vision-capable LLM profile if it tests better. | **MUST** (OCR), vision option **SHOULD**-conditional | v1.1 §5.4, §5.9.3. |
| Support DOCX direct text extraction. | **MUST** | v1.1 §5.4. |
| Support multi-language content (French, English, Portuguese); preserve/tag language or translate to a working language before assessment. | **MUST** | v1.1 §5.4, §2.6. |
| Preserve table structure that matters for the verdict (deadlines, eligibility, evaluation matrices). | **MUST** | v1.1 §5.4. |
| Output one structured "document bundle" per tender (plain text + metadata per document), reusable (no re-OCR on re-run). | **MUST** | v1.1 §5.4. |

## AI triage (Stage A)

| Req | Classification | Notes |
|---|---|---|
| A cheap triage pass filters obviously irrelevant notices before a full AI call. | **MUST** (as design), may start rule/keyword-based | v1.1 §5.6. |
| Triage rules are admin-configurable: include/exclude keywords, target sectors/regions, optional minimum contract value, relevance threshold. | **MUST** | v1.1 §5.6, §5.8. |
| Defaults come from the "sectors, regions and contract types" section of the knowledge base. | **SHOULD** | v1.1 §5.6. |
| Actual target profile (sectors/regions/value) is an open question for OPEX. | **NEEDS_DECISION** | v1.1 §12.3 #5. |

## AI verdict (Stage B)

| Req | Classification | Notes |
|---|---|---|
| Produce structured verdict with: Background; Requirements (incl. exact deadline, date + time + timezone); Why we can/cannot apply (gap analysis naming specific gaps); verdict/recommendation (APPLY / DO NOT APPLY / APPLY WITH CONDITIONS); confidence score; deadline urgency flag. | **MUST** | v1.1 §5.6, §8.1. |
| Two-stage processing: Stage A cheap triage before Stage B full assessment. | **MUST** | v1.1 §5.6. |
| Evidence-cited matches: every "meets requirement" claim cites the knowledge-base section it relies on; uncited claims downgraded to "unverified". | **SHOULD** ([PROPOSED]) | v1.1 §5.6 quality rules. |
| "No evidence on file" wording for gaps (not "OPEX does not have X"). | **SHOULD** ([PROPOSED]) | v1.1 §5.6 quality rules. |
| Structured JSON output, validated; invalid output retried once; if still failing → `verdict_failed`, alert raised, raw notice still emailed with an "automatic assessment unavailable" banner. | **MUST** | v1.1 §5.6, §11. |
| `incomplete_inputs = true` recorded when any attachment failed to download/extract; email says so. | **MUST** | v1.1 §5.6, §8.1, §6.1. |
| Oversized bundles: summarise per document (map) then run verdict on summaries (reduce); record that this happened. | **MUST** | v1.1 §5.6. |
| Store deadlines as UTC plus original timezone string; emails show deadline in Nigerian time (WAT) and the timezone stated in the notice. | **MUST** | v1.1 §5.6. |
| Record provider profile, model, knowledge-base version and prompt version per verdict. | **MUST** | v1.1 §5.6, §7, §0.1. |

## Knowledge base

| Req | Classification | Notes |
|---|---|---|
| v1: one structured, versioned document read directly by the verdict engine; no embeddings, no vector store, no retrieval step. | **MUST** | v1.1 §5.5 (decision D5), §3. |
| Editable without a deployment (admin app: upload `.docx`/`.pdf`/`.md`/`.txt` or paste/edit text). | **MUST** | v1.1 §5.5. |
| Versioned: every save creates an immutable version with content hash and timestamp; every verdict records the version used. | **MUST** | v1.1 §5.5, §7. |
| Structured layout with documented sections (company profile; capabilities; past projects; certifications; key staff; partners; known gaps; sectors/regions/contract types to pursue or skip). | **MUST** (document exists), exact template **DEFERRED** | v1.1 §5.5, Appendix A. |
| Token-budget guard: count KB tokens vs profile `context_window_tokens`; warn in admin when exceeding configurable share (default 40% [PROPOSED]). | **SHOULD** ([PROPOSED] default) | v1.1 §5.5. |
| Access-controlled (may contain sensitive company/financial info). | **MUST** | v1.1 §5.5, §6.2, §10. |
| Phase-2 upgrade path: per-file metadata + retrieval store via `get_knowledge_context()`; embed model as its own role. | **DEFERRED** (phase 2/5) | v1.1 §5.5. |
| Keep `get_knowledge_context(tender_bundle)` interface stable. | **SHOULD** | v1.1 §5.5 (design guidance). |

## Notifications

| Req | Classification | Notes |
|---|---|---|
| One email per new/updated tender that passes triage, even when the verdict is "do not apply". | **MUST** | v1.1 §5.7. |
| Email body follows the §8 template exactly. | **MUST** | v1.1 §5.7, §8. |
| Every supporting document attached, or reliably linked where attaching is impossible. | **MUST** | v1.1 §5.7; v1.0 had a single ~20 MB threshold — superseded by the capability-aware attachment planner. |
| Attachment planner picks first provider whose capabilities fit the whole set; otherwise attach what fits on best provider and link the rest via secure expiring links listed by filename. | **MUST** | v1.1 §5.7, §5.10.2. |
| Record exactly what was attached vs linked in the notification log. | **MUST** | v1.1 §5.7, §7. |
| Secure download links: signed, expiring URLs served from the document archive; default expiry 14 days (configurable). | **MUST** (secure+expiring), default **SHOULD** ([PROPOSED]) | v1.1 §5.7. |
| Recipients come from configured recipient lists, not hardcoded addresses. | **MUST** | v1.1 §5.7. |
| Deliverability basics: proper From address, plain-text + HTML versions. | **MUST** | v1.1 §5.7. |
| SPF/DKIM on any custom-domain sender. | **DEFERRED** | v1.1 §5.7, §12.2. |
| Other channels (Slack/Teams/SMS): out of scope for v1; design layer for later additions. | **MUST** (design for extensibility), channel delivery **DEFERRED** | v1.1 §3. |

## Test Mode

| Req | Classification | Notes |
|---|---|---|
| Test Mode is a global switch; when ON all tender emails go only to the dev list, subject prefixed `[TEST]`. | **MUST** (design), switch [PROPOSED] | v1.1 §5.12. |
| Default: **ON until go-live**; turning it off is an explicit, audit-logged admin action. | **MUST** (default), [PROPOSED] | v1.1 §5.12. |
| "Test this source" fetches and displays what it would find without recording tenders as seen and without emailing anyone. | **MUST** | v1.1 §5.12, §4.1. |
| While any unapproved profile is assigned to the verdict role, the knowledge base sent to it must be the placeholder/non-sensitive version. | **MUST** | v1.1 §5.12, §5.9.4. |

## Administration

| Req | Classification | Notes |
|---|---|---|
| Admin web app (thin) over shared database, for non-technical operators. | **MUST** | v1.1 §4.1, §5.11. |
| Screens: Health dashboard, Sources, Tenders (+ per-tender timeline), Knowledge base, LLM providers, Recipients, Mail providers, Triage & urgency, Settings, Audit log. | **MUST** (as specified screens) | v1.1 §5.11. |
| Admin app must not run crawls inline; test actions are dry runs. | **MUST** | v1.1 §4.1. |
| Authenticated users only, with at least Admin and Viewer roles; KB and secrets never exposed to Viewers. | **SHOULD** ([PROPOSED]) | v1.1 §5.11. |
| Exact login method and who gets access is an open question. | **NEEDS_DECISION** | v1.1 §12.3 #9. |

## Audit / history

| Req | Classification | Notes |
|---|---|---|
| Every tender seen, document fetched, verdict generated, and email sent logged with timestamps. | **MUST** | v1.1 §6 NFR, §6.1. |
| Correlation ID per tender and per pipeline run; stamped on every log line, DB row and error. | **MUST** | v1.1 §6.1. |
| Every pipeline stage records a status, not just a result (e.g. "4 of 5 attachments extracted"). | **MUST** | v1.1 §6.1. |
| Structured logging (JSON or equivalent): timestamp, correlation ID, source, stage, status, machine-readable error code/category. | **MUST** | v1.1 §6.1. |
| Run-history table/view independent of tender records. | **MUST** | v1.1 §6.1, §7. |
| Retention minimum for run history, per-tender stage logs, verdicts (e.g. 12 months). | **MUST** (defined minimum), period configurable | v1.1 §6.1. |
| Single health view per source: last successful run, last failure + category, rolling 7/30-day counts. | **MUST** | v1.1 §6.1. |

## Configuration

| Req | Classification | Notes |
|---|---|---|
| Reconfigurable without code changes: add/disable source, change crawl frequency, LLM profiles + role assignment, recipients, mail providers + order + sender identity, Test Mode, KB content, triage rules/threshold, urgency window, alert thresholds, monthly AI budget. | **MUST** | v1.1 §5.8. |
| Secrets write-only in UI; never returned in API responses or logs. | **MUST** | v1.1 §5.8, §5.9.1, §6.2. |
| Config change history (who, when, which fields — never secret values). | **MUST** | v1.1 §7 (ConfigChangeLog), §5.9.5. |

## Failure handling

| Req | Classification | Notes |
|---|---|---|
| A crashed run must not lose seen-records or block the next scheduled run. | **MUST** | v1.1 §6 NFR. |
| Fail loudly: any stage failure above a configurable threshold triggers alerting. | **MUST** | v1.1 §6.1, §5.10.4. |
| Retry with backoff: LLM calls default 2 retries then fallback; mail transient errors default 2 retries; provider chain failover. | **MUST** (defaults may differ) | v1.1 §5.9.3, §5.10.2. |
| Whole mail chain down: message stays queued `pending_retry`, retried on schedule, health view red banner. | **MUST** | v1.1 §5.10.2(6). |
| Failed verdicts: `verdict_failed` status, alert raised, raw notice still emailed with banner. | **MUST** | v1.1 §5.6. |
| Waiting on budget: tenders queue as `awaiting_budget` (Stage B paused at 100% budget; triage continues). | **MUST** | v1.1 §5.9.6. |

## Alerts

| Req | Classification | Notes |
|---|---|---|
| System alerts go to the dev alert list through the same provider chain. | **MUST** | v1.1 §5.10.4 (decision D7). |
| Throttling: one alert when unhealthy, one on recovery, reminders at configurable interval. | **MUST** | v1.1 §5.10.4. |
| Triggers (configurable thresholds): source fails N runs in a row; parser mismatch; AI call failure or invalid output after retry; budget 80%/100%; email fails on every provider; breaker opens; daily health digest. | **MUST** | v1.1 §5.10.4. |
| Alerts carry correlation ID and deep link to the timeline in admin. | **MUST** | v1.1 §5.10.4. |
| Refuse to deactivate/delete the last active dev recipient. | **MUST** | v1.1 §5.10.1. |
| First dev recipient seeded from an environment variable. | **MUST** | v1.1 §5.10.1. |
| Optional business "operations" alert list. | **OPTIONAL** | v1.1 §5.10.1; open question §12.3 #2. |

## Provider management (LLM)

| Req | Classification | Notes |
|---|---|---|
| LLM provider profiles: name, base_url, api_key (encrypted/write-only/never logged), model, context_window_tokens, max_output_tokens, temperature, timeout_seconds, extra_headers, supports_json, supports_vision, cost_per_1k_input/output, approved_for_company_docs (default false), active. | **MUST** | v1.1 §5.9.1. |
| Role assignment (triage / verdict / embeddings / vision_ocr) maps roles to profiles; each role can name a fallback profile. | **MUST** (triage/verdict), embeddings/vision_ocr OPTIONAL or DEFERRED | v1.1 §5.9.3. |
| Standardise on the OpenAI-compatible chat-completions shape behind an `LLMClient` interface. | **SHOULD** ([PROPOSED], decision D4) | v1.1 §5.9.2. |
| Retry/fallback: on timeout, rate-limit or 5xx, retry with backoff (default 2), then fail over to fallback. | **MUST** | v1.1 §5.9.3. |
| Fallback subject to same data policy as primary (never fail over to unapproved profile for KB calls). | **MUST** | v1.1 §5.9.3, §5.9.4. |
| `approved_for_company_docs` gate: system refuses to send KB content to unapproved profiles. | **MUST** | v1.1 §5.9.4, §11. |
| "Test connection" button: one-line prompt, latency, resolved model, error; checks JSON-mode/vision where possible. | **SHOULD** | v1.1 §5.9.5. |
| Usage tracking: log tokens in/out, latency, estimated cost per call. | **MUST** | v1.1 §5.9.6, §7 (LLMCall). |
| Monthly AI budget: alert at 80%, pause Stage B at 100% (triage continues; tenders queue `awaiting_budget`). | **SHOULD** ([PROPOSED] budget mechanism; value open) | v1.1 §5.9.6, §12.3 #7. |
| Actual monthly AI budget value is an open question for OPEX. | **NEEDS_DECISION** | v1.1 §12.3 #7. |

## Provider management (mail)

| Req | Classification | Notes |
|---|---|---|
| Mail provider chain of 2–3 services with failover; Sendlib first. | **MUST** | v1.1 §5.10.2 (decision D8). |
| Per-provider config: type, credentials (encrypted, write-only), from_address, from_name, reply_to, priority, active, capabilities block. | **MUST** | v1.1 §5.10.2. |
| Failover: transient errors retried w/ backoff; permanent errors skip to next provider and raise alert; every attempt recorded. | **MUST** | v1.1 §5.10.2. |
| Circuit breaker: after N consecutive failures, skip provider for cooldown + raise alert; test-email or probe closes breaker. | **MUST** | v1.1 §5.10.2. |
| Dedupe key (tender + verdict + recipient-set hash) stored and sent as email header; ambiguous-timeout duplicates accepted, flagged `possible_duplicate`. | **MUST** | v1.1 §5.10.2. |
| Sendlib specifics (limits, OAuth via Google, dedicated mailbox) recorded as research notes; re-verify before building adapter. | **SHOULD** | v1.1 §5.10.3, Appendix B. |
| Providers 2 and 3. | **NEEDS_DECISION** (TBD) | v1.1 §12.3 #14. |
| Dedicated sender mailbox + OPEX authorisation for the mailbox used by Sendlib. | **NEEDS_DECISION** | v1.1 §12.3 #3. |
| Sendlib Free vs Pro. | **NEEDS_DECISION** | v1.1 §12.3 #4. |

## Security

(Recurring security requirements; consolidated in `docs/10-security-spec.md`.)

| Req | Classification | Notes |
|---|---|---|
| Secrets encrypted at rest; master key outside the database. | **MUST** | v1.1 §6.2. |
| Write-only secrets; never returned by UI/API; never logged (metadata only). | **MUST** | v1.1 §6.2, §5.8. |
| KB access-controlled. | **MUST** | v1.1 §6.2. |
| Third-party data handlers listed with owners in a security note before go-live. | **MUST** | v1.1 §6.2. |

---

## Requirements summary counts (informational)

- `MUST`: the core functional surface above (~55 rows).
- `SHOULD`: ~10 rows, mostly source-marked [PROPOSED].
- `OPTIONAL`: 2 rows.
- `DEFERRED`: partitions of RAG, SPF/DKIM, vision_ocr, embeddings role.
- `NEEDS_DECISION`: mail chain TBD, recipients, sender mailbox, budget, triage target profile, admin access, KB/storage location, production LLM data approval, TenderDetail paid tiers, plus items in `docs/13-open-decisions.md`.

## Explicitly non-requirements (do not implement)

- Application submission.
- Slack/Teams/SMS delivery in v1.
- RAG / vector store / embeddings in v1.
- Multi-tenant hosting.
- Anything beyond the named v1.1 scope (see `docs/00-project-brief.md`).
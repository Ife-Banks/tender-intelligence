# 00 — Project Brief

> **Source of truth:** `source-docs/tender-intelligence-spec-v1.1.md` (authoritative).
> Historical context only: `source-docs/tender-intelligence-spec-v1.0.md`.
> Where the two conflict, v1.1 wins. See §13-open-decisions for unresolved items.

## What the system does

Tender Intelligence is a continuous tender-monitoring and auto-notification system for **OPEX Consulting Limited / RegTech365**. It watches a configurable list of tender sources, detects new (and updated) tender postings, downloads every attached document, extracts and understands their content, and — using a versioned record of what OPEX has actually done and is certified for — produces a structured go/no-go verdict (APPLY / DO NOT APPLY / APPLY WITH CONDITIONS). That verdict, with all supporting documents attached or securely linked, is emailed to the configured recipients before the deadline is "a rounding error away."

The system does **not** submit applications. It notifies humans, who apply.

## Problem being solved

On 18 September 2026, OPEX discovered a WAHO tender for a regional pharmaceutical traceability system whose EOI deadline was the same day. The notice had been live for weeks. Nobody had been watching the source, and there was no fast way to tell "worth our time" from "must-apply". This is not a one-off: OPEX/RegTech365 operate in GRC, ITGRC, fintech regulatory technology and compliance platforms, where high-value opportunities are published across scattered, differently-structured bodies (WAHO, UN agencies via UNGM, TenderDetail, All Business Africa, and more). A human checking five to ten sites a day and judging fit is not sustainable.

## Primary users

- **OPEX / RegTech365 business staff** — receive tender assessment emails and act on them.
- **Builders/maintainers (developers)** — receive system alerts via a dedicated dev alert list; the "who" and exact addresses are open decisions (`docs/13-open-decisions.md`).
- **Non-technical operators** — manage configuration through the admin web app.

## Major system components

| Component | What it is | Responsibilities |
|---|---|---|
| **Worker (headless)** | Scheduled process(es), no UI | Crawl sources, detect new/updated tenders, fetch documents, extract text, run triage and verdict, send email, write logs and alerts |
| **Admin web app** | Thin web UI + API over the same database | Manage sources, LLM provider profiles, recipients, mail providers, the knowledge base, triage/urgency thresholds; view health, run history, per-tender timelines; audit log |
| **Shared database** | Relational DB + document storage | Holds configuration *and* operational data; changes in admin are picked up by the worker on its next run — no redeploy, no restart |

## End-to-end workflow

```text
Source Registry
    ↓
Crawler / Watcher Engine
    ↓
New-Listing Detection / Deduplication (and update detection)
    ↓
Document Fetcher (every attachment; per-document failure tolerance)
    ↓
Document Understanding (text/OCR/tables, multi-language)
    ↓
Stage A — AI Triage (cheap relevance filter, admin-configurable)
    ↓
Stage B — AI Verdict (with versioned knowledge base)
    ↓
Verdict Formatter (exact §8 template)
    ↓
Email Dispatcher (attachment planner + mail provider chain)
    ↓
Notification Log / Audit Trail / Alerts
```

## v1.1 scope

- Headless worker core + thin admin web app (decision D1).
- Configurable source registry; WAHO first; source adapter abstraction.
- Document fetch + understanding (PDF/DOCX/ZIP, OCR, multi-language, tables).
- Two-stage AI processing: Stage A triage, Stage B verdict.
- v1 knowledge base = one structured, versioned document read directly by the verdict engine (decision D5). **No RAG in v1.**
- Email notifications via a mail provider chain (Sendlib first) with an attachment planner and secure expiring links.
- Separate tender-recipient and dev-alert-recipient lists (decisions D6, D7).
- Test Mode (ON by default) and per-provider `approved_for_company_docs` data policy (D9, D10 proposed).
- Correlation IDs, structured logging, per-stage status, run history, audit log, health view, alerting.

## Explicitly out-of-scope / deferred functionality

- Application submission (never).
- Slack/Teams/SMS notifications for v1 — email only, but notification layer must allow later channels without rework.
- **RAG / vector database / embeddings pipeline** — not part of v1; planned phase-2/5 upgrade.
- Multi-tenant hosting.
- SPF/DKIM / sending-domain DNS setup (deferred).
- Exact production LLM model name and credentials from OPEX (deferred).
- Knowledge-base document template (deferred).
- Embeddings role and vision-OCR role (only if testing justifies).

## First implementation milestone

**Phase 0** — repository foundations, database schema, encrypted secret storage, structured logging with correlation IDs, `MailProvider` interface with the Sendlib adapter and attachment planner, dev alert recipient seeded from an environment variable, **Test Mode ON**. Goal: any message the system sends can be sent safely and traced.

Followed by **Phase 1** — WAHO end-to-end in Test Mode, no AI yet. See `docs/12-deployment.md` and `README.md` for the full sequence.

## Important constraints

- The worker **re-reads configuration at the start of every run**; never caches settings across runs.
- The admin app **does not run crawls inline**; its "Test this source" / "Test connection" actions are **dry runs** that write nothing to seen-tenders and send nothing to business recipients.
- If the admin app is down, the worker keeps running on the last saved configuration.
- The system must **never silently fail**; breakage raises alerts to the dev list.
- Secrets are **write-only** and never logged or returned by APIs.
- Knowledge-base content is only sent to LLM profiles flagged `approved_for_company_docs = true`.
- AI output is structured and validated; invalid output is retried once, then treated as `verdict_failed` with an alert and the raw notice still emailed.
- Sources, LLM providers, mail providers, recipients, triage thresholds and Test Mode are all **reconfigurable without code changes or redeploys**.

## Source-of-truth rules

1. `tender-intelligence-spec-v1.1.md` is authoritative.
2. `tender-intelligence-spec-v1.0.md` is historical/contextual only.
3. Conflicts are recorded in `docs/13-open-decisions.md`, not silently merged.
4. Business requirements must not be invented. Unspecified values are left open.
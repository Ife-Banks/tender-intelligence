# 12 — Deployment

> Source of truth: v1.1 §9, §10, §12. Cloud vendors are **proposed choices** only where noted; the source commits to a relational DB + object storage/filesystem for documents, a headless worker, and a thin admin app, and to phases that prove the pipeline before generalising.

## 12.1 Deployment model implied by the source

- **Headless worker** as scheduled process(es) — a cron-style schedule or lightweight job queue is explicitly acceptable at this scale (v1.1 §9). No heavy orchestration required.
- **Admin web app** — thin UI + API over the same database. Can use a different stack than the worker; only db + schema must be shared.
- **Relational database** for configuration and operational data. *[Proposed]*: Postgres ("Postgres is fine" §4.1).
- **Document storage** — local copy of every tender document. *[Proposed]*: object storage or a well-organised filesystem/bucket (v1.1 §9). Physical location = open decision (`docs/13-open-decisions.md` #8).
- **Secrets** — master key outside the DB (env var or secrets manager); provider secrets encrypted in the DB.
- **Scheduling** — per-source `crawl_frequency`; test sends/pipeline probes on demand from admin (dry-run).

## 12.2 Deployment areas

| Area | Requirement | Notes |
|---|---|---|
| Worker deployment | Scheduled, restart-safe, no UI | Must not lose seen-records on crash; `pending_retry` outbox survives restarts |
| Admin deployment | Thin app + API | Optional for worker operation |
| Database | Shared; migrations not destructive | PROJECT_RULES #14 |
| Document storage | Copies + signed-link serving | Never rely on source URLs staying valid |
| Secrets | Encrypted at rest; master key external | Env var or secrets manager |
| Scheduling | Per-source schedules; pipeline queues after failures | Flush backlog throttled |
| Logs | Structured (JSON or equivalent), correlation IDs | Queryable by error code |
| Monitoring | Health view + dev-list alerts; red banner when queue stuck | §6.1 |
| Backups | Database + document archive + secrets config | Retention of audit data (e.g. 12 months) |
| Configuration | Env-seeded bootstrap; config file→admin app | Worker re-reads each run |
| Environment separation | Test vs prod via **Test Mode** switch | Model below |

## 12.3 Environment separation & Test Mode defaults

- **Test Mode ON by default** until go-live. ON ⇒ tender emails only to dev list, subject prefixed `[TEST]`.
- Off ⇒ explicit, audit-logged admin action (go-live gate).
- Environments differ by configuration (recipients, profiles, flag values) rather than separate code paths. *[Proposed]*
- Test providers never receive real KB content; verified by `approved_for_company_docs` gate.

## 12.4 Phased delivery sequence (v1.1 §10; do not reorder without a documented reason)

- **Phase 0 — Foundations:** repo, DB schema, encrypted secrets, structured logging + correlation IDs, `MailProvider` + Sendlib adapter + attachment planner, env-seeded dev alert recipient, Test Mode ON.
- **Phase 1 — WAHO end-to-end (Test Mode, no AI):** crawl → detect new → fetch docs → extract → templated email with attachments to dev list. Check how Sendlib Free/Pro limits affect real WAHO attachments.
- **Phase 2 — AI verdict:** provider profiles + role assignment (test provider), versioned KB (placeholder content while unapproved), two-stage triage/verdict + quality rules, §8 template email, second mail provider + failover chain, first admin screens (LLM providers, recipients, KB).
- **Phase 3 — Generalize:** `SourceAdapter` abstraction + TenderDetail/All Business Africa adapters; prove UNGM is one new adapter.
- **Phase 4 — Configuration & hardening:** source config into admin, remaining screens, alert manager, audit/health, budget guard, load-test high-volume source triage; **switch to OPEX's paid model + `approved_for_company_docs` + real KB + Test Mode off**.
- **Phase 5 (later):** retrieval upgrade only if KB outgrows the context budget.

## 12.5 Confirmed vs proposed suppliers

- **Confirmed:** relational DB; headless worker; thin admin; document archive; version-controlled bootstrap config; cron/light job queue; "Postgres is fine"; object storage *or* filesystem.
- **Proposed:** any specific cloud vendor, serverless, container orchestration, CI/CD tooling — none are mandated; choose and document.

## 12.6 Operational notes (from source)

- Backlog flush must be throttled to Sendlib's rate limits (30 req/min Free / 300 Pro).
- Gmail's own limits still apply through Sendlib.
- If a conventional provider needing a verified domain is added as provider 2/3, SPF/DKIM DNS setup is then required (deferred, `docs/13-open-decisions.md`).
- Our own notification log is the source of truth — never third-party provider logs.
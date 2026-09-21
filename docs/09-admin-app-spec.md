# 09 — Admin Application Specification

> Source of truth: v1.1 §4.1, §5.11, §5.12. The admin app is a **thin web UI + API over the shared database** so non-technical staff can safely operate the system. It must never run crawls inline; worker/admin independence rules apply (§02, §04).

## 9.1 Role & principles

- Thin web UI and its API over the same database as the worker.
- A configuration change made here is picked up by the worker on its **next run** — no redeploy, no restart.
- Admin test actions are **dry-runs** (or test-only sends): they write nothing to the seen-tenders table and send nothing to business recipients.
- If the admin app is down, the worker keeps running on the last saved configuration.

## 9.2 Required screens (confirmed, v1.1 §5.11)

| # | Screen | Capabilities |
|---|---|---|
| 1 | **Health dashboard** | Per source: last successful run, last failure category, 7/30-day counts of new tenders, verdicts, notification failures; provider chain status; **red banner** if any queue is stuck. |
| 2 | **Sources** | List, add, edit, enable/disable; "Test this source" dry run (shows what it would find). |
| 3 | **Tenders** | Browse; open one to see its **timeline by correlation ID** (seen → documents → extraction → triage → verdict → email). |
| 4 | **Knowledge base** | Edit/upload (`.docx`, `.pdf`, `.md`, `.txt` or pasted text), version history, diff between versions, **token-budget indicator**. |
| 5 | **LLM providers** | Profiles; role assignment; "Test connection"; `approved_for_company_docs` toggle; usage and budget. |
| 6 | **Recipients** | The two lists (tender recipients; dev alert recipients) as per §5.10.1. |
| 7 | **Mail providers** | Chain order, capabilities, "Send test email", breaker status. |
| 8 | **Triage & urgency** | Include/exclude keywords, target sectors/regions, optional minimum contract value, relevance threshold, urgency window. |
| 9 | **Settings** | Test Mode switch (§5.12), alert thresholds, retention. |
| 10 | **Audit log** | Every configuration change (who, when, which fields — never secret values). |

## 9.3 Authentication

- **Confirmed:** authenticated users only (v1.1 §5.11). The knowledge base and secrets are **never exposed to Viewers**.
- **Roles [PROPOSED]:** at least Admin (can change configuration and secrets) and Viewer (read-only dashboards and timelines).
- **Open decision:** the exact login method and who gets access — `docs/13-open-decisions.md` (#9). Do not invent an SSO/identity provider or user provisioning process.

## 9.4 Configuration management

- All admin actions that mutate configuration write to the shared database; worker picks up at next run.
- Every change is audit-logged in `ConfigChangeLog` with actor, entity, entity_id, changed fields (never secret values).
- Sources: add/edit/disable without code deployment; "Test this source" dry-run.
- Triage rules, urgency window, alert thresholds, Test Mode and monthly AI budget are admin-configurable (v1.1 §5.8).

## 9.5 Secret handling

- Secrets (LLM API keys, mail credentials, source auth) are **write-only**:
  - Never returned in API responses.
  - Never logged.
  - UI shows only whether a secret is set + maybe the last few characters.
- `approved_for_company_docs` set to `true` is an explicit, audit-logged admin action.
- Config change log never stores secret values.

## 9.6 Dry-run behaviour

- "Test this source": fetches and displays what it would find — writes nothing to seen-tenders, emails no one.
- "Test connection" (LLM): sends a one-line prompt; shows latency, resolved model, error; checks JSON-mode and vision support where possible.
- "Send test email" (mail): a test-only send; a success closes a provider's circuit breaker.
- These actions never route to business recipients.

## 9.7 Test source

- Adding a **test source** and verifying a live source can be added/disabled/scheduled via configuration alone is an acceptance criterion (v1.1 §11) — the admin UI must make this possible without deployment.

## 9.8 Test provider connection

- "Test connection" must validate the profile end-to-end (one-line prompt, latency, resolved model, JSON/vision capability where possible) and surface failures clearly.
- Changes take effect on the **next call**, not on save necessarily *[mechanism: role/profile resolution happens per run]*.

## 9.9 Worker independence

- Admin app is optional for worker operation; only the shared DB + document storage are coupling points.
- Worker re-reads config at run start; never caches settings across runs.
- Admin actions do not mutate operational crawl state (except via config, which the worker consumes).

## 9.10 Read/write responsibilities

| Data | Admin write? | Worker write? |
|---|---|---|
| Sources, recipients, LLM profiles/roles, mail providers, settings, triage rules, KB | Yes (audit-logged) | No (reads only) |
| Tenders, documents, run history, verdicts, notifications, alerts, usage, breaker state | No (read-only via timelines/health) | Yes |
| Secrets | Yes (write/set/rotate) | Reads at run time |

> **Note:** breaker state is worker-owned internal status *[proposed]*; admin only observes it and can trigger a probe/test email.

## 9.11 Confirmed vs proposed

- **Confirmed:** the ten screens; dry-run admin; authenticated users; viewers cannot see KB/secrets; admin-on-down resilience; audit of config; write-only secrets; Test Mode switch; health dashboard per-source metrics & red banner.
- **Proposed:** Admin/Viewer role model; login method; specific UI framework; breaker observability details.
- **Open decisions:** admin user accounts and access — `docs/13-open-decisions.md`.
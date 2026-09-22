# Tender Intelligence

Continuous tender monitoring and auto-notification for **OPEX Consulting Limited / RegTech365**. A headless worker watches configurable tender sources, downloads and understands every attached document, and — using a versioned record of OPEX's real capabilities — emails a structured go/no-go verdict (APPLY / DO NOT APPLY / APPLY WITH CONDITIONS) with the tender's documents attached (or securely linked).

It does **not** submit applications. It notifies humans, who apply.

## Where the source specifications live

- `source-docs/tender-intelligence-spec-v1.1.md` — **authoritative** specification (revision of v1.0).
- `source-docs/tender-intelligence-spec-v1.0.md` — historical/contextual only. Where the two conflict, v1.1 wins; meaningful conflicts are recorded in `docs/13-open-decisions.md`.

## What `/docs` contains

Implementation specification derived from v1.1. Each file states what is a confirmed requirement vs a proposed implementation choice.

| File | Contents |
|---|---|
| `00-project-brief.md` | What the system does, scope, out-of-scope, first milestone, constraints |
| `01-product-requirements.md` | Functional requirements by capability, classified MUST/SHOULD/OPTIONAL/DEFERRED/NEEDS_DECISION |
| `02-technical-architecture.md` | Headless worker + admin app, pipeline, abstractions, invariants |
| `03-data-model.md` | Entities, fields, relationships, constraints, indexes, audit/security |
| `04-pipeline-spec.md` | End-to-end pipeline, failure/retry/idempotency/correlation/alerting |
| `04a-waho-discovery-notes.md` | WAHO discovery verification record (verified/assumed/unknown), dry-run boundary, task report |
| `05-source-adapter-spec.md` | Source adapter architecture + WAHO first implementation |
| `06-document-processing-spec.md` | Fetcher + understanding (PDF/OCR/DOCX/ZIP/tables/multilingual) |
| `07-ai-verdict-spec.md` | Stage A triage + Stage B verdict, evidence rules, JSON validation, data policy |
| `08-email-notification-spec.md` | MailProvider abstraction, chain/failover, Test Mode, attachments, templates |
| `09-admin-app-spec.md` | Admin UI/API screens and behaviours |
| `10-security-spec.md` | Secrets, data policy, access control (confirmed vs proposed) |
| `11-testing-strategy.md` | Test layers and the failure scenarios the system must survive |
| `12-deployment.md` | Deployment model and phased delivery |
| `13-open-decisions.md` | **Every unresolved business/technical decision — do not invent these** |
| `14-acceptance-criteria.md` | Testable acceptance criteria (Given/When/Then) |

## What `/prompts` contains

Paste-ready prompts for an AI coding agent (e.g. Claude Code). Each prompt names the docs to read first, defines the task and out-of-scope, and requires tests, a secure implementation, and a structured report.

| Prompt | Builds |
|---|---|
| `00-master-context.md` | Project context + rules (prefix for all prompts) |
| `01-architecture.md` | Initial architecture and interfaces |
| `02-database.md` | Schema, migrations, models, indexes |
| `03-infrastructure.md` | Config, logging, secrets, scheduling, storage abstraction |
| `04-waho-discovery.md` | WAHO listing discovery (`list_new_tenders`, pagination, fixtures) |
| `05-tender-deduplication.md` | New/update detection, seen-tenders, correlation IDs, RunHistory |
| `06-tender-persistence.md` | Storage layer: tenders, documents, status transitions, auditability |
| `07-document-discovery.md` | WAHO `get_detail` + `get_attachments` (document enumeration) |
| `08-document-download.md` | Document fetcher: download, ZIP recurse, checksums, per-doc failures |
| `09-document-processing.md` | Understanding: PDF/OCR/vision/DOCX/tables/langs + document bundle |
| `10-pipeline-orchestration.md` | Headless worker run loop, config reload, RunHistory, crash safety |
| `11-test-mode-email.md` | Mail provider chain, Test Mode routing, templates, idempotency |
| `12-audit-and-timeline.md` | Correlation timelines, notification/audit logs, alert manager |
| `13-ai-triage.md` | Stage A triage |
| `14-ai-verdict-engine.md` | Stage B verdict + formatter |
| `15-admin-api.md` | Admin API |
| `16-admin-ui.md` | Admin UI |
| `17-security-hardening.md` | Security review + hardening pass |
| `18-testing-and-qa.md` | Hostile QA / failure testing |
| `19-code-review.md` | Final review against all specs (incl. go-live blockers) |

Deployment is not a separate prompt: go-live preparation is part of the code-review gate
(`19-code-review.md`, requirement 9 — see `docs/12-deployment.md`).

## How an AI coding agent should use this repository

1. Read `PROJECT_RULES.md` first — it is binding.
2. Read `README.md` + `prompts/00-master-context.md` for context.
3. Paste the prompt for the slice you want built (always after the master context).
4. The prompt tells you which docs to read before writing code.
5. Implement, test, and report per the prompt's "Report" section.
6. Do not invent anything in `docs/13-open-decisions.md` — flag it instead.

## Recommended implementation order

```text
Phase 0  Infrastructure / safety / project foundations
Phase 1  WAHO discovery → dedup/persist → documents (discovery → download → processing)
         → pipeline orchestration → Test Mode email → audit/timeline
Phase 2  Knowledge Base → AI triage → AI verdict (+ 2nd mail provider, first admin screens)
Phase 3  Additional source adapters (TenderDetail, All Business Africa, UNGM)
Phase 4  Admin application / configuration / hardening (go-live)
Phase 5  RAG/vector retrieval ONLY if a demonstrated v1 requirement emerges
```

Phase 5 is not part of the initial implementation.

## How to identify unresolved decisions

Read `docs/13-open-decisions.md`. Every item has a decision statement, why it matters, status, options, who should decide, and impact. If a task depends on one, implement a marked configuration default and flag it to the human rather than choosing a business value. Key unresolved items: actual recipients, sender mailbox, Sendlib Free vs Pro, mail providers 2–3, target sectors/regions/minimum value, monthly AI budget, storage location, admin users, and production LLM data-handling approval.

## Where to start

Begin with an AI coding agent opening `prompts/00-master-context.md`, then `prompts/01-architecture.md` (Phase 0), then `prompts/03-infrastructure.md` and `prompts/02-database.md`. Phase 0 (foundations) is implemented: dependency/tooling scaffolding, database schema + migrations, encrypted secrets, structured logging with correlation IDs, provider interfaces, the worker startup spine, a minimal admin API with `/health`, and the test suite. Application source code lives under `src/`.

Phase 1 (WAHO → Test Mode email) is split into fine-grained slices: `04-waho-discovery.md`, `05-tender-deduplication.md`, `06-tender-persistence.md`, `07-document-discovery.md`, `08-document-download.md`, `09-document-processing.md`, `10-pipeline-orchestration.md`, `11-test-mode-email.md`, `12-audit-and-timeline.md`. Run them in order; each names its prerequisite prompts in "Read"/"Out of scope".

## Local development

Phase 0 requires only a Python 3.12+ environment. The default dev database is **SQLite** —
no Docker or Postgres needed:

```text
git clone <repo> && cd tender-intelligence
uv sync                              # install deps + dev tools into .venv
uv run python -m tender_intelligence.crypto generate-key   # prints a TI_MASTER_KEY
cp .env.example .env                 # then set TI_MASTER_KEY (see below)
uv run alembic upgrade head          # creates ./data/dev.db and applies migrations
uv run pytest                        # run the test suite (SQLite, offline)
uv run python -m tender_intelligence.worker         # run the worker bootstrap
uv run python -m tender_intelligence.admin          # run the admin API on :8000
```

`.env.example` documents every supported variable. By default `TI_DATABASE_URL` points at
SQLite (`sqlite+pysqlite:///./data/dev.db`); delete that file to reset the dev database.
The database URL comes from `TI_DATABASE_URL` (env var or `.env`) in that order of priority —
`alembic.ini` intentionally keeps `sqlalchemy.url` empty so the `.env` is honored.

To use a hosted Postgres instead (e.g. Neon), just paste the connection string into
`TI_DATABASE_URL` — create a project in the Neon web app, "Create database", copy the
connection string, and add `?sslmode=require`:

```text
TI_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST.neon.tech/DB?sslmode=require
```

Then rerun `uv run alembic upgrade head` against it.

`/health` is the admin API's liveness + database check:

```text
curl http://127.0.0.1:8000/health
```

Without a running Postgres, the test suite is fully offline (SQLite via Alembic in-memory
migrations), so `uv run pytest` always works. The worker only requires the seed YAML path
when bootstrap-seeding config from a file.

## Note

Setup commands for unselected technologies are deliberately omitted (see `docs/12-deployment.md` for the deployment model and proposed-choice labels). Docker is **not** required for local development — the stack targets SQLite by default (see `docs/12-deployment.md` for the deployment model), with Postgres via Neon/another host as a drop-in via `TI_DATABASE_URL`.
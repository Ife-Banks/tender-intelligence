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
| `04-waho-source-adapter.md` | WAHO adapter + fixtures |
| `05-document-processing.md` | Document acquisition + understanding |
| `06-ai-triage.md` | Stage A triage |
| `07-ai-verdict-engine.md` | Stage B verdict + formatter |
| `08-email-notifications.md` | Mail provider chain + notification templates |
| `09-admin-api.md` | Admin API |
| `10-admin-ui.md` | Admin UI |
| `11-security-hardening.md` | Security review + hardening pass |
| `12-testing-and-qa.md` | Hostile QA / failure testing |
| `13-deployment.md` | Deployment preparation |
| `14-code-review.md` | Final review against all specs |

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
Phase 1  WAHO → tender discovery → documents → processing → Test Mode email
Phase 2  Knowledge Base → AI triage → AI verdict (+ 2nd mail provider, first admin screens)
Phase 3  Additional source adapters (TenderDetail, All Business Africa, UNGM)
Phase 4  Admin application / configuration / hardening (go-live)
Phase 5  RAG/vector retrieval ONLY if a demonstrated v1 requirement emerges
```

Phase 5 is not part of the initial implementation.

## How to identify unresolved decisions

Read `docs/13-open-decisions.md`. Every item has a decision statement, why it matters, status, options, who should decide, and impact. If a task depends on one, implement a marked configuration default and flag it to the human rather than choosing a business value. Key unresolved items: actual recipients, sender mailbox, Sendlib Free vs Pro, mail providers 2–3, target sectors/regions/minimum value, monthly AI budget, storage location, admin users, and production LLM data-handling approval.

## Where to start

Begin with an AI coding agent opening `prompts/00-master-context.md`, then `prompts/01-architecture.md` (Phase 0), then `prompts/03-infrastructure.md` and `prompts/02-database.md`. Phase 0 scaffolding is underway (`pyproject.toml`, `.env.example`, `alembic.ini`); application source code will live under `src/` as slices land.

## Note

Setup commands for unselected technologies are deliberately omitted (see `docs/12-deployment.md` for the deployment model and proposed-choice labels).
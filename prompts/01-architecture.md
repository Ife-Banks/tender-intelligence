# Prompt 01 — Architecture

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `README.md`
- `docs/00-project-brief.md`
- `docs/01-product-requirements.md`
- `docs/02-technical-architecture.md`
- `docs/04-pipeline-spec.md`
- `docs/13-open-decisions.md`

## Task

Implement the **initial application architecture and interfaces** for the Tender Intelligence system. This is Phase 0 skeleton work. Do not implement business logic end-to-end yet; establish the seams so later prompts can fill them in without rework.

Specifically:

1. Repository layout for a **headless worker** core plus a **thin admin app** target, sharing a database and document storage. The admin app may be stubbed at this stage, but the separation must be structural.
2. The **pipeline skeleton**: Source Registry → Crawler → New-Listing Dedup → Document Fetcher → Document Understanding → Stage A Triage → Stage B Verdict → Verdict Formatter → Email Dispatcher → Notification Log/Audit, as clearly separated stages that each record a status and share a correlation ID.
3. The core **abstractions** (interfaces/ABCs, minimal signatures only):
   - `SourceAdapter` (`list_new_tenders()`, `get_detail()`, `get_attachments()`)
   - `LLMClient` (role-based model calls with retry/fallback hooks)
   - `MailProvider` (send with capabilities metadata)
   - An attachment-planner function signature
   - A document-processor stage signature producing a reusable document bundle
4. **Structured logging** scaffolding with correlation ID + stage + status + error-code fields.
5. **Configuration access** interface reflecting that the worker re-reads config at the start of every run and never caches across runs.
6. A **Test Mode** global switch that defaults ON.
7. Placeholder modules (clearly marked) for later stages — do not implement crawlers, AI, or email adapters yet.

## Out of scope

- Any source adapter implementation (arguably `docs/05`).
- Any real LLM provider / mail provider adapters.
- AI prompting, verdict logic, OCR, attachments compression.
- The admin web UI/API beyond a stub.
- Database schema (see `prompts/02-database.md` — unless the schema is trivial scaffolding, defer it).

## Tests

- Add unit tests that the pipeline skeleton wires stages in the documented order and that a stage failure with a correlation ID produces a structured log record with the expected error-code taxonomy stub.
- Add tests asserting the config reload boundary (new config object per run; no cross-run cache).
- Add a test that Test Mode defaults ON.

## Rules

- No unrelated refactors; minimal files.
- Preserve the separation: worker must not import admin code and vice versa.
- No secrets in code or history.
- If an ambiguity blocks you (e.g. language/stack choice not mandated by the source), pick the source-suggested default only where v1.1 §9 names one; otherwise note it as open and proceed with a minimal, clearly-replaceable approach.

## Report

Files changed; mapping of each file to the docs/requirements it satisfies; tests run + results; ambiguities and open decisions you encountered; remaining TODOs.
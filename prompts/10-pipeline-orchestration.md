# Prompt 10 — Pipeline Orchestration

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/04-pipeline-spec.md` (full — §4.1 diagram, §4.2 stages, §4.3 crash safety, §4.4 retry,
  §4.5 partial failures, §4.8 run history, §4.10 config reload, §4.11 begin)
- `docs/02-technical-architecture.md` (§2.9 worker architecture, admin-independence §4.10)
- `docs/13-open-decisions.md`

## Task

Implement the **headless worker / crawl-run orchestrator**: the loop that runs each enabled
source on its schedule, wires the stages from `prompts/04..09`, records `RunHistory`, and
reloads configuration per run — with **no AI and no email** in this prompt.

1. **Run loop per enabled source:** on schedule, run » discover (`04`) » deduplicate (`05`)
   » persist (`06`) » document discovery (`07`) » download (`08`) » process (`09`); if a stage
   is unimplemented, the orchestrator still executes the implemented chain and skips the rest
   with a clear log (Phase-1 incremental wiring).
2. **RunHistory lifecycle** (`docs/04` §4.8): row created at run **start** (listings found),
   completed/errored at run **end**, with `failed correlation IDs` recorded. A crashed run
   must never lose seen-records nor block the next scheduled run (`docs/04` §4.3).
3. **Configuration reload** (`docs/04` §4.10–4.11): re-read config at the start of **every**
   run, never cache across runs. If the admin app is down, the worker runs on the *last saved*
   configuration. Unreachable sources log a warning, not a crash.
4. **Correlation IDs** everywhere (`docs/04` §4.8): each run and each tender carries one;
   every log line, row and error stamped; timeline reconstructible later.
5. **Retry + backoff** for crawls and downloads per `docs/04` §4.4 — no hammering.
6. **Failure handling:** structured error codes (`source_unreachable` etc. `docs/04` §4.3);
   partial failures recorded not swallowed; per-document/stage statuses preserved. No silent
   failure — every stage above threshold raises an alert **hook** (implementation of alerting
   lands in `prompts/12`).
7. Provide a **dry-run** mode (inspects what would happen, writes seen-records? — config flag,
   but by default dry-run writes nothing and sends nothing) for "Test this source".

## Out of scope

- AI triage/verdict stages — `prompts/13/14-ai-*.md`.
- Any email dispatch — `prompts/11-test-mode-email.md`.
- Admin FastAPI surface / UI — `prompts/15/16-admin-*.md`.
- Alert delivery implementation — `prompts/12-audit-and-timeline.md`.

## Tests

- Fixture source runs end-to-end (offline): RunHistory start→completed with counts.
- Simulated mid-run crash: next scheduled run succeeds; no duplicate seen-records; no blocked
  state.
- Config changed between runs → new config observed; admin-app-down scenario works with last
  saved config (env/config seams, not process coupling).
- Dry-run: nothing persisted, nothing sent.
- One stage throwing → whole run errors cleanly; run-history reflects `errored`; other
  sources unaffected.

## Rules

- Worker is admin-independent: no dependency on a running admin HTTP app (`docs/04` §4.10).
- Never re-encrypt/log secrets from config reloads (`docs/10`, `PROJECT_RULES`).
- Stage statuses are recorded, not just results (`docs/04` §4.5).

## Report

Files changed; run-loop diagram mapping to stage prompts; RunHistory lifecycle impl; config
reload strategy; retry/backoff table; crash-safety test; tests + results; TODOs.
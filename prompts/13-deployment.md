# Prompt 13 — Deployment

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/12-deployment.md` (full — phases §12.4 must not be reordered without a documented reason)
- `docs/00-project-brief.md` (first milestone)
- `docs/10-security-spec.md`
- `docs/13-open-decisions.md` (surface what must be decided for go-live, don't decide)

## Task

Prepare **deployment** for the Tender Intelligence system per `docs/12`. Produce deployment artifacts and documentation; do not change business behaviour.

1. **Worker deployment:** a repeatable way to deploy and schedule the headless worker honouring per-source `crawl_frequency`; crash-safe (seen-tenders persist, `pending_retry` outbox survives restarts). Clarify restart flow.
2. **Admin app deployment:** deployable independently; worker does not depend on it.
3. **Database:** migrations run idempotently; document backup/recovery for DB + document archive + secrets config (audit retention per settings).
4. **Document storage:** archive + signed-link serving readiness (service the worker and admin can start against).
5. **Secrets:** environment secrets + master key provisioning flow (out-of-band; nothing committed). Document exactly which env vars exist.
6. **Scheduling & backlog:** rate-limit-aware flushing (e.g. Sendlib 30/min Free, 300/min Pro — re-verify).
7. **Logs & monitoring:** structured-log collection, health view availability, red-banner signal, alert thresholds via settings.
8. **Environment separation:** Test Mode defaults ON; prod switch-off is an explicit audit-logged action; document the go-live checklist from `docs/12` §12.4 phase 4 (approved provider + real KB + Test Mode off).
9. **Runbook:** startup, health checks, failure triage, recovery, backup/restore procedure.
10. Flag anything the deployment requires that is an **open decision** (storage location O10, domain/DNS O17, admin users O11, providers O4/O13, budget O9, etc.) in a clearly-marked "Required decisions for go-live" section — do not choose.

## Out of scope

- Actually deploying to a real cloud vendor (choose-and-document is for the owner; `docs/12` §12.5).
- New features or refactors.
- Live credentials.

## Tests/verification

- Consequence of this prompt is documentation/artefacts: verify every documented command/step is valid (run what can be run locally: migrations, env bootstrap, scheduler registration check in a sandbox).
- Verify Test Mode ON default on a fresh environment bootstrap (DP2).

## Rules

- No vendor lock-in invent; no unsupported business decisions.
- Nothing user-facing changes.

## Report

Artifacts created; docs mapping (esp. `docs/12` §§); verified steps; "Required decisions for go-live" list; TODOs.
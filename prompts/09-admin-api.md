# Prompt 09 — Admin API

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/09-admin-app-spec.md` (full)
- `docs/03-data-model.md`
- `docs/08-email-notification-spec.md`
- `docs/10-security-spec.md`
- `docs/13-open-decisions.md` (O11 admin users; O3/O4/O9/O12 — don't decide)

## Task

Implement the **admin API** — the read/write surface the admin UI (next prompt) will call. Thin, over the shared database.

Endpoints must cover the ten screens (`docs/09` §9.2):

1. **Health** — per source, last successful run, last failure category, 7/30-day counts (new tenders, verdicts, notification failures), provider chain status, red banner if queue stuck.
2. **Sources** — list/add/edit/enable-disable; **"Test this source" dry run** endpoint (fetches and shows; writes nothing to seen-tenders, emails no one).
3. **Tenders** — browse; per-tender timeline by correlation ID (seen → documents → extraction → triage → verdict → email with statuses).
4. **Knowledge base** — current version, upload/edit (`.docx/.pdf/.md/.txt` or pasted text), version history, diff between versions, token-budget indicator.
5. **LLM providers** — profiles CRUD, role assignment, "Test connection" (one-line prompt, latency, resolved model, error; JSON/vision check where possible), `approved_for_company_docs` toggle (explicit, audit-logged), usage + budget.
6. **Recipients** — two lists (tender, dev alert); CRUD with the last-active-dev-recipient guard.
7. **Mail providers** — chain order, capabilities, "Send test email" (test-only; closes breaker), breaker status.
8. **Triage & urgency** — keywords, sectors, regions, min value, threshold, urgency window.
9. **Settings** — Test Mode switch (ON default; off = explicit audit-logged action), alert thresholds, retention.
10. **Audit log** — every config change; never secret values.

**API rules:**
- **Secrets are write-only** — set/rotate only; never returned in responses; UI shows "set" + optional last-4. `docs/10` §10.1.
- Viewers cannot see KB or secrets; roles enforced at the API layer.
- Audit-log every mutating call (actor, entity, fields — never values that are secrets).
- Response shapes are stable contracts for the UI.
- Read/write split per `docs/09` §9.10.

## Out of scope

- The UI (next prompt).
- Deciding admin users/login method (O11) — implement a config-driven role model with Admin/Viewer, no hard-coded business user list.
- Pipeline, AI, email internals — only their authoritative readers (timelines/health).

## Tests

- CRUD tests per resource with auth-role enforcement (Viewer blocked from KB/secrets endpoints).
- Dry-run endpoint produces no seen-tenders writes and no emails.
- Last-active-dev-recipient deletion/deactivation refused.
- Secrets never present in any response (assert omission), never in audit log.
- Test Mode toggle: off requires audit entry; default ON.
- Timeline endpoint reconstructs a tender's full history from seeded data.
- Test-connection and test-email endpoints use fakes in tests (no real third parties).

## Rules

- No inline crawls in the admin path (dry-run only).
- Keep worker independent: admin API must not assume the worker is reachable.
- Tests must stay offline.

## Report

Files changed; endpoint list; docs mapping; tests run + results; security review notes; TODOs.
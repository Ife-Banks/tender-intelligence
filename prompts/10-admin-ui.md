# Prompt 10 — Admin UI

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/09-admin-app-spec.md` (full, the ten screens)
- `docs/10-security-spec.md`
- The API contract produced by `prompts/09-admin-api.md` (consume it, don't relitigate it)

## Task

Implement the **admin web UI** — a thin front-end over the admin API. Non-technical users must be able to operate the system safely. No inline business logic: the UI is a view/edit layer over API shapes.

Build each confirmed screen (`docs/09` §9.2):

1. **Health dashboard** — per-source status, last run/failure category, 7/30-day counts, chain status, red banner for stuck queues.
2. **Sources** — list/add/edit/disable, parser-config view, "Test this source" dry-run result display.
3. **Tenders** — browse + timeline by correlation ID (statuses per stage).
4. **Knowledge base** — edit/upload, version history, diff, token-budget indicator.
5. **LLM providers** — profiles, role assignment, "Test connection", `approved_for_company_docs` toggle with confirmation, usage/budget display.
6. **Recipients** — both lists, full CRUD, explicit warning/block on last active dev recipient.
7. **Mail providers** — chain order, capabilities, "Send test email", breaker status.
8. **Triage & urgency** — rules editor.
9. **Settings** — Test Mode switch (explicit confirmation + audit notice; defaults ON), alert thresholds, retention.
10. **Audit log** — read-only filterable history (no secret values).

**UI rules:**
- Secrets shown only as "set" + optional last-4; never rendered in full; never sent back to API as value.
- Viewer role: KB and secrets screens hidden entirely (server also enforces).
- Dry-runs/test calls surfaced as results, not as side-effecting actions.
- Accessible, responsive, and consistent with the API's read/write split (`docs/09` §9.10).

## Out of scope

- Backend logic (API prompt owns it).
- Deciding login method/identity (O11) — consume whatever the API provides.
- Any pipeline/worker functionality in the UI.

## Tests

- Component tests: each screen renders from fixture API payloads; dry-run result display; secret fields masked; last-dev-recipient guard surfaced.
- Viewer-role test: KB + secrets routes not rendered.
- Test-Mode toggle flow requires confirmation and reflects default ON.
- No UI test may hit a real provider (mock the API client).

## Rules

- The UI is never a source of truth; write-through to the API.
- No secrets logged from the browser; no extra secret handling invented beyond the API contract.

## Report

Files changed; screens implemented; docs mapping; tests run + results; TODOs.
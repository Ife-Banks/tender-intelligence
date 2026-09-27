# Consolidated Admin Audit & Remediation

Date: 2026-09-25  
Branch/commit inspected: `main` / `16b6543` (`admin api and ui configured`)

## 1. Scope and baseline

Inspected the current repository, Prompt 15 Admin API, Prompt 16/16A static UI, and Prompt 16B API integration. Remediated the local test-auth UI seam and environment restriction only. No production identity provider or account list was selected. No worker/pipeline behavior, real provider, mail operation, or business data was touched.

Before edits, the tracked worktree had no modifications; numerous pre-existing untracked `.pytest-*` scratch directories and `.claude/` were present. Those were left untouched. The current audit changes are listed at the end. Recent commits were `16b6543`, `536e4a1`, and `6bac53b`.

### Prompt state observed

| Prompt | Current source/evidence | Independent status observed |
|---|---|---|
| 04–10 | discovery through orchestration modules, integration tests and earlier evidence | Implemented; current full regression has one unrelated WAHO deadline-parser failure |
| 11 | notification/mail modules, Sendlib evidence and tests | Implemented; Test Mode remains server-controlled and default ON |
| 12 | audit/timeline/alert modules and tests | Implemented |
| 12.1 | deadline module, migration, resolution tests | Implemented; adjacent WAHO parser failure remains outside this audit |
| 12.2 | Sendlib config evidence and existing Prompt 11 model | Config path implemented; this task did not run a live provider test |
| 13 | triage service, verification report/tests | Implemented; deterministic tests/evidence exist |
| 14 | verdict service, formatter, implementation report/tests | Implemented; not independently reverified here |
| 15 | `admin/api.py`, `auth.py`, Prompt 15 report and integration tests | Implemented; previous independent report says NEEDS FIXES (PostgreSQL last-active-dev-recipient concurrency not proven) |
| 16 | static HTML/CSS/ES modules, fixtures and implementation report | Implemented; no framework introduced |
| 16A | design system, static UI and independent report | Implemented; previous verification reports responsive/browser-console evidence incomplete |
| 16B | API client/UI integration and reports | Implemented; this audit fixes known auth-shell defects, but authenticated browser CRUD remains unobserved in this environment |

`implementation/00-current-state.md`, `01-decisions.md`, and `02-known-issues.md` are absent. `implementation/03-next-task.md` is stale and still describes Prompt 11 as the next task; it was not rewritten here.

## 2. Architecture and ownership

```text
Browser → static Admin HTML/CSS/ES modules → centralized api.mjs
        → FastAPI /api/v1 routes → shared services/repositories → shared DB/config

Worker → orchestrator/pipeline → shared services/repositories → shared DB/config
```

The UI calls only same-origin `/api/v1` through `api.mjs`; the only browser `fetch()` is in that module. Operational provider/source work is behind API services. Search found no Admin import in `worker/` or `orchestrator/`; Admin remains optional for worker execution. No CORS is needed for the supported FastAPI-mounted UI because the UI/API share an origin. A separately hosted static UI would require an explicitly configured safe same-origin proxy or a separately specified CORS policy; none was added.

The Admin API exposes the current route families: Sources (`GET /sources/supported-types`, `GET/POST /sources`, `GET/PUT /sources/{id}`, `PATCH /sources/{id}/active`, `POST /sources/{id}/test`); Tenders (`GET /tenders`, `GET /tenders/{id}`, `/timeline`, `/verdicts`); Settings and Triage (`GET/PUT /settings`, `GET/PUT /triage`); LLM (`GET/POST /llm/profiles`, `GET/PUT/DELETE /llm/profiles/{id}`, `GET /llm/roles`, `PUT /llm/roles/{role}`, `GET /llm/usage`, `POST /llm/profiles/{id}/test`); Recipients (`GET/POST /recipients`, `PUT/DELETE /recipients/{id}`); Mail (`GET/POST /mail/providers`, `PUT /mail/providers/{id}`, `PATCH /mail/providers/{id}/active`, `POST /mail/providers/{id}/test`, `POST /mail/test`); KB (version list/detail/upload/diff); Audit (`GET /audit`); Health (`GET /health/dashboard`). These are the existing routes, not newly added endpoints.

Routes declare `CurrentActor` for authenticated Viewer/Admin access or `AdminActor` for Admin-only actions/resources. `require_admin` rejects Viewer with 403. Configuration mutation endpoints use explicit request schemas; secret serializers expose configured state only. Existing API and integration tests cover representative Admin/Viewer/read/write and safe-error behavior.

## 3. Authentication diagnosis (O11)

**Multiple causes existed.**

1. The default production actor resolver had no configured identity provider and correctly returned 401. O11 (who may access, account list/roles, and login provider) remains OPEN. The prior UI copy incorrectly implied an identity provider was already configured.
2. A development-only `X-Test-Actor` / `X-Test-Role` seam existed, gated by `TI_ADMIN_ENABLE_TEST_AUTH`, but the UI boot path called `createApi()` without actor options, so local browser requests never used it.
3. The server previously treated every value except `production`/`prod` as eligible when test auth was enabled. That included staging. This was an unsafe environment-boundary defect.
4. `createApi()` retained module-global test actor values after a configured call, so a later default/anonymous `createApi()` in the same page context could inherit them.
5. The 401 shell used an inaccurate “sign in through the configured identity provider” message, initially labeled anonymous access as Viewer, and left Test Mode in “loading” even though an anonymous actor cannot read settings. CSS display declarations also defeated hidden state on some live-shell fixture controls.

Remediation: test headers are now accepted only when `TI_ENV=development` and the explicit `TI_ADMIN_ENABLE_TEST_AUTH=true` flag is on; missing or malformed actor/role stays 401. Staging and production test headers are rejected. The UI sends fixed local actor identifiers only after explicit `?devAuth=1` opt-in plus role selection. Default API-client creation clears test identity. The UI identifies anonymous state honestly, marks Test Mode unavailable until an authenticated settings read succeeds, and explains O11 without pretending production login exists. A global `[hidden]` rule preserves live-vs-fixture visibility.

The browser-selected test role is untrusted input. It does not elevate a production/staging request: only the backend's exact development + explicit-enable gate can accept it. This mechanism is local testing only and does not resolve O11 or constitute production authentication.

### Required test-auth matrix

| Environment | Flag | Actor header | Expected / observed |
|---|---:|---|---|
| development | OFF | none | 401 / tested |
| development | ON | Admin | accepted test Admin / tested |
| development | ON | Viewer | same actor resolver role contract; Viewer authorization separately tested |
| production | ON | test Admin | 401 / tested |
| production | OFF | none | 401 / default fail-closed test |
| staging | ON | test Admin | 401 / newly tested |

`TI_ENV` is canonical. `TI_ENVIRONMENT` is ignored; an existing test asserts that precedence. `.env.example` defaults to `TI_ENV=development`, `TI_ADMIN_ENABLE_TEST_AUTH=false`.

## 4. Ten-screen/API review

All ten renderers exist and use the shared API client: Health (`/health/dashboard`); Sources (`/sources`, `/sources/supported-types`, source CRUD/active/test); Tenders (`/tenders`, detail/timeline/verdicts); Knowledge Base (version list/detail/upload/diff); LLM (profiles/roles/usage/tests); Recipients (recipient list/CRUD); Mail (providers and test endpoints); Triage (`/triage`); Settings (`/settings`); Audit (`/audit`). The screens use live API DTOs when the API is reachable and do not silently fall back to fixtures on 401/403. Fixture mode remains visibly labeled when the API is unavailable/absent.

This audit exercised all UI Node contracts and API integration tests, including API-shaped persisted test-client flows. It did not perform a browser click-through of authenticated CRUD: the CUA browser inventory had no tabs available, so no actual browser network console/session was available. No test was represented as browser proof. The prior Prompt 16B report also documents PostgreSQL concurrency and Prompt 16A mobile/browser-console evidence gaps.

## 5. Authorization, secrets, Test Mode, and operations

- Anonymous: fails closed with 401; UI now labels `Role · anonymous`.
- Viewer: authenticated reads where permitted; `AdminActor` routes return 403. The UI hides KB/Audit and backend authorization remains authoritative. Representative API tests cover viewer denials and writes.
- Admin: explicit actor role required; writes use API validation/audit mechanisms.
- API serializes explicit DTOs; no ORM model dumps are returned as responses. Secret-bearing fields are write-only or exposed as configured flags. Audit tests and tests use sentinel fake values, not real credentials.
- Pattern scan matches were confined to source secret-handling code and synthetic test sentinels; no real key/token/password value was emitted by the filename-only check. Browser code has no localStorage/sessionStorage; only `api.mjs` performs fetch.
- Test Mode remains ON by default; Prompt 15 API and notification tests confirm server state and protections. No setting was mutated here, no provider was tested, and no email/business recipient was contacted.
- Source dry-run and LLM/mail test operations remain backend-mediated. This audit triggered none of them.
- No production identity provider/users, business recipients, sender mailbox, provider approvals, target sectors/regions/minimum values, AI budget, scraping/ToS policy, or other O11/O1–O24 decisions were invented or altered.

## 6. UI, runtime, and responsive findings

Static architecture remains HTML/CSS/ES modules; no React/Next/Vue/bundler was introduced. The 26 UI tests pass. The `hidden` override now prevents visible offline-preview controls in live mode. 401 and 403 stay distinct in the API client; validation/not-found/server/network errors are normalized safely by the client.

An authenticated browser viewport/runtime check could not be run because no browser tab was exposed by the available CUA inventory. Prior Prompt 16A verification similarly recorded missing desktop/tablet/mobile and direct console evidence. This is an outstanding verification limitation, not a finding that a particular screen is broken.

## 7. Worker independence and open decisions

No worker/orchestrator files changed. Static dependency search found no import from worker/orchestrator to `tender_intelligence.admin`. O11 remains OPEN; production continues to require the future real identity provider/resolver and stays fail-closed. Other decisions remain as listed in `docs/13-open-decisions.md`.

## 8. Tests and results

Commands:

- `node --test tests/ui/*.test.mjs` — **26 passed, 0 failed**.
- `\.venv\Scripts\pytest.exe -p no:cacheprovider --basetemp=.pytest-consolidated-audit-final tests/integration/test_admin_api_prompt15.py tests/integration/test_admin_ui_api_prompt16b.py tests/integration/test_audit_security.py -q` — **32 passed, 0 failed**.
- `\.venv\Scripts\pytest.exe -p no:cacheprovider --basetemp=.pytest-consolidated-audit-full tests -q` — **551 collected, 550 passed, 1 failed**. Failure: `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`, where the expected French July date parses as `None`. This is an existing WAHO/deadline parsing area outside Prompt 15/16 ownership and was not changed. The failure blocks a clean full-regression gate; do not hide or waive it.
- `\.venv\Scripts\ruff.exe check src/tender_intelligence/admin/auth.py tests/integration/test_admin_api_prompt15.py` — passed.
- Initial repository-wide `ruff check src tests` found 94 lint findings in pre-existing files outside this task; no unrelated lint cleanup was attempted.
- `git diff --check` — passed.
- Worker dependency search (`rg -n "tender_intelligence\\.admin|admin\\." src/tender_intelligence/worker src/tender_intelligence/orchestrator`) — no matches.

The first pytest invocation could not access the OS temp root under the sandbox. Re-running with an explicit workspace-local `--basetemp` succeeded. No tests were weakened or removed. No real providers were called.

## 9. Defects fixed and remaining blockers

### Fixed in this audit

| Severity | Ownership | Root cause | Fix and regression evidence |
|---|---|---|---|
| High | Prompt 15 auth | Test headers were accepted in any non-production environment | Restrict to exact `TI_ENV=development`; matrix test proves staging and production rejection |
| High | Prompt 16B UI integration | UI never passed the existing local test-auth seam | Explicit `?devAuth=1` role choice; sends fixed local actor/role headers through centralized client; UI tests pass |
| Medium | Prompt 16B API client | Module-level test actor could survive subsequent anonymous factory call | `createApi()` clears test auth by default; regression asserts actor headers absent before and after explicit test identity |
| Medium | Prompt 16B shell | Anonymous user was mislabeled Viewer, claimed a configured identity provider, and showed Test Mode as loading | Anonymous/O11-aware copy and unavailable Test Mode indicator; UI contract test passes |
| Medium | Prompt 16B shell CSS | Author CSS defeated native `hidden` display behavior | Global `[hidden]` rule and static test |

### Remaining

1. **High, outside Prompt 15/16 ownership:** one full-suite French WAHO deadline parser test fails. Owner should triage the WAHO/Prompt 12.1 parsing boundary before treating the full repository as regression-clean.
2. **Prompt 15 dependency:** PostgreSQL concurrent last-active development-recipient protection remains unverified per the previous independent report; SQLite does not establish PostgreSQL locking behavior.
3. **Prompt 16A verification:** tablet/mobile rendered checks and direct browser-console checks remain unproven.
4. **Prompt 16B verification:** no authenticated browser session/click-through was available; API/UI persistence integration tests pass, but live browser CRUD/network evidence remains outstanding.
5. **O11 production decision:** still open by design. No production login exists, and API 401 is the correct current production result.

## 10. Files changed

- `.env.example`
- `README.md`
- `docs/15-admin-api.md`
- `src/tender_intelligence/admin/auth.py`
- `src/tender_intelligence/admin/static/api.mjs`
- `src/tender_intelligence/admin/static/app.mjs`
- `src/tender_intelligence/admin/static/styles.css`
- `tests/integration/test_admin_api_prompt15.py`
- `tests/ui/live-api.test.mjs`
- `tests/ui/screen-contract.test.mjs`
- `demo-evidence/admin-consolidated-audit-report.md`
- `demo-evidence/admin-consolidated-audit.json`
- `demo-evidence/admin-consolidated-test-results.txt`

## 11. Deliberately deferred

- **Prompt 17:** broader security hardening, threat modeling, security headers beyond existing controls, full dependency audit, comprehensive penetration/bypass review, and third-party data-handler signoff.
- **Prompt 18:** hostile F1–F20 campaign, chaos/retry stress, broad concurrency/reentrancy testing, and full acceptance-criteria proof.
- **Prompt 19:** final whole-repository requirements/code review and release-readiness/go-live gate.

## 12. Final gate

The Admin auth seam defect is remediated, but this consolidated gate remains **NEEDS FIXES** because the complete regression suite has one unrelated WAHO date parser failure and earlier Prompt 15/16A/16B evidence gaps remain open. O11 itself is not treated as a defect; its current fail-closed production behavior is correct.

CONSOLIDATED ADMIN AUDIT: NEEDS FIXES — DO NOT START PROMPT 17

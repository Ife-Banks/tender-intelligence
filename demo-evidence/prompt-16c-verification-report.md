# Prompt 16C — Independent Behavioral Verification Report

## Executive Summary

**Result: NEEDS FIXES.** The application-level authentication gate has been removed from the running Admin UI/API. `/admin/` loads directly, the legacy `devAuth=1` parameter is unnecessary and ignored, and representative API calls work without a session. The focused no-login, safety, UI, and worker tests pass. However, the actual running Admin API returns a safe `500 internal_error` for `GET /api/v1/tenders` and `GET /api/v1/llm/usage`, preventing those live screens from operating. The UI's 403 path also retains stale advice about an administrator account role. No implementation code was changed during this verification.

## Product Decision Verified

`docs/13-open-decisions.md` O11 marks application-level login/authentication **DEFERRED / OUT OF CURRENT SCOPE**, non-blocking, and does not authorize public exposure. `docs/09-admin-app-spec.md`, `docs/10-security-spec.md`, `docs/15-admin-api.md`, `.env.example`, and `implementation/03-next-task.md` agree. The Admin API uses the non-human audit actor `internal-admin-api`. No IdP is configured or required.

## Admin UI Verification

Opened the actual running service in the in-app browser at `http://127.0.0.1:8000/admin/` with no query parameters, cookie setup, or authorization header. It rendered the operations shell, all ten navigation destinations, Test Mode ON, and the persisted live health dashboard without redirect or login UI. Navigating to `http://127.0.0.1:8000/admin/?devAuth=1` retained the same page and navigation; the query is not required and did not act as authentication. The live Tenders screen displayed a generic unavailable panel because its API request returned 500 (Finding F16C-01).

No sign-in, login, logout, session-expired, IdP redirect, role-selection, or fake-user indicator appeared in the rendered UI. `src/tender_intelligence/admin/static/api.mjs` sends no Authorization header and uses `credentials: "omit"`.

## Admin API Verification

Read-only requests were made to the actual running service at `127.0.0.1:8000`, without application credentials. No response carried `WWW-Authenticate`.

| Method | Endpoint | Status | Result / response shape | Application authentication required |
|---|---|---:|---|---|
| GET | `/health` | 200 | JSON object | No |
| GET | `/api/v1/sources/supported-types` | 200 | JSON object | No |
| GET | `/api/v1/sources` | 200 | JSON object | No |
| GET | `/api/v1/tenders` | 500 | `{"error":{"code":"internal_error"}}` | No; operational failure |
| GET | `/api/v1/settings` | 200 | JSON object; `test_mode=true` | No |
| GET | `/api/v1/triage` | 200 | JSON object | No |
| GET | `/api/v1/llm/profiles` | 200 | JSON object | No |
| GET | `/api/v1/llm/roles` | 200 | JSON object | No |
| GET | `/api/v1/llm/usage` | 500 | `{"error":{"code":"internal_error"}}` | No; operational failure |
| GET | `/api/v1/recipients` | 200 | JSON object | No |
| GET | `/api/v1/mail/providers` | 200 | JSON object | No |
| GET | `/api/v1/knowledge-base/versions` | 200 | JSON object | No |
| GET | `/api/v1/audit` | 200 | JSON object | No |
| GET | `/api/v1/health/dashboard` | 200 | JSON object | No |

The isolated TestClient API suite also exercised unauthenticated reads and representative writes with a migrated test database; all passed. The two live 500s are not 401/403 auth denials, but they fail the normal-operation expectation for those endpoints. Root cause was not established in this verification and remains assigned outside 16C until diagnosed (Finding F16C-01).

## Authentication Removal Verification

- `src/tender_intelligence/admin/auth.py` is deleted.
- `src/tender_intelligence/admin/main.py` has no auth middleware, actor resolver, session gate, or login route.
- `src/tender_intelligence/admin/api.py` route handlers do not depend on a current actor; configuration audits use `internal-admin-api`.
- `src/tender_intelligence/config/settings.py` no longer defines the test-auth setting.
- UI source and tests contain no required `devAuth`, `testAuth`, fake actor, role header, browser auth storage, login redirect, or identity controls.
- `TI_ADMIN_ENABLE_TEST_AUTH`, `TI_OIDC_*`, and `TI_AUTH_*` are absent from active configuration and `.env.example`.
- `.env` is ignored by Git (`git check-ignore .env` returned `.env`).
- `AdminUser` remains only as a reserved historical model/table; it is not used by the Admin UI/API.

## 401/403 Analysis

No 401 was observed from the current running Admin service or the focused unauthenticated route tests. `tests/ui/live-api.test.mjs` deliberately preserves unexpected upstream 401 as an error rather than falling back to fixtures; this does not create an app login path.

403 support remains in the client to represent deployment/network policy denial, consistent with `docs/15-admin-api.md`; it is not backed by application RBAC. However, `src/tender_intelligence/admin/static/app.mjs` still has `accessDeniedPanel()` copy telling a user to ask an authorized administrator to review their account role. If a deployment-layer 403 occurs, this copy implies a deferred identity/role system. This is stale misleading behavior (Finding F16C-02), not evidence of an active login gate.

## Safety Controls Verification

- **Test Mode:** actual `/api/v1/settings` returned `test_mode=true`; browser shell displayed “TEST MODE · ON”.
- **Mail safety:** focused tests verified test email is Test-Mode-gated, `[TEST]`-marked, restricted to the active development/alert recipient, audited, and uses an injected fake service. No real email/provider call was made.
- **Secrets:** adversarial sentinel-secret tests verified API responses, audit records, errors, logs, and encrypted storage do not reveal the secret. The UI API client has no auth token/header or browser storage.
- **Validation:** malformed/unknown secret-bearing configuration fields are rejected without reflecting submitted secrets.
- **Audit:** unauthenticated configuration actions are recorded with actor `internal-admin-api`; test-mail action is audited.
- **Dry-run:** source dry-run tests verify no Tender, RunHistory, verdict, notification, or attempt writes and zero notification-spy calls.

## Secret Safety Verification

No credentials were placed in the browser or verification evidence. Test sentinel values were synthetic and checked for leakage. `demo-evidence` artifacts contain no provider secrets. Source/provider authentication fields elsewhere are for source/mail integrations, not Admin application identity.

## Test Mode Verification

Test Mode remained ON in the live database/browser during read-only runtime inspection. Test-mail tests temporarily exercise the server-side setting in an isolated SQLite fixture and restore it within the fixture; the live setting was not changed. No business email was sent.

## Audit Verification

The no-login integration test confirmed settings changes persist and are audited with the stable non-human actor `internal-admin-api`. Test-email audit behavior and secret omission passed the focused test suite. Live `/api/v1/audit` returned 200.

## Worker Independence Verification

Ran `tests/integration/test_worker.py`, `tests/integration/test_orchestrator_pipeline.py`, `tests/integration/test_notification_service.py`, and `tests/integration/test_audit_security.py`: 54 passed. No Admin UI session, `devAuth`, login cookie, or human actor is used by worker execution. Test/provider doubles were used; no real mail was sent.

## Configuration Verification

`.env.example` contains `TI_ENV=development` and ordinary database/crypto/log/storage settings, but no Admin authentication, OIDC, or test-auth variables. `.env` is Git-ignored. O11 docs state that internal/private deployment is assumed and public exposure is not authorized. No obsolete authentication variable is required for app operation.

## Documentation Verification

O11 is consistently marked deferred/non-blocking in `docs/13-open-decisions.md`, `docs/09-admin-app-spec.md`, `docs/10-security-spec.md`, `docs/15-admin-api.md`, and the current next-task file. Historical source specification passages still mention proposed identity behavior but are superseded by the current O11 decision and are not active configuration. Prompt 17 is marked **not started**; the 16C scope amendment in its prompt only states the deferred boundary and does not implement hardening work.

Previous independent verification debt remains open and was not closed here: Prompt 14 has no current independent verification artifact; the Prompt 15 report records NEEDS FIXES; Prompt 16A report records NEEDS FIXES; Prompt 16B report records NEEDS FIXES. Prompt 16C does not change those statuses.

## Regression Testing

Commands executed from repository root:

1. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-16c-independent tests/integration/test_admin_api_prompt15.py tests/integration/test_admin_ui_api_prompt16b.py tests/integration/test_admin_api.py tests/ui` — **20 passed** (pytest collected Python tests; Node UI tests are separate).
2. `node --test tests/ui/*.test.mjs` — **26 passed, 0 failed**.
3. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-16c-worker tests/integration/test_worker.py tests/integration/test_orchestrator_pipeline.py tests/integration/test_notification_service.py tests/integration/test_audit_security.py` — **54 passed**.
4. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-16c-independent-full` — **618 passed, 1 failed**. The sole failure is the known unrelated `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`, where the fixture's `Date limite de dépôt...` text parsed to `None`. It is not an authentication failure and was not changed.

Browser runtime check: actual local UI loaded and rendered live health, settings shell, navigation, and Test Mode with no login. Tenders rendered a safe error panel on its API 500. No browser auth redirect or login flow occurred.

## Findings

### F16C-01 — Live Admin reads fail with generic 500

- **Severity:** High (operational blocker; not shown to be caused by 16C)
- **Area:** Live Admin API / current runtime database or API configuration
- **Observed behavior:** Actual `GET /api/v1/tenders` and `GET /api/v1/llm/usage` return 500 `internal_error`; the Tenders UI consequently shows “This information is unavailable.” Other sampled endpoints return 200. TestClient tests against isolated migrated SQLite return 200 for these routes.
- **Expected behavior:** Internal no-session requests to normal Admin reads should succeed when the service is healthy.
- **Evidence:** Read-only HTTP requests to `127.0.0.1:8000`; browser route `/#/tenders`; API integration tests pass in isolated DB.
- **Recommended action:** Diagnose the current running API/database state under the owning Prompt 15/runtime task before relying on these live screens. No 16C fix was made.

### F16C-02 — Stale identity/RBAC wording on deployment 403

- **Severity:** Medium
- **Area:** Admin UI access-denied error copy
- **Observed behavior:** `accessDeniedPanel()` says “Ask an authorized administrator to review your account role” when the client classifies 403 as access denied.
- **Expected behavior:** With application identity/roles deferred, a 403 should be described as a deployment/network access policy denial, not an account-role decision.
- **Evidence:** `src/tender_intelligence/admin/static/app.mjs` `accessDeniedPanel()` and `src/tender_intelligence/admin/static/api.mjs` 403 probe state.
- **Recommended action:** Replace the stale role-oriented message with neutral deployment-policy guidance in a remediation task. Not changed during verification.

## Final Gate

PROMPT 16C: NEEDS FIXES

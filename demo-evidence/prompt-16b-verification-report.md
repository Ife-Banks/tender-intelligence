# Prompt 16B — Independent Behavioral Verification Report

## A. Scope and verdict

Independently reviewed the current static Admin UI/API client, Prompt 15 API, test reports, and actual local browser behavior. No implementation or backend files were modified. No source dry-run, provider test, LLM request, mail send, or business mutation was invoked from the browser.

**Result: NEEDS FIXES.** The centralized API client, same-origin boundary, safe error conversion, and test/API suites are largely sound. The actual local UI correctly receives the API's unauthenticated response without falling back to fixture data. However, the live-mode shell visibly exposes fixture controls and the offline “nothing is sent” banner because CSS overrides their HTML `hidden` state. On this live API screen it also labels an unauthenticated visitor as Viewer and leaves Test Mode at “loading.” This is misleading for an operations interface and must be corrected before Prompt 17. Authenticated UI mutation paths could not be exercised because O11 has no configured identity provider and the UI does not pass the API client's optional test-auth headers.

## B. Sources and dependencies reviewed

- `PROJECT_RULES.md`
- `docs/09-admin-app-spec.md`, `docs/10-security-spec.md`, `docs/15-admin-api.md`
- `prompts/15-admin-api.md`, `prompts/16-admin-ui.md`, supplied Prompt 16B verification prompt
- `demo-evidence/prompt-15-verification-report.md` and JSON
- `demo-evidence/prompt-16a-verification-report.md` and JSON
- `demo-evidence/prompt-16b-api-integration-report.md` and test results
- Prompt 15 API source, UI source, tests, and current database migrations

Dependency status:

- **Prompt 15:** implementation present, but independent gate is `PROMPT 15: NEEDS FIXES — DO NOT START PROMPT 16`. Its report leaves PostgreSQL concurrent last-active development-recipient locking unverified; production identity O11 remains open and default auth fails closed.
- **Prompt 16A:** gate is `PROMPT 16A: NEEDS FIXES — DO NOT START PROMPT 16B`, due unverified tablet/mobile and browser-console checks (not a confirmed UI defect). The prior report found all ten fixture routes render.
- These dependency gaps are reported as such; they are not attributed to Prompt 16B.

## C. Repository baseline

- Commit: `6bac53b08cab57ce27b409a3deb6a0b138826482`.
- The working tree was already broadly modified/untracked. UI, API, reports, and tests are untracked relative to this commit, so Git cannot independently identify a clean Prompt 16B-only diff. Existing modified backend files include Prompt 15/API and configuration work; no backend files were changed during this verification.
- Prompt 16B report lists changes to `static/api.mjs`, `static/app.mjs`, `static/index.html`, UI contract tests, and `tests/integration/test_admin_ui_api_prompt16b.py`.
- Framework scan: no React/Next/Vue/Vite dependency was introduced. Browser API requests are centralized in `static/api.mjs`.
- Prior reported baselines: UI 24/24; Prompt 15 API 21/21; Prompt 16B UI-shaped integration 1/1. Independently rerun below: UI 24/24 and combined API suites 22/22.

## D. API boundary and endpoint mapping

`LiveAdminApi` is the only browser-side network boundary. It uses relative `/api/v1` paths, same-origin credentials, JSON headers/payloads, a 30-second abort timeout, structured error classification, safe user messages, and request/correlation ID extraction. Search found no browser storage, direct XHR, service worker, or `fetch` in `app.mjs`; the only browser `fetch` is inside `api.mjs`. Calls target same-origin Admin API routes; fixture `.invalid` URLs are synthetic values, not invoked.

| Screen/action | Actual Prompt 15 endpoint(s) | Source/test review |
|---|---|---|
| Health | `GET /health/dashboard` | UI render uses API fields; API route present. Live browser was stopped at 401. |
| Sources | `GET /sources`, `GET /sources/supported-types`, `POST /sources`, `PUT /sources/{id}`, `PATCH /sources/{id}/active`, `POST /sources/{id}/test` | UI/API source and Prompt 15 API tests; dry-run delegates to coordinator. No live dry-run invoked. |
| Tenders | `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/timeline`, Admin `GET /tenders/{id}/verdicts` | API source/report mapping matches. Search is local to fetched page because API has no text-search parameter. |
| Knowledge Base | Admin `GET /knowledge-base/versions`, `GET /knowledge-base/versions/{id}`, `GET /knowledge-base/diff`, `POST /knowledge-base/versions` | Client encodes selected file bytes as `filename`, `content_base64`, `note`; mock client test verifies. No live upload. |
| LLM | `GET/POST /llm/profiles`, `GET/PUT/DELETE /llm/profiles/{id}`, `GET/PUT /llm/roles`, `GET /llm/usage`, `POST /llm/profiles/{id}/test` | API code/tests match. LLM test dependency remains unavailable without injected factory; no provider call. |
| Recipients | `GET/POST /recipients`, `PUT/DELETE /recipients/{id}` | API validates and enforces last active dev-recipient guard. UI sends writable fields; guard tested through API. |
| Mail | `GET/POST /mail/providers`, `PUT /mail/providers/{id}`, `PATCH /mail/providers/{id}/active`, `POST /mail/providers/{id}/test`, `POST /mail/test` | Uses existing API/NotificationService seam; mail tests use mocks. No provider or email action invoked. |
| Triage | `GET/PUT /triage` | UI preserves null, empty list, and zero values in request shape; integration test persists them. Prompt 15 limitation: numeric unset clearing is not fully supported. |
| Settings | `GET/PUT /settings` | UI reloads after mutations; integration test verifies persisted setting payloads. Test Mode starts ON. No Test Mode mutation made. |
| Audit | `GET /audit?offset=&limit=` | API is Admin-only. UI filtering covers fields available in current DTO; filtering is current-page only. |

The endpoint paths, method/payload details, safe API error handling, and lower-level API behavior match the Prompt 15 route inventory inspected. The current test suite does not exercise every screen as a browser-driven authenticated session.

## E. Local browser/API verification

Created and migrated an isolated SQLite database under the verification scratch directory; started the repository's actual FastAPI Admin app on `127.0.0.1:8000` with `TI_ENV=development`, `TI_ADMIN_ENABLE_TEST_AUTH=false`, and no provider credentials. This changed no application source or project configuration.

Loaded `http://127.0.0.1:8000/admin/` in the browser. The real API returned 401 for its supported-types probe and health read. The UI rendered an “Authentication required” panel rather than rendering fixture tender/health records. This confirms fail-closed API behavior and that 401 does not select the fixture client.

**Observed live-shell defect:** although the client was live/auth-required, screenshot and accessibility tree still showed `DEMO DATA · OFFLINE`, the `OFFLINE PREVIEW` banner (“Nothing is sent…”), the Preview role selector, and fixture Scenario selector. The stylesheet sets `.demo-chip { display: ... }`, `.demo-banner { display: flex }`, and `.topbar-control { display: grid }` without a global `[hidden]` override, so those author rules defeat the browser's default hidden rendering. The UI also displayed `Role · viewer` for an unauthenticated visitor and left `TEST MODE · loading`; no settings request can resolve Test Mode before authentication. This is incorrect runtime state presentation. The backend still denied the request; no unauthorized data or mutation succeeded.

No authenticated Admin/Viewer browser session was available: O11 is unresolved, and `app.mjs` calls `createApi()` without the optional test-actor arguments. No login mechanism was invented. Consequently successful browser CRUD, server-refreshed state, and browser-level 403 behavior remain unverified. Prompt 15 route tests and the UI-shaped TestClient integration provide API-level evidence only.

## F. Screen integration and boundaries

The current UI source contains all ten route renderers and endpoint calls listed above. The unauthenticated local browser displayed only the Health shell and auth-required state; it did not exercise ten screens with live API data. The previous Prompt 16A fixture run independently rendered all ten routes, but that is not evidence of live API integration. No live API fallback to fixture data occurred on 401.

Auth/error mapping in the live client and tests:

- 401 → `auth_required`; browser panel displayed.
- 403 → `forbidden`/Viewer in client; backend Prompt 15 tests reject restricted routes and representative Viewer writes.
- 400/422, 404, 409, 429, 5xx → safe structured code mapping covered in client/API tests, without raw response text.
- network errors/timeouts are converted to safe `ApiError`s in source, but no browser-disconnected/timeout fixture was exercised in this run.
- malformed successful JSON returns `null`; the renderer may produce a generic safe failure, but malformed-response behavior lacks direct test coverage.

## G. Security, mutation, and external-boundary review

- No API keys/passwords/provider secrets found in fixtures/source; no localStorage/sessionStorage/IndexedDB use found. Auth uses same-origin credentials; secret values in write DTOs are cleared after successful save in UI code. Prompt 15 tests validate write-only secret serializers and safe validation errors.
- No direct third-party/provider/source fetch paths were found. Only same-origin API requests are made by the live client. No provider, WAHO, mail server, or production endpoint was contacted.
- Viewer UI hides privileged routes when role is actually Viewer; API authorization remains authoritative. Prompt 15 integration tests prove Viewer writes and Admin-only resources are rejected. Auth-required initial state is mislabeled as Viewer, but server authorization did not fail open.
- Test Mode was ON in the local migrated settings defaults and no mutation was made. No source, recipient, provider, KB, or settings writes were performed through the browser. Prompt 15 API tests verify those service-level write/audit contracts; the Prompt 16B persistence integration uses injected actors and migrated SQLite.
- No confirmation cancel/submit flows or duplicate-click recovery were exercised in the live browser. UI tests/static code show confirmation and button disabling for several operations, but the full mutation matrix is not covered by the one persistence integration test.

## H. Tests and regression

Commands run in this verification:

1. `node --test tests/ui/*.test.mjs` — **24 passed, 0 failed**.
2. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-16b-verify-api tests/integration/test_admin_api_prompt15.py tests/integration/test_admin_ui_api_prompt16b.py` — **22 passed, 0 failed**.
3. `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-16b-verify-reg -k 'not test_date_limite_july_gmt'` — **621 selected, all passed**.
4. `node --check src/tender_intelligence/admin/static/app.mjs` and `node --check src/tender_intelligence/admin/static/api.mjs` — passed.
5. `git diff --check` — passed; only existing LF/CRLF warnings in unrelated modified files.

The full suite's known excluded test is `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` (mojibake in Windows fixture). Prompt 15's independently reported 620-test run had that one failure and the PostgreSQL recipient concurrency gap. No worker/source/pipeline/mail backend files were edited in this verification.

## I. Findings by severity

1. **HIGH — live shell misrepresents live/auth-required mode.** `[hidden]` is defeated for offline banner and demo controls by CSS display declarations; visitor is presented as Viewer and Test Mode “loading” when actually unauthenticated. This can mislead operators around real-vs-simulated side effects and role. Backend still fails closed. Ownership: Prompt 16B UI.
2. **HIGH / dependency — authenticated UI integration not independently exercised.** O11 remains open, so real Admin identity is unavailable; the current UI does not consume the client’s optional local test auth arguments. API TestClient tests exercise writes but cannot prove live browser post-mutation state. Ownership: O11/verification environment plus UI test seam; do not resolve O11 here.
3. **MEDIUM — API/UI live failure coverage incomplete.** The UI suite mocks status mapping but lacks direct browser E2E for API 400/403/404/409/500, offline timeout, malformed response, and no-false-success mutation recovery. The API-level suites cover many backend cases.
4. **Prompt 15 dependency — not fully verified.** PostgreSQL concurrency behavior for last-active dev-recipient protection remains unproven in the Prompt 15 verification report.
5. **Prompt 16A dependency — not fully verified.** Tablet/mobile rendered layouts and direct console review remain outstanding from Prompt 16A verification.

## J. Final gate

The API client passes its tests and the actual API rejects unauthenticated access, but the browser's live shell is materially misleading and a successful authenticated browser path was not available. Prompt 15 is also not independently verified. Do not proceed to Prompt 17 based on this evidence.

PROMPT 16B: NEEDS FIXES — DO NOT START PROMPT 17

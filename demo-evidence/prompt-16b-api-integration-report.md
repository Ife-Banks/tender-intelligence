# Prompt 16B — Admin UI API Integration Report

## 1. Scope

Connected the Prompt 16A plain HTML/CSS/ES-module interface to the existing Prompt 15 Admin API. No API routes or business logic were added. No Admin identity provider, source crawler, LLM provider, Sendlib provider, or mail-delivery code was implemented in the browser.

## 2. Specifications reviewed

- `PROJECT_RULES.md`
- `docs/09-admin-app-spec.md`
- `docs/10-security-spec.md`
- `docs/15-admin-api.md`
- `prompts/15-admin-api.md`
- `prompts/16-admin-ui.md`
- Prompt 16A implementation report and current static UI/API client
- Prompt 15 API source and `tests/integration/test_admin_api_prompt15.py`
- Prompt 16B requirements pasted by the operator

## 3. Implementation reviewed/changed

- `src/tender_intelligence/admin/static/api.mjs`: same-origin versioned API boundary, safe error parsing, session-state classification, request timeout, JSON methods, and KB byte upload matching `content_base64` API schema.
- `src/tender_intelligence/admin/static/app.mjs`: ten screens load from `createApi()`. Live mode uses server responses and server authorization; fixture mode is labelled and shown only when the API is unavailable or the API route is absent. Actions refresh authoritative state after successful writes. Buttons prevent repeat submissions. Secret inputs are reset after successful saves.
- `src/tender_intelligence/admin/static/index.html`: live connection, actor role, and Test Mode indicators; fixture-only controls are hidden in live mode.
- `tests/ui/live-api.test.mjs`, `tests/ui/screen-contract.test.mjs`: live client and screen contract checks.
- `tests/integration/test_admin_ui_api_prompt16b.py`: Prompt 15 API test-client exercise using UI-shaped request payloads and migrated SQLite state.

## 4. Endpoint map (actual Prompt 15 contract)

| UI screen/action | Endpoint | Request/response handling |
|---|---|---|
| Health | `GET /api/v1/health/dashboard` | Renders server health, source history, verdict counts, notification failures, provider chain, stuck queue, and open alerts. Browser does not calculate health state. |
| Sources | `GET /sources`, `GET /sources/supported-types` | Uses `items` and `pagination`; source fields use API DTO names. |
| Create/edit source | `POST /sources`, `PUT /sources/{id}` | Sends only writable source fields; omits unchanged write-only auth. |
| Enable/disable source | `PATCH /sources/{id}/active` | Sends `{active}` and reloads the list. |
| Test source | `POST /sources/{id}/test` | Uses the Prompt 15 dry-run result (`candidate_count`, `dry_run`, `persisted`, `email_sent`, `stages`, `planned_actions`, correlation ID). Live mode warns that the configured adapter may fetch the source site. |
| Tender list | `GET /tenders?offset=&limit=&source_id=&status=&recommendation=` | Uses actual server-side filters. Title/external-ID search is local to the currently loaded page because Prompt 15 has no search parameter. |
| Tender details/timeline | `GET /tenders/{id}`, `GET /tenders/{id}/timeline` | Timeline is rendered from API events; correlation, tender, run, stage, and event timestamps are retained. Verdict history uses `GET /tenders/{id}/verdicts` for Admin. |
| Knowledge Base | `GET /knowledge-base/versions`, `POST /knowledge-base/versions`, `GET /knowledge-base/versions/{id}`, `GET /knowledge-base/diff?from_id=&to_id=` | Upload transmits selected file bytes as base64 with filename/note; Admin-only API remains authoritative. |
| LLM | `GET /llm/profiles`, `POST /llm/profiles`, `PUT/DELETE /llm/profiles/{id}`, `GET /llm/roles`, `PUT /llm/roles/{role}`, `GET /llm/usage`, `POST /llm/profiles/{id}/test` | Write-only API key; approval confirmation; real provider test is confirmed and uses API response. An unavailable test dependency is shown from the API error. |
| Recipients | `GET /recipients?list_type=`, `POST /recipients`, `PUT/DELETE /recipients/{id}` | PUT sends only schema fields, never response-only IDs/timestamps. The API enforces the final active dev-recipient rule and its error is shown. |
| Mail | `GET /mail/providers`, `POST /mail/providers`, `PUT /mail/providers/{id}`, `PATCH /mail/providers/{id}/active`, `POST /mail/providers/{id}/test`, `POST /mail/test` | Provider credentials are write-only. Test-email actions explicitly confirm that a real `[TEST]` email will be sent; no retry, failover, or delivery behavior is implemented in JavaScript. |
| Triage | `GET /triage`, `PUT /triage` | Preserves null/unset, empty list, and numeric zero as distinct submitted values. Does not run triage in the browser. |
| Settings | `GET /settings`, `PUT /settings` | Reads the actual Test Mode and displays it in the shell. Turning Test Mode off requires a reason and confirmation; after API success the settings are reloaded. |
| Audit | `GET /audit?offset=&limit=` | Uses API pagination and filters the current page by actor/entity/target/changed fields. Read-only. |

## 5. Authentication and Viewer/Admin behavior

- Requests use same-origin credentials (including configured session cookies); no access token is stored in browser storage or placed in a URL.
- `401` stays in live mode and displays authentication-required state. If a request later returns `401`, the shell changes to signed-out state. `403` is surfaced as access denied; a normal Viewer role is detected through the Admin-only audit probe and gets Viewer navigation.
- KB and audit navigation, secret-management controls, and mutations are hidden for Viewer presentation. API authorization remains authoritative if a restricted call is attempted.
- Prompt 15 currently fails closed by default while O11 is unresolved. This task deliberately did not add login or weaken the boundary. The actual default application therefore returns `401` until an approved `actor_resolver`/identity integration is configured. A local test-header seam exists in the API but is not exposed as a production UI control.

## 6. Safety and secret handling

- No browser `localStorage`/`sessionStorage`, direct `fetch` outside `api.mjs`, dynamic HTML insertion, inline styles, provider credentials in URLs, or raw response-body rendering.
- Known API error codes and safe field messages are displayed; raw error bodies/stacks are discarded. Correlation/request ID is displayed when returned in a supported header/body field.
- LLM, mail, and source tests are explicit operator actions. The verification suite used mocked providers and a synthetic source adapter; no external provider or tender website was contacted and no email was sent.
- Live mail test sends a real `[TEST]` email via Prompt 15 to the active dev-alert recipient selected by the API. Provider-test endpoint chooses the API's first active dev-alert recipient; UI does not invent a recipient-selection parameter that the endpoint does not support.

## 7. Persistence and integration evidence

`tests/integration/test_admin_ui_api_prompt16b.py` submits UI-shaped settings, triage, source, recipient, and mail-provider writes to the real FastAPI router using a migrated SQLite database. It reloads and checks persisted settings, triage values (including empty and zero), source state, recipient state, provider state, and configuration audit entries.

The 21-case Prompt 15 Admin API integration suite also passed. It verifies fail-closed/default auth, Viewer restrictions, source dry-run non-persistence, Test Mode audit, recipient safety, encrypted/write-only secrets, mocked LLM testing, test-mail Test Mode gating/audit, timeline reconstruction, and UI asset serving. No real provider calls were made.

## 8. API mismatches / limitations retained

These are Prompt 15/API-contract or open-decision limitations; this UI task did not silently change the backend:

1. **O11 authentication remains open.** The default app has no configured identity provider and returns 401. UI correctly preserves fail-closed behavior but cannot authenticate an operator until the approved resolver is configured.
2. **Tender DTO is narrower than the Admin UI spec.** Current tender list/detail DTOs do not expose document state, triage state, notification state, or `incomplete_inputs`; the UI reports unavailable/not provided and does not infer those fields. Timeline reflects only events the API returns.
3. **Audit DTO is narrower than the screen spec.** Current API returns `id`, actor, entity, entity ID, changed fields, and timestamp; it does not return a separate action, outcome, or correlation/config-change ID. Filtering is therefore limited to available fields/current page.
4. **Server-side tender search is absent.** Search operates only over the currently loaded page; no endpoint or backend contract was invented.
5. **Unset mutations for numeric triage settings are not fully supported by Prompt 15.** Its update handler ignores `None` for relevance threshold/urgency window, so the UI keeps current values and reports that a blank cannot clear them. The API needs a deliberate PATCH/clear contract to make these configurable back to unset.
6. Prompt 15's LLM connection test reports `llm_test_unavailable` when its client factory is not configured; UI presents the safe API result rather than simulating success.

## 9. Test results

- UI tests: 24 passed, 0 failed.
- Prompt 15 Admin API integration: 21 passed, 0 failed.
- Prompt 16B UI-shaped persistence integration: 1 passed, 0 failed.
- Full backend suite: one unrelated pre-existing failure in `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` (French July/GMT deadline parser returned `None`). The full suite excluding that test passed. No deadline/parser files were changed.
- `node --check` on `api.mjs` and `app.mjs`: passed.
- Ruff on new Prompt 16B integration test: passed.
- `git diff --check`: passed (only pre-existing line-ending warnings in unrelated modified files).

## 10. Remaining work / ownership

- Identity-provider integration for O11: Admin API/authentication ownership; deferred by task instruction.
- Missing tender and audit response fields and numeric-triage clear semantics: Prompt 15 API contract/implementation owners.
- French WAHO deadline parser regression: source/deadline implementation owner; unrelated to Prompt 16B.
- Prompt 17 has not been started.

## Final gate

PROMPT 16B: IMPLEMENTED — READY FOR INDEPENDENT BEHAVIORAL VERIFICATION

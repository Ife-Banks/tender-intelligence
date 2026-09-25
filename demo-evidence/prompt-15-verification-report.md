# Prompt 15 — Independent Behavioral Verification

## A. Scope and source material

Verified the Prompt 15 Admin API implementation as a read/control layer over existing
services and persisted state. No Prompt 16 UI, Admin identity provider, live LLM, or live
email provider was implemented or exercised. No business email was sent.

Reviewed `PROJECT_RULES.md`; `docs/09-admin-app-spec.md`; `docs/10-security-spec.md`;
`docs/13-open-decisions.md` (O3/O4/O9/O11/O12); `docs/15-admin-api.md`;
`prompts/15-admin-api.md`; implementation state/decision/known-issue/next-task documents;
and the Admin, notification, pipeline coordinator, configuration, audit, and model code.
O11 (Admin authentication/provider and account provisioning) remains open. Prompt 14 has
not been independently verified; this API does not claim otherwise.

## B. Implementation and ownership reviewed

- `src/tender_intelligence/admin/api.py`: endpoint handlers, Pydantic request schemas,
  actor guards, resource serializers, audit writes, dashboard aggregation, source dry-run,
  and mail/LLM tests.
- `src/tender_intelligence/admin/auth.py`: injectable actor resolution and local-only test
  authentication.
- `src/tender_intelligence/admin/main.py`: application lifecycle and composition of the
  existing `RunCoordinator` for source dry-run; shared DB and storage wiring.
- `src/tender_intelligence/config/settings.py`: environment-name and test-auth settings.
- `src/tender_intelligence/notifications/service.py`: test-only send through the existing
  Test Mode policy, provider chain, and persistence seams.
- Existing `TimelineService`, `RecipientGuard`, configuration audit service, SQLAlchemy
  models, repositories, `RunCoordinator`, and `NotificationService` were inspected as the
  owning seams.

Prompt 15 owns API serialization, authorization boundary, input validation, and invoking
those existing services. It does not own crawl logic, notification retry policy, verdict
generation, provider approval decisions, or identity-provider selection. No second
configuration or retry system was found.

## C. Behavioral matrix

| Scenario | Expected | Observed | Result / evidence |
|---|---|---|---|
| Default auth | Fail closed | 401 without injected actor | PASS; API integration tests |
| Invalid actor identity/role | Reject actor | 401 | PASS; parameterized actor tests |
| Viewer reads operational data | Read-only | Permitted through viewer DTOs | PASS; endpoint authorization tests |
| Viewer writes settings/config | Denied | 403 | PASS; settings write test |
| Viewer reads verdict detail, KB metadata/content, audit | Admin-only | 403 | PASS; privileged-route tests |
| Admin config mutation | Shared row + audit | Persisted and audit fields match | PASS; settings/profile tests |
| Secret input/response/audit/error | Never echo/persist plaintext | Sentinel absent; write-only serializers and safe errors | PASS; adversarial redaction test |
| Invalid request values | 422 without submitted value | Safe validation response | PASS; validation and email schema tests |
| Source dry-run | Existing coordinator; no pipeline writes | `DRY_RUN`, no run history, no tender/run/notification writes | PASS; actual coordinator API integration |
| Source dry-run notification side effect | None | Notification spy remained at zero calls | PASS |
| Test email in Test Mode | Dev recipient only; marked test; persist provider attempt/log | Existing NotificationService test path, `[TEST]`, non-tender log/attempt | PASS with provider mocks; no live send |
| Failed test email | Safe failure and audit | 502 safe code; failed audit persisted | PASS |
| LLM connection test | Record only actual invocation | Injected mock records one call; absent factory returns safe unavailable and no LLMCall | PASS; no production LLM call |
| Timeline | Reconstruct persisted correlation events | Timeline returned for persisted tender correlation | PASS |
| Test-mail operational health | Exclude test-only failures/stuck rows | Test failures excluded; tender failure retained | PASS; regression added in this verification |
| Test Mode default | Remain enabled unless authorized config change | Test-email service rejects disabled mode; API config endpoint itself permits Admin change | PASS for send gate; noted API is a configuration surface |

### Route/role boundary

All `/api/v1` routes require an actor. Viewer access is read-only for sources, tenders,
timeline, dashboard, shared settings/triage, redacted LLM profiles/roles/usage, redacted
recipients/providers. Writes, source/provider tests, verdict detail, KB version metadata and
content, and audit are Admin-only. Unauthorized requests returned 401/403 in behavioral
tests. Lists use bounded pagination where applicable. Request DTOs reject unknown keys.

## D. Source dry-run persistence proof

The test invokes `POST /api/v1/sources/{id}/test`, which now delegates to
`RunCoordinator.run_source(source_id, dry_run=True)`. It asserts the source adapter actually
made discovery requests and checks database counts before and after for Tender, RunHistory,
Verdict, NotificationLog, and NotificationAttempt, plus `Source.last_run_at`. Counts were
unchanged; `run_history_id` is absent; response has `persisted=false` and `email_sent=false`;
notification spy count is zero. Dry-run candidate count and planned actions are returned.

Limitation: `RunReport` currently supplies summary counts/actions, not each discovered
candidate's title/URL. The Admin API does not duplicate discovery to manufacture a preview.
If the Admin product contract requires a per-candidate preview, the shared Prompt 10
`RunReport` seam must be extended by its owner before that detail can be exposed.

## E. Persistence, configuration, and audit

Tests inspect reloaded SQLAlchemy rows, not only response DTOs. Settings and LLM profile
approval changes persist to the same shared models and audit rows. Test-email success/failure
persists NotificationLog/NotificationAttempt and audit data without tender/verdict identity.
The mail test service enforces Test Mode and `dev_alert` recipient validation. No migration or
parallel settings store was introduced.

Test-only test-mail logs are excluded from the tender operations failure/stuck counters; a
new test asserts a test failure is ignored while a tender notification failure still counts.

## F. Secret and privacy findings

Secret-bearing configuration DTOs are write-only and encrypt values through the existing
master-key mechanism. Viewer DTOs omit recipient addresses, KB contents, and sensitive
profile/provider metadata. Audit stores changed field names/status rather than values.
Adversarial sentinel tests checked API responses, validation errors, audit serialization,
and safe provider errors. LLM custom plaintext `extra_headers` are rejected because the
existing profile model has no encrypted header-secret field.

## G. Defects found and Prompt 15 fixes

1. Viewer access to KB version metadata and audit exceeded the documented boundary. Both are
   now Admin-only; authorization regression tests cover the routes.
2. Actor resolver accepted empty identity/unsupported role objects. It now validates actor
   identity and role and returns 401 on invalid values.
3. Email fields lacked shared recipient-address validation. Recipient and provider DTOs now
   use the existing `is_valid_email` validator.
4. Generic settings accepted arbitrary triage/alert mappings, permitting secret-like values
   into normal settings/audit. Triage remains on its dedicated API; alert thresholds accept
   only the supported numeric warning share; stored nested settings serialize safely.
5. LLM profile `extra_headers` allowed plaintext credential-like values. The unsupported
   field is rejected until an encrypted model seam exists.
6. Whitespace-only triage keyword/sector/region values were accepted; validation now rejects
   blank entries.
7. Source dry-run previously bypassed RunCoordinator; it now invokes the authoritative
   Prompt 10 dry-run boundary. The default app composes the existing shared pipeline in its
   lifecycle. Database and notification spies confirm no run persistence or sends.
8. Test-email-only rows polluted tender operational health counters; excluded them and added
   a regression test.
9. Duplicate names on source/profile/provider updates needed conflict handling; API now
   translates uniqueness violations to safe 409 codes. Profile/provider audit metadata
   reports whether credentials remain configured separately from whether they were updated.

No live service call was made to test external connectivity. Mock tests establish the API
contract and existing service behavior, not provider account validity.

## H. Regression results and limits

Focused combined tests for `test_admin_api_prompt15.py`, legacy `test_admin_api.py`, and
`test_notification_service.py` passed when run with a workspace-local pytest temp directory.
Ruff passed on changed Admin/config/notification modules and tests. An initial focused pytest
attempt failed during fixture setup because the host's shared `%TEMP%\pytest-of-ifeol`
directory denied access; rerun with `--basetemp=.verification-tmp` passed. This was an
environment setup issue, not a product failure.

The complete `tests` suite is in progress; the result is recorded in
`prompt-15-test-results.txt` and will be reflected in the machine-readable report. The prior
implementation run had 609 passes and one unrelated French WAHO deadline-parser failure
(`tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`).
Prompt 15 does not own that parser. The suite must be re-evaluated after completion.

## I. Remaining issues and readiness

- **O11 blocks production authentication** until an approved identity provider/account
  provisioning strategy is selected and injected. Local test auth is disabled by default and
  must never serve as production auth.
- The LLM connection-test endpoint has no concrete factory wired by the application; it
  truthfully returns unavailable, and this verification used only an injected mock.
- Mail provider connectivity was not tested against an actual Sendlib account. Test Mode,
  provider chain, and persistence paths were exercised with mocks. A valid operator-owned
  test configuration is still required.
- Source candidate preview is summary-only under the current `RunReport` contract.
- Prompt 14 remains independently unverified.

Prompt 16 must not treat the Admin API as fully verified until the last-active development
recipient mutation is exercised concurrently against PostgreSQL. O11, concrete LLM provider
composition, live mail connectivity, and Prompt 14 verification remain upstream and are
handled fail-closed; they do not invalidate the API's offline safety results. The independent
verification is incomplete because SQLite cannot prove the PostgreSQL row-lock invariant.

## Endpoint matrix and configuration coverage

Every route below was inspected for role guard, handler behavior, and audit policy. Routes
tagged `behaviorally exercised` were invoked by the focused integration tests; other routes
received code/DTO/auth review and shared authorization-path coverage. Mutation audit is
required for each create/update/delete/configuration operation; read routes are not audited.

| Method/path | Role | Expected and observed behavior | Audit | Evidence |
|---|---|---|---|---|
| GET `/sources/supported-types` | Admin/Viewer | registered type DTO | No | Code review |
| GET `/sources` | Admin/Viewer | paginated source list | No | Code review |
| POST `/sources` | Admin | creates shared row; credentials encrypted | Yes | Code review |
| GET `/sources/{id}` | Admin/Viewer | safe detail; auth redacted | No | Code review |
| PUT `/sources/{id}` | Admin | updates row; duplicate name safe 409 | Yes | Code review |
| PATCH `/sources/{id}/active` | Admin | updates shared active state | Yes | Code review |
| POST `/sources/{id}/test` | Admin | coordinator dry-run; no DB/email side effect | No | Behaviorally exercised |
| GET `/tenders` | Admin/Viewer | paginated/filterable safe projection | No | Code review |
| GET `/tenders/{id}` | Admin/Viewer | safe tender projection | No | Code review |
| GET `/tenders/{id}/timeline` | Admin/Viewer | persisted correlation timeline | No | Behaviorally exercised |
| GET `/tenders/{id}/verdicts` | Admin | verdict projection | No | Route guard/code review |
| GET `/health/dashboard` | Admin/Viewer | persisted operational counters | No | Behaviorally exercised |
| GET/PUT `/settings` | Read: Admin/Viewer; write: Admin | same shared Settings; write audited | Write | Behaviorally exercised |
| GET/PUT `/triage` | Read: Admin/Viewer; write: Admin | same triage configuration; write audited | Write | DTO/route review |
| GET/POST `/llm/profiles` | Read: Admin/Viewer; write: Admin | Viewer-redacted list; encrypted secret on create | Create | Secret test + route review |
| GET/PUT/DELETE `/llm/profiles/{id}` | Read: Admin/Viewer; write: Admin | shared profile; assigned profile deletion conflicts | Write | Approval/secret tests + review |
| GET `/llm/roles` | Admin/Viewer | shared role assignments | No | Code review |
| PUT `/llm/roles/{role}` | Admin | shared role assignment; audited | Yes | Code review |
| GET `/llm/usage` | Admin/Viewer | persisted usage summary | No | Code review |
| POST `/llm/profiles/{id}/test` | Admin | injected mock logged; missing factory safe unavailable | Yes/LLMCall | Behaviorally exercised |
| GET/POST `/recipients` | Read: Admin/Viewer; write: Admin | Viewer address redaction; validated create | Create | Route review |
| PUT/DELETE `/recipients/{id}` | Admin | existing last-dev guard; duplicate email safe conflict | Yes | Last-active sequential test + review |
| GET `/mail/providers` | Admin/Viewer | ordered chain DTO; Viewer redacted | No | Route review |
| POST/PUT `/mail/providers` | Admin | shared provider; encrypted credentials; duplicate safe 409 | Yes | Secret test + review |
| PATCH `/mail/providers/{id}/active` | Admin | shared active state | Yes | Route review |
| POST `/mail/providers/{id}/test` | Admin | existing provider test seam | Yes | Route review; no live provider |
| POST `/mail/test` | Admin | Test Mode/dev recipient; `[TEST]`; existing service chain | Yes | Mock success/failure tests |
| GET `/knowledge-base/versions` | Admin | version metadata only | No | Viewer-denial test |
| GET `/knowledge-base/versions/{id}` | Admin | KB content | No | Route guard review |
| POST `/knowledge-base/versions` | Admin | validated immutable upload | Yes | Route review |
| GET `/knowledge-base/diff` | Admin | KB diff | No | Route guard review |
| GET `/audit` | Admin | paginated audit | No | Viewer-denial test |

Configuration reads/writes resolve through existing `Setting`, `Source`, `LLMProfile`,
`LLMRoleAssignment`, `Recipient`, `MailProvider`, and `KnowledgeBaseVersion` records.
Admin API does not make a second config store. Test Mode stays enforced by the test-send
service. An Admin can deliberately update the shared Test Mode setting via settings API;
no verification test disabled it or sent business email.

## Concurrency evidence

The last-active development recipient guard locks seeded singleton `Setting(id=1)` with
`SELECT ... FOR UPDATE`, then recounts active recipients. Migration 0001 seeds that singleton.
The code serializes competing mutations on PostgreSQL, but the available SQLite test DB
ignores row locks. The verification exercised sequential deletion/disable rules and existing
NotificationService concurrent delivery tests, but did not prove Admin recipient mutation
under concurrent PostgreSQL transactions. This is a remaining verification gap.

## Updated test comparison

The prior implementation report recorded 38 focused passes and 609 passes + 1 unrelated
failure. The new complete run collected 620 tests: 619 passed, 1 failed (the same French
WAHO deadline parser test due mojibake in the Windows fixture). The focused combined run of
Admin API + legacy Admin health + NotificationService passed 48 tests after the final
uniqueness conflict mapping edits. Ruff passed after all edits.

## Final assessment

Prompt 15 implementation is present. Its API surfaces and principal safety boundaries are
independently verified with live in-process requests, persisted-state checks, and mocks.
Production identity (O11), concrete LLM provider composition, live mail connectivity, and
Prompt 14 verification are external dependencies and are handled fail-closed. The API can be
verified without those services. One explicitly requested behavior—the PostgreSQL concurrent
last-active recipient invariant—remains unproven, so independent verification is incomplete.
Do not conflate implementation completion, independent verification, and deployment
readiness.

## J. Final gate

PROMPT 15: NEEDS FIXES — DO NOT START PROMPT 16

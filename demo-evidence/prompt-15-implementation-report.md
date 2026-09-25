# Prompt 15 — Admin API implementation report

## Scope and specification mapping

Implemented the authenticated Admin API surface described in `prompts/15-admin-api.md`,
primarily `docs/09-admin-app-spec.md` §§9.1–9.7, over existing shared models and service
boundaries. The implementation also follows `docs/03-data-model.md`, `docs/04-pipeline-spec.md`,
`docs/07-ai-verdict-spec.md`, `docs/08-email-notification-spec.md`, and
`docs/10-security-spec.md`. No Admin UI, worker pipeline, verdict logic, RAG, or database
migration was added.

## Files changed

- `src/tender_intelligence/admin/api.py` — versioned route definitions, validation, role
  checks, explicit response DTOs, and safe serialization.
- `src/tender_intelligence/admin/auth.py` — injectable actor resolution; default fail-closed
  behavior and opt-in local/test header actor.
- `src/tender_intelligence/admin/main.py` — application lifecycle, service injection, API
  router, liveness route, and safe exception serialization.
- `src/tender_intelligence/config/settings.py`, `.env.example` — documented local test-auth
  and upload-size settings; `TI_ENV` alias is honored.
- `src/tender_intelligence/notifications/service.py` — `send_test_email()` using the existing
  provider chain, Test Mode, active dev recipient guard, attempt repository, and breaker
  persistence; no tender notification retry queue is created for this test operation.
- `tests/integration/test_admin_api_prompt15.py` — offline API behavior, role boundary,
  secret handling, dry-run behavior, persistence, Test Mode routing, and safe failure tests.
- `tests/integration/test_notification_service.py` — test-email provider-chain and safety
  tests.
- `docs/15-admin-api.md` — route, auth, configuration, and limitation reference.
- `demo-evidence/prompt-15-implementation-mapping.md` — mapping to existing domain seams.

No migration was required because these routes use the existing schema.

## Implemented behavior

- Authenticated read/write surface for sources, tenders, timeline, health, settings, triage,
  LLM profiles/roles/usage, recipients, mail providers, KB versions/diffs, and audit.
- Viewer access is read-only; verdict and KB content are Admin-only. Responses use hand-built
  DTOs and redact credentials/private metadata.
- Configuration changes use existing rows and `ConfigChangeLog`; secret inputs use the
  existing encryption seam and are write-only.
- Source test delegates to the composed `RunCoordinator.run_source(..., dry_run=True)` seam;
  the current response contains summary counts/actions, and the independent verification
  confirms no tender/run/notification persistence or send.
- Mail tests require Test Mode ON and an active `dev_alert` recipient, mark subject/body with
  `[TEST]`, use the existing provider chain, and persist a non-tender NotificationLog and
  provider attempts. Provider failure is audited and returned as a safe 502.
- KB upload validates file type/size and stores extracted content as an immutable version.
- Request validation and server errors do not echo request values or raw exception details.

## Endpoint inventory

All endpoints below use `/api/v1`; every endpoint requires an authenticated actor. `Viewer`
may read operational records; `Admin` is required for writes, audit, verdict details, and KB
content. Requests use strict Pydantic DTOs; response bodies are explicit DTOs, not ORM models.

| Method + path | Role | Purpose | R/W | Audited | Secret-bearing |
| --- | --- | --- | --- | --- | --- |
| `GET /sources/supported-types` | Admin/Viewer | registered source types | R | No | No |
| `GET /sources` | Admin/Viewer | paginated source list | R | No | No |
| `POST /sources` | Admin | create source | W | Yes | Auth input, encrypted/write-only |
| `GET /sources/{source_id}` | Admin/Viewer | source detail | R | No | No; auth redacted |
| `PUT /sources/{source_id}` | Admin | update source | W | Yes | Auth input, encrypted/write-only |
| `PATCH /sources/{source_id}/active` | Admin | enable/disable | W | Yes | No |
| `POST /sources/{source_id}/test` | Admin | discovery dry-run | R/dry-run | No | No |
| `GET /tenders` | Admin/Viewer | paginated/filterable tenders | R | No | No; notice data limited |
| `GET /tenders/{tender_id}` | Admin/Viewer | tender detail | R | No | No; safe projection |
| `GET /tenders/{tender_id}/timeline` | Admin/Viewer | persisted timeline | R | No | No; safe projection |
| `GET /tenders/{tender_id}/verdicts` | Admin | verdict history/detail | R | No | No |
| `GET /health/dashboard` | Admin/Viewer | persisted operation counters | R | No | No |
| `GET /settings` | Admin/Viewer | shared settings | R | No | No |
| `PUT /settings` | Admin | update settings/Test Mode | W | Yes | No |
| `GET /triage` | Admin/Viewer | triage settings | R | No | No |
| `PUT /triage` | Admin | update triage settings | W | Yes | No |
| `GET /llm/profiles` | Admin/Viewer | profile list (Viewer is redacted) | R | No | No |
| `POST /llm/profiles` | Admin | create profile | W | Yes | API key encrypted/write-only |
| `GET /llm/profiles/{profile_id}` | Admin/Viewer | profile detail (Viewer is redacted) | R | No | No |
| `PUT /llm/profiles/{profile_id}` | Admin | update profile | W | Yes | API key encrypted/write-only |
| `DELETE /llm/profiles/{profile_id}` | Admin | delete unused profile | W | Yes | No |
| `GET /llm/roles` | Admin/Viewer | list role assignments | R | No | No |
| `PUT /llm/roles/{role}` | Admin | assign profile/fallback | W | Yes | No |
| `POST /llm/profiles/{profile_id}/test` | Admin | minimal connection test | W/test | LLMCall | No KB; provider secret not returned |
| `GET /llm/usage` | Admin/Viewer | persisted usage summary | R | No | No |
| `GET /recipients` | Admin/Viewer | recipient list (Viewer emails redacted) | R | No | No |
| `POST /recipients` | Admin | create recipient | W | Yes | Recipient PII, write-only to Viewer |
| `PUT /recipients/{recipient_id}` | Admin | update recipient | W | Yes | Recipient PII, write-only to Viewer |
| `DELETE /recipients/{recipient_id}` | Admin | delete recipient | W | Yes | No |
| `GET /mail/providers` | Admin/Viewer | provider list (Viewer redacted) | R | No | No |
| `POST /mail/providers` | Admin | create provider | W | Yes | Credentials encrypted/write-only |
| `PUT /mail/providers/{provider_id}` | Admin | update provider | W | Yes | Credentials encrypted/write-only |
| `PATCH /mail/providers/{provider_id}/active` | Admin | activate/deactivate provider | W | Yes | No |
| `POST /mail/providers/{provider_id}/test` | Admin | test selected mail provider | W/test | Yes | Uses provider credential internally; never returned |
| `POST /mail/test` | Admin | send explicitly marked test email | W/test | Yes | No |
| `GET /knowledge-base/versions` | Admin | version metadata/history | R | No | No KB text |
| `GET /knowledge-base/versions/{version_id}` | Admin | KB content detail | R | No | Company KB content |
| `POST /knowledge-base/versions` | Admin | upload a new immutable KB version | W | Yes | Company KB content |
| `GET /knowledge-base/diff` | Admin | compare KB versions | R | No | Company KB excerpts |
| `GET /audit` | Admin | paginated audit entries | R | No | Secret values are excluded/redacted |

Request schemas are `SourceWrite`, `SettingsWrite`, `TriageWrite`, `LLMProfileWrite`,
`RoleWrite`, `RecipientWrite`, `MailProviderWrite`, `KBWrite`, and `TestEmailWrite` in
`src/tender_intelligence/admin/api.py`. Success responses use explicit resource DTOs,
`items` + `pagination` for lists, and correlation/status fields for tests. Common safe errors
include 401/403, 404, 409, 413, 415, 422, 502, and 503 with machine-readable error codes.
The live OpenAPI schema exposes the exact serialized shapes.

## Authorization matrix

| Capability | Admin | Viewer |
| --- | --- | --- |
| Browse source/tender/health/timeline/settings/triage/usage summaries | Yes | Yes |
| Read verdicts and KB content | Yes | No |
| Manage sources, settings, triage, LLM profiles/roles, recipients, mail, KB | Yes | No |
| Test source, LLM, or mail provider | Yes | No |
| Read audit log | Yes | No |

## Authentication and unresolved seams

O11 (Admin identity provider and account list) remains OPEN. No production identity provider
was selected or invented. Without an injected actor resolver, all API routes fail closed with
401. Header-based actor auth is only available when `TI_ADMIN_ENABLE_TEST_AUTH=true` and
`TI_ENV` is not production; the example keeps this disabled.

The application has no concrete LLM provider factory wired. The “test connection” endpoint
uses an injected fake/client factory and safely reports unavailable when absent; it does not
create a hidden network path. Mail tests use the existing NotificationService only when its
configuration is present. O3/O4/O9/O12 remain open and are not decided here.

## Original implementation test results (superseded by independent verification)

- `tests/integration/test_admin_api_prompt15.py`: 10 passed after final fixes.
- Focused legacy Admin API health test: 1 passed.
- Focused NotificationService tests, including test-mail success and guard rejection: 2
  passed; API failed-send audit test: 1 passed.
- Ruff checks for changed Python modules/tests: passed.
- Focused combined run at implementation time `test_admin_api_prompt15.py`, `test_admin_api.py`,
  `test_notification_service.py`: 38 passed.
- Full regression suite at implementation time collected 610 tests: 609 passed, 1 failed. The failure is the
  existing unrelated French WAHO date parser test `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`; Prompt 15 does not own that parser. The
  focused Admin API/notification regression suite passes after the Admin lifespan fix.

## Defects found and fixed

1. **`TI_ENV` settings alias** — local environment setting documented in `.env.example` was
   ignored because the model expected `TI_ENVIRONMENT`. Added the explicit alias and tested
   development opt-in versus production rejection.
2. **Failed test-send response/audit** — provider failure returned a success-shaped response
   and the failure audit was rolled back when converting to HTTP 502. The API now commits the
   safe failure audit and returns a correlation-linked 502 without provider exception text.
3. **Notification safety exception construction** — new test-send checks passed one argument
   to an exception that requires an error code and message. Added the existing routing error
   code and verified the intended safety exception.
4. **Response identity label** — provider test response now names the persisted row
   `notification_log_id`, not `notification_attempt_id`.

## Remaining limitations

- Production API remains unavailable until the project resolves O11 and injects the approved
  authentication integration.
- LLM test connection remains unavailable until the existing LLM abstraction is composed
  with a concrete configured factory.
- Provider live test requires valid operator-supplied encrypted credentials, provider
  configuration, and active test recipient. No live email was sent during verification.

## Final gate

Implementation is ready for independent behavioral verification. It is not a production
deployment approval while O11 and the concrete LLM test client integration remain unresolved.

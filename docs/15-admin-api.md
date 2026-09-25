# Admin API — Prompt 15 implementation

This document describes the API implementation in `src/tender_intelligence/admin/`.
It is a thin control and read layer over the shared worker database and existing domain
services. It does not run the worker pipeline or implement the Admin UI.

## Authentication and roles

All `/api/v1` routes require an `Actor` resolved by the injectable
`app.state.actor_resolver`. The repository has not selected an Admin identity provider
or account list (open decision O11), so the default resolver fails closed with HTTP 401.
The optional `X-Test-Actor` / `X-Test-Role` local test resolver is enabled only when
`TI_ADMIN_ENABLE_TEST_AUTH=true` and `TI_ENV` is not `production`/`prod`; it defaults off.
It is not a production authentication mechanism.

Viewer routes are read-only. Configuration writes, provider tests, audit reads, verdict
details, and all Knowledge Base metadata/content require Admin. Viewer serializers omit KB,
LLM credentials, recipient addresses, and sensitive tender notice metadata.

## Route inventory

All paths below are relative to `/api/v1`.

| Area | Routes |
| --- | --- |
| Sources | `GET /sources/supported-types`, `GET/POST /sources`, `GET/PUT /sources/{id}`, `PATCH /sources/{id}/active`, `POST /sources/{id}/test` |
| Tenders | `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/timeline`, Admin `GET /tenders/{id}/verdicts` |
| Operations | `GET /health/dashboard` |
| Settings and triage | `GET/PUT /settings`, `GET/PUT /triage` |
| LLM | `GET/POST /llm/profiles`, `GET/PUT/DELETE /llm/profiles/{id}`, `GET/PUT /llm/roles`, `POST /llm/profiles/{id}/test`, `GET /llm/usage` |
| Recipients | `GET/POST /recipients`, `PUT/DELETE /recipients/{id}` |
| Mail | `GET/POST /mail/providers`, `PUT /mail/providers/{id}`, `PATCH /mail/providers/{id}/active`, `POST /mail/providers/{id}/test`, `POST /mail/test` |
| Knowledge Base | Admin `GET /knowledge-base/versions`, Admin `GET /knowledge-base/versions/{id}`, Admin `POST /knowledge-base/versions`, Admin `GET /knowledge-base/diff` |
| Audit | Admin `GET /audit` |

List routes use bounded offset/limit pagination and return explicit DTOs. Request schemas
reject unknown fields. API exceptions and request-validation errors do not echo submitted
values or exception text.

## Shared configuration and service boundaries

```text
Admin API DTO and authorization
        ↓
shared SQLAlchemy models / existing repositories
        ↓
worker configuration reads on its next run
```

Source tests delegate to the shared `RunCoordinator.run_source(..., dry_run=True)` boundary.
The default app composes this existing worker pipeline during lifespan; tests may inject a
coordinator. The dry-run report contains stage/count/planned-action metadata and creates no
Tender, RunHistory, or SeenTender records. If composition is unavailable, the endpoint
returns a safe 503 rather than bypassing the pipeline. The current RunReport is summary-only;
it does not return each discovered candidate's title/URL. Timeline reads use
`TimelineService`.
Configuration edits use the existing `ConfigChangeLog` audit service. Recipient edits
use `RecipientGuard`. Mail delivery tests call `NotificationService.send_test_email`,
which enforces Test Mode and the active development-recipient list, uses the configured
provider chain, and records notification/provider-attempt rows without tender/verdict
association. The formatter/test route does not select business recipients.

The LLM test endpoint invokes an injected `llm_client_factory`; no concrete provider
factory is currently part of application composition. Until that upstream service is
provided, the endpoint responds with a safe unavailable error rather than inventing a
provider implementation. No KB is sent during the connection test. Custom LLM
`extra_headers` cannot currently be configured through this API because the existing model
stores them in plaintext and there is no encrypted header-secret field.

## Secrets and file uploads

LLM/mail/source authentication values are write-only. They are encrypted with the
existing master-key mechanism before storage. Responses expose only configured booleans;
audit records contain changed-field names/status, never secret values. If encryption is
not configured, secret writes fail closed. Do not put real credentials in `.env.example`.

KB upload accepts `.docx`, `.pdf`, `.md`, and `.txt` and extracts text into an immutable
KnowledgeBaseVersion. The decoded upload limit is the configurable technical setting
`TI_ADMIN_MAX_KB_UPLOAD_BYTES` (20 MiB proposed default). No database migration was
required for this API slice.

## Decisions and deployment limitations

- O11 (Admin identity provider and account list) remains open; production auth must be
  supplied through `actor_resolver` before deployment.
- O3/O4/O9/O12 business sender/provider/budget/data-policy choices remain governed by
  their existing specifications and are not decided by this API.
- The LLM test endpoint requires a concrete injected client factory, which is not wired
  by the current application composition.
- Mail provider testing requires existing provider credentials, encryption key, active
  provider configuration, Test Mode ON, and an active `dev_alert` recipient.
- Prompt 16 owns the UI. It should call this API and must not create a parallel
  configuration store.

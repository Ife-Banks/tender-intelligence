# Admin API — Prompt 15 implementation

This document describes the API implementation in `src/tender_intelligence/admin/`.
It is a thin control and read layer over the shared worker database and existing domain
services. It does not run the worker pipeline or implement the Admin UI.

## Application access

Per the current owner decision in `docs/13-open-decisions.md` O11, application-level login
and identity authentication are intentionally deferred for this internal-tool version. The
Admin UI opens at `/admin/` and `/api/v1` routes do not require an application session or
test-auth headers. No Admin/Viewer identity model is simulated. Keep the service within the
intended internal/private deployment; this scope decision does not authorize public exposure.

Operational safeguards remain server-side: secret values are write-only and omitted from
responses/audit values, schemas validate all writes, changes are audit-logged with the
non-human channel actor `internal-admin-api`, and Test Mode/mail-recipient/dry-run guards
remain enforced independently of identity.

## Route inventory

All paths below are relative to `/api/v1`.

| Area | Routes |
| --- | --- |
| Sources | `GET /sources/supported-types`, `GET/POST /sources`, `GET/PUT /sources/{id}`, `PATCH /sources/{id}/active`, `POST /sources/{id}/test` |
| Tenders | `GET /tenders`, `GET /tenders/{id}`, `GET /tenders/{id}/timeline`, `GET /tenders/{id}/verdicts` |
| Operations | `GET /health/dashboard` |
| Settings and triage | `GET/PUT /settings`, `GET/PUT /triage` |
| LLM | `GET/POST /llm/profiles`, `GET/PUT/DELETE /llm/profiles/{id}`, `GET/PUT /llm/roles`, `POST /llm/profiles/{id}/test`, `GET /llm/usage` |
| Recipients | `GET/POST /recipients`, `PUT/DELETE /recipients/{id}` |
| Mail | `GET/POST /mail/providers`, `PUT /mail/providers/{id}`, `PATCH /mail/providers/{id}/active`, `POST /mail/providers/{id}/test`, `POST /mail/test` |
| Knowledge Base | `GET /knowledge-base/versions`, `GET /knowledge-base/versions/{id}`, `POST /knowledge-base/versions`, `GET /knowledge-base/diff` |
| Audit | `GET /audit` |

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
stores them in plaintext and there is no encrypted header-secret field. The built-in
`azure_openai` protocol is the exception for Azure API-key authentication: it sends the
encrypted profile API key as the `api-key` header and does not persist that key in
`extra_headers`.

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

- O11 application login is deferred/out of current scope; no identity provider is configured.
- O3/O4/O9/O12 business sender/provider/budget/data-policy choices remain governed by
  their existing specifications and are not decided by this API.
- The LLM test endpoint requires a concrete injected client factory, which is not wired
  by the current application composition.
- Mail provider testing requires existing provider credentials, encryption key, active
  provider configuration, Test Mode ON, and an active `dev_alert` recipient.
- Prompt 16 owns the UI. It should call this API and must not create a parallel
  configuration store.

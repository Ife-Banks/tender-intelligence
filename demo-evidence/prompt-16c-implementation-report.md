# Prompt 16C — Implementation Report

## Executive summary

Removed the application-level identity gate and its local test-auth bypass from the Admin API
and UI. The Admin application now works at `/admin/` without a login session, query flag, role
selector, auth header, or fake actor. Identity/login is intentionally deferred for the current
internal-tool scope under O11. Existing safety controls remain server-side.

This report records implementation and automated test results. It is **not** independent
verification and does not mark Prompts 14, 15, 16A, or 16B as verified.

## Scope decision

- Application-level authentication/login: **DEFERRED / OUT OF CURRENT SCOPE**.
- O11 is non-blocking for current Admin functionality.
- No production identity provider is configured or implemented.
- The decision assumes internal/private operation; it does not approve public exposure. The
  existing Admin runner binds to `127.0.0.1:8000`.

## Before and after

Before, requests reached an actor resolver; absent a resolver/session they returned 401, and the
UI displayed an authentication-required panel with an optional `devAuth=1`/test-role flow.

After, `/admin/` loads and `/api/v1` operates directly without an application session. The
frontend sends neither simulated actor/role headers nor application credentials, and an
unexpected upstream 401 remains an ordinary safe API error rather than triggering fake login
or fixture fallback.

## Files changed

- `src/tender_intelligence/admin/auth.py` — removed the unused actor resolver/Admin gate.
- `src/tender_intelligence/admin/api.py` — removed actor dependencies and identity-based RBAC
  filtering; operational endpoints now use safe DTOs consistently. Audit/config attribution is
  the explicit non-human channel `internal-admin-api`.
- `src/tender_intelligence/admin/main.py` — removed the injectable actor resolver; kept existing
  CSP/security headers and application service wiring.
- `src/tender_intelligence/config/settings.py`, `.env.example` — removed the obsolete test-auth
  setting; no credential or secret value was added.
- `src/tender_intelligence/admin/static/api.mjs` — removed test identity/header support, role
  discovery, and auth-specific state; API requests omit browser credentials.
- `src/tender_intelligence/admin/static/app.mjs`, `index.html` — removed auth-required UI,
  fake role presentation, role-gated navigation/actions, and the `devAuth` query flow; all ten
  screens operate through the API.
- `src/tender_intelligence/admin/static/fixtures.mjs` — removed the fixture’s misleading Viewer
  identity example.
- `src/tender_intelligence/db/models/users.py` — marked `AdminUser` as a retained, unused
  historical schema artifact. No migration history or table was deleted.
- `tests/integration/test_admin_api_prompt15.py`,
  `tests/integration/test_admin_ui_api_prompt16b.py`, and `tests/ui/*.test.mjs` — replaced
  obsolete authentication/RBAC expectations with no-login behavior checks; preserved safety,
  secret, audit, and dry-run assertions.
- Current docs updated: `README.md`, `docs/01-product-requirements.md`,
  `docs/02-technical-architecture.md`, `docs/03-data-model.md`, `docs/09-admin-app-spec.md`,
  `docs/10-security-spec.md`, `docs/11-testing-strategy.md`, `docs/13-open-decisions.md`,
  `docs/14-acceptance-criteria.md`, `docs/15-admin-api.md`,
  `docs/16-opex-data-collection.md`, `docs/context-history.md`.
- Prompt guidance updated: `prompts/00-master-context.md`, `prompts/15-admin-api.md`,
  `prompts/16-admin-ui.md`, `prompts/17-security-hardening.md`, `prompts/19-code-review.md`.
- `implementation/03-next-task.md` now records the current state and verification debt.
- `demo-evidence/prompt-16c-implementation-result.json` and
  `demo-evidence/prompt-16c-test-results.txt` contain machine-readable status and test evidence.

At task start, `implementation/00-current-state.md`, `implementation/01-decisions.md`, and
`implementation/02-known-issues.md` did not exist; no duplicate files were created. The
pre-existing `implementation/03-next-task.md` was stale at Prompt 11 and has been updated.

## Authentication code removed/deferred

- Removed `Actor`, `resolve_actor`, `require_admin`, `CurrentActor`, and `AdminActor` route gates.
- Removed `actor_resolver` from `create_app()` and `app.state`.
- Removed `TI_ADMIN_ENABLE_TEST_AUTH`, `configureTestAuth`, `X-Test-Actor`, `X-Test-Role`,
  `devAuth=1`, and `devRole`.
- Removed Admin/Viewer identity simulation and UI role discovery/gates. Pipeline LLM roles and
  recipient-role fields remain domain configuration, not user identities.
- Retained the `AdminUser` ORM/migration artifacts as historical schema; current routes do not
  read or create users. No migration was added or deleted.
- Kept genuine generic 403 handling for an upstream/deployment policy denial. Normal Admin API
  routes no longer produce 401 because an application actor is missing.

## Safety controls preserved

- **Test Mode:** existing persisted setting remains default-ON and controls notification routing.
- **Test email:** `/mail/test` and provider test paths still require Test Mode ON and an active
  `dev_alert` recipient; subjects/text retain the `[TEST]` marker and calls are audited.
- **Secrets:** provider credentials remain encrypted server-side, write-only on input, represented
  by configured flags in response DTOs, and excluded from audit values.
- **Validation:** strict Pydantic request models, email/URL/config validation, safe 422 errors,
  and last-active-development-recipient guard remain.
- **Audit:** configuration writes remain persisted in `ConfigChangeLog`. Until human identity is
  in scope, audit actor attribution is `internal-admin-api` and is not presented as a person.
- **Dry-run:** source test remains dry-run and asserts no persistence or email side effect.
- **Worker independence:** no worker, pipeline, crawler, provider factory, or mail service behavior
  was changed; existing worker integration tests passed in the full suite.
- **Network/deployment:** no bind address, CORS, CSP, or deployment exposure was widened.
- **Git secrets:** `.env` and `.env.*` remain ignored while `.env.example` remains allowed; the
  local `.env` is ignored by Git.

## Documentation and data-model treatment

O11 now reads `DEFERRED / OUT OF CURRENT SCOPE`, `Blocking: NO`. Current architecture, security,
acceptance, API, README, roadmap, and Prompt 17 instructions no longer require a production
IdP or Viewer-role enforcement. The original `source-docs/tender-intelligence-spec-v1.1.md`
remains unchanged as historical source provenance; the newer owner decision in O11 supersedes
its proposed authentication requirement for the current release.

## Tests run

Focused Admin integration tests: 19 passed. UI tests: 26 passed. Python full suite collected 619
tests and completed with 618 passed and the one known WAHO deadline-parser failure listed below.
Node syntax checks and Ruff checks passed. Exact commands and outcomes are in
`demo-evidence/prompt-16c-test-results.txt`.

## Known failure

`tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`
still fails because the existing parser returns no deadline for the French July/GMT fixture.
This was a known pre-existing issue, unrelated to the Admin access change, and was not modified.

## Verification debt

Known independent-verification gaps for Prompts 14, 15, 16A, and 16B remain. This implementation
does not claim those prompts are verified or that the application is production-ready. Prompt
16C itself is ready for independent verification.

## Prompt 17 boundary

Prompt 17 has **NOT** started. No broad security hardening, dependency updates, rate limiting,
deployment redesign, authentication provider, or unrelated pipeline change was implemented.

## Implementation status

**IMPLEMENTED** — ready for independent verification; not independently verified.

PROMPT 16C: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION

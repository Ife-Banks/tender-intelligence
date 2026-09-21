# Prompt 11 — Security Hardening

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/10-security-spec.md` (full — the invariant list §10.3 is your checklist)
- `docs/09-admin-app-spec.md` (auth/roles)
- `docs/02-technical-architecture.md`
- The code implemented by earlier prompts

## Task

Perform a **security review and hardening pass** across the whole repository. Work through every invariant in `docs/10` §10.3 and verify/fix:

1. **Secrets:** encrypted at rest (master key outside the DB); write-only in UI/API; never in logs; never in `ConfigChangeLog`; never in commit history or fixtures.
2. **No secret leakage paths:** grep the codebase for key/signature/token/password patterns in logs, exceptions, responses, tests, and docs. Fix every finding.
3. **Data policy enforcement:** `approved_for_company_docs=false` profiles must be refused KB content — including fallback paths, and regardless of Test Mode. Verify with tests that would fail without the gate.
4. **KB access control:** Viewer role cannot reach KB or secrets via API or UI; verify both server and client enforcement.
5. **Admin actions:** mutating endpoints audit-log (actor, entity, fields) and never record secret values; Test-Mode-off is explicit + audit-logged.
6. **Dependencies/vulnerabilities:** run dependency/security scan tooling available in the repo (e.g. `pip-audit`/`npm audit`, or CI-compatible equivalent) and remediate or document findings.
7. **Secure links:** signed URLs expire (default 14 days `[PROPOSED]`), storage paths never exposed, direct-URL guesses fail.
8. **Input validation:** all API inputs validated (enums, size limits, formats — emails validated on entry).
9. **Output safety in UI:** XSS/HTML-injection protections for tender/metadata content rendered in the admin UI (content is third-party-derived).
10. **Supply-chain posture:** document any third party that sees data (LLM/mail/OCR/Sendlib) with an owner per `docs/10` §10.1, ready for the go-live security note.

## Out of scope

- New business features or refactors beyond the security fix surface.
- Resolving open decisions (O11 login method etc.) — note needs, don't decide.

## Tests

- For every invariant you verify, add or keep a test that fails if the invariant is broken (secret-leak grep test, data-policy-refusal tests incl. fallback, no-secret-in-audit test, signed-link expiry test, viewer-blocked tests).
- Negative tests: attempt each bypass and assert it fails.

## Rules

- Do not weaken security to make a test pass.
- Do not remove existing tests.
- Keep fixes minimal and mapped to a documented requirement.

## Report

Findings (verified-fixed / risk-accepted / needs-decision), files changed, tests added/run + results, and a short draft "third-party data handlers" security-note list per `docs/10` §10.1.
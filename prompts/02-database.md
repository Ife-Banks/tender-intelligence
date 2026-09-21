# Prompt 02 — Database

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/02-technical-architecture.md` (§2.4 shared DB, §2.7 config flow)
- `docs/03-data-model.md` (full)
- `docs/04-pipeline-spec.md`
- `docs/13-open-decisions.md` (so you know what NOT to decide)

## Task

Implement the **database schema, migrations, models, indexes and constraints** for the Tender Intelligence system, exactly per `docs/03-data-model.md`.

Implement entities: `Source`, `Tender`, `Document`, `KnowledgeBaseVersion`, `Verdict`, `LLMProfile`, `LLMRoleAssignment`, `LLMCall`, `MailProvider`, `NotificationLog`, `NotificationAttempt`, `Recipient`, `AlertEvent`, `RunHistory`, `Settings`, `ConfigChangeLog`, `AdminUser`. Do NOT create `KnowledgeBaseDoc` (Phase 2 only).

Requirements:

1. Use **migrations** (never destructive changes). Document the migration workflow in a README section for the repo if one does not exist.
2. Encode confirmed constraints from `docs/03`:
   - `Recommendation` enum: `APPLY`, `DO NOT APPLY`, `APPLY WITH CONDITIONS`.
   - `Tender.status`: `new/updated/processed/verdict_failed/awaiting_budget`.
   - `Recipient.list_type`: `tender | dev_alert`; `delivery`: `to/cc/bcc`.
   - Unique `(source_id, external_id)` on Tender.
   - `approved_for_company_docs` default false on `LLMProfile`.
   - Settings: `test_mode` default **true**.
   - `NotificationLog` stores recipients, attachments[] and links[] snapshots, `dedupe_key`, `possible_duplicate`.
3. Field types/sizes: reasonable `[PROPOSED]` choices consistent with the doc; mark any deviation in the final report.
4. Indexes: the `[PROPOSED]` indexes in `docs/03` (per source+external_id, status, deadline, correlation_id, checksum, generated_at, timestamps, breaker state).
5. No secrets in plaintext columns: secret-bearing columns are named `*_encrypted` and the model layer must never log or return them (see `docs/10-security-spec.md`).
6. Provide models/ORM classes if the chosen stack uses one; keep them thin.
7. Do NOT seed real recipients, providers, or profile values (they are open decisions). The ONLY allowed seed is a dev-alert recipient from an environment variable (per `docs/03` §Recipient and `docs/04` §4.9).

## Out of scope

- Pipeline implementation, adapters, AI, email.
- Settling open decisions (recipients, providers, budget, storage).
- `KnowledgeBaseDoc` (Phase 2).

## Tests

- Migration applies cleanly from empty database in tests.
- Enum/constraint violations raise errors.
- Unique tender per (source, external_id) enforced.
- Settings default test_mode = true.
- Secret columns are excluded from any default serialisation (no accidental exposure test).
- Recipient "last active dev recipient cannot be deleted/deactivated" logic is implemented in the data layer **or** clearly deferred to a service layer with a test asserting the guard is in place.

## Rules

- Migrations only; no drop/recreate in normal flow.
- No invented business rules beyond the confirmed constraints.
- If a column/relationship sense is ambiguous, check `docs/03` again; if still ambiguous, record it in the report as a proposed-default with rationale.

## Report

Files changed; entities implemented; deviations from `docs/03` (with reasons); tests run + results; open decisions you deliberately did not settle.
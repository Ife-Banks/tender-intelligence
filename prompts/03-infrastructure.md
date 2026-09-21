# Prompt 03 — Infrastructure

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md` (#11, #14, #15, #16)
- `docs/02-technical-architecture.md` (config flow, logging, storage, correlation IDs, Test Mode)
- `docs/04-pipeline-spec.md` (retry, correlation IDs, config reload, alerting)
- `docs/10-security-spec.md` (secret storage)
- `docs/12-deployment.md`
- `docs/13-open-decisions.md`

## Task

Implement the **configuration, logging, secrets handling, scheduling, storage abstraction and general infrastructure foundations** for the worker. Phase 0 of `docs/12-deployment.md`.

1. **Configuration:** a version-controlled bootstrap config (YAML/JSON) loaded fresh at the start of every run (never cached across runs). Provide the seam for the later admin-app-backed configuration; document how admin changes will flow in later (shared DB).
2. **Environment secrets:** config pulls secrets from environment variables / a secrets interface for the very first run. The encryption-master-key must live OUTSIDE the database (`docs/10` §10.1). Provide encryption helpers for secrets-at-rest marked clearly as the mechanism for `*_encrypted` columns.
3. **Structured logging:** JSON (or equivalent) records with timestamp, correlation ID, source, stage, status, and a machine-readable error code. Include an error-code taxonomy module seeded with the documented codes: `source_unreachable`, `parser_mismatch`, `document_download_failed`, `ocr_failed`, `ai_call_timeout`, `ai_invalid_output`, `budget_exceeded`, `email_send_failed`, `provider_failover`. Add a sanitisation guard so secrets and KB/full-prompt content can never be logged — prove it with a test.
4. **Correlation IDs:** generator + propagation utilities (context-aware) so every pipeline stage stamps rows and log lines.
5. **Scheduling:** a minimal scheduler wrapper honouring per-source `crawl_frequency`, with crash-safe semantics (a crashed run must not lose seen-records or block the next run) and polite-crawling parameters (rate limit, UA, backoff) as config.
6. **Storage abstraction:** a small interface for the document archive (store object, read bytes, produce a handler for signed expiring links later). Physical backend may be local/filesystem as a placeholder — the interface is the deliverable. `docs/12` notes the real location is an open decision.
7. **Test Mode:** a global switch defaulting ON; expose helpers reflecting `docs/04`/`docs/08` rules (routing to dev list, `[TEST]` prefix, dry-runs write nothing).
8. **Dev-alert bootstrap:** seed the first dev alert recipient from an environment variable, per `docs/04` §4.9.

## Out of scope

- Any source adapter, AI, email adapters, or pipeline business logic.
- The admin web app.
- Choosing real cloud vendors.

## Tests

- Config reload produces a fresh object per run (no cross-run cache).
- Log records are JSON and carry correlation ID/stage/status; secret values never appear even when a log call is made with a secret-like value.
- Encryption round-trip works; master key is not derivable from the DB when master key is provided externally.
- Scheduler registers per-source frequencies; a simulated crash does not block the next run.
- Test Mode defaults ON and routes correctly via its helper.
- Error-code taxonomy is stable and used by log helper.

## Rules

- Keep the worker/run boundaries strict (#17 in PROJECT_RULES: no unrelated refactors).
- Nothing user-facing yet; this is foundation only.
- Never commit real secrets or a real master key.

## Report

Files changed; mapping to docs; tests run + results; deviations (marked `[PROPOSED]`); TODOs; any open decision you needed but did not settle.
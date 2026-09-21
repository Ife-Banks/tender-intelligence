# 15 — AI Coding Rules

> **Canonical source:** `PROJECT_RULES.md` at the repository root is the authoritative, non-negotiable contract. This document is the docs-series home of those rules and mirrors them. Keep the numbering identical in both files so cross-references such as "(PROJECT_RULES #5)" stay valid.

## Rules

1. **Read the relevant docs before coding.** Never write code for a feature without first reading the docs that specify it and the prompt that requests it.

2. **`docs/` is the implementation specification.** The files in `docs/` are derived from the source specification and are the working contract. `/prompts` define the current session's task only.

3. **v1.1 is authoritative over v1.0.** `source-docs/tender-intelligence-spec-v1.1.md` wins over `tender-intelligence-spec-v1.0.md`. v1.0 is historical context only. Never let v1.0 override v1.1.

4. **Never invent business requirements.** If it isn't in the source docs or `docs/`, it isn't a requirement. Open business decisions live in `docs/13-open-decisions.md`.

5. **Never silently resolve open decisions.** Open decisions are business choices. Use a clearly-marked configuration default where unavoidable, and flag the decision to the human. Do not hard-code a resolution as if it were a requirement.

6. **Distinguish confirmed requirements from proposed implementation choices.** Where ambiguity exists, label the statement: confirmed vs `[PROPOSED]` vs `[DEFERRED]`.

7. **Implement incrementally.** One feature/slice at a time, in the documented order. No speculative scaffolding.

8. **Do not build RAG/vector infrastructure** unless an explicit approval exists. RAG is phase 2/5, not v1.

9. **Preserve provider abstractions.** LLM providers and mail providers must be swappable via configuration. Keep the `LLMClient` and `MailProvider` seams intact.

10. **Preserve source-adapter isolation.** Each source lives behind the `SourceAdapter` interface; adding a site of a supported type is config-only, new types need one new adapter. No source-specific logic leaks into the pipeline, DB, email, or admin code.

11. **Preserve Test Mode.** Global switch, ON by default; when ON all tender emails go only to the dev alert list with `[TEST]` subject prefix; turning off is explicit and audit-logged.

12. **Never expose secrets.** No secrets in logs, API responses, `ConfigChangeLog`, fixtures, tests, docs, or commit history. Write-only. Encrypted at rest with the master key outside the database.

13. **Add tests with meaningful implementation changes.** Every new capability or fix ships with tests. Never delete or weaken tests to make something pass.

14. **Use database migrations, never destructive schema changes.**

15. **Maintain structured logs and correlation IDs.** JSON (or equivalent) records with timestamp, correlation ID, source, stage, status, and a machine-readable error code.

16. **Maintain auditability.** Every tender/document/verdict/email logged; stages record status not just results; retention minimum respected; recipients snapshotted at send time.

17. **Do not perform unrelated refactors.** Keep changes minimal and scoped to the task.

18. **Before implementing a feature, identify which document and requirement justify it.** State it in the final report (e.g. "implements `docs/04` §4.9 — dev-alert seeding").

19. **If requirements conflict, stop and document the conflict instead of guessing.** Add a note to `docs/13-open-decisions.md` (or the current task's report) and ask.

20. **Keep the worker functional even when the admin application is unavailable.** The admin app is optional for operation; the worker re-reads configuration at the start of every run and never caches settings across runs.

21. **AI-generated code must not weaken security, remove tests, bypass validation, or silently change business behaviour** merely to make tests pass. A failing requirement is a finding to report, not something to special-case away.
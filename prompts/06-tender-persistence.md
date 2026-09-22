# Prompt 06 — Tender Persistence

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/03-tender-data-model.md` (full — entities, enums, relationships, migration policy)
- `docs/04-pipeline-spec.md` (§4.2, §4.8)
- `docs/02-technical-architecture.md` (§2.9, migrations, connection management)
- `prompts/02-database.md` (migration baseline)
- `prompts/03-infrastructure.md` (env/config)

## Task

Implement the **storage layer** that dedup (`prompts/05`), document stages
(`prompts/07/08/09`) and the email layer (`prompts/11`) all read and write through — without
baking WAHO or pipeline specifics into the models.

1. Persist tenders via the `Tender` model: create on new, update fields on `update` events,
   preserving the first-seen correlation ID and unique constraint (`source_id`, `external_id`).
   Never store secrets or raw HTML bodies the model does not declare (`docs/15` #8, #10).
2. Provide repository seams:
   - seen-tender read/write used by dedup (upsert, list-by-source, existence check),
   - tender status transitions idempotently (new → `preparing_*` → … → evaluated — follow the
     enum exactly in `docs/03`),
   - document rows referenced by later stages (per-document statuses), created here but
     populated by the download/processing prompts.
3. Make every write **auditable**: `RunHistory` rows (start/end, counts, failed correlation
   IDs) and correlation-ID stamping on every row (`docs/04` §4.8). Nothing about the crawl
   state may live only in memory.
4. **Repository pattern, not ORM-in-controllers:** the pipeline and admin app must depend on
   repository interfaces, not raw models — the shape exchanges `docs/05` DTOs.
5. Ensure migrations for any new fields/tables are additive and follow `docs/02` migration
   policy; the baseline from `prompts/02` must not be rewritten.

## Out of scope

- Dedup decision logic — `prompts/05-tender-deduplication.md`.
- Attachments/links enumeration — `prompts/07-document-discovery.md`.
- File download/checksums — `prompts/08-document-download.md`.
- Any AI, verdict, or email logic.

## Tests

- Upsert is idempotent; unique `(source_id, external_id)` enforced by both code and constraint.
- Status transition follow the exact enum from `docs/03`; illegal transitions rejected.
- Correlation ID required on write; rows are queryable by correlation ID (timeline readiness).
- RunHistory lifecycle (start → completed/errored) works under a simulated failure.
- Migration test: fresh DB migrates to head; no data-loss path in `update` flow.

## Rules

- Keep models spec-shaped (`docs/03`); never invent fields needed by a single pipeline stage.
- Business rules and defaults come from `docs/03`/spec v1.1 — never invented; conflicts → stop
  and document (`PROJECT_RULES` #19).

## Report

Files changed; entity/column mapping from `docs/03`; repository interfaces; migration list;
concurrency notes; tests + results; open items.
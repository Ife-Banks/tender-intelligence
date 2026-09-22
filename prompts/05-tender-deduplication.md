# Prompt 05 — Tender Deduplication

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/04-pipeline-spec.md` (§4.1 stage 3, §4.2, §4.5, §4.7 idempotency, §4.8 run history, §4.14 update events)
- `docs/05-source-adapter-spec.md` (dedup identifiers: `external_id`, `source_id`)
- `docs/03-tender-data-model.md` (`Tender` unique constraint, `status`, `RunHistory`)
- `docs/02-technical-architecture.md` (§2.9)

## Task

Implement **new-listing detection / deduplication**: decide, from the listings produced by
`prompts/04-waho-discovery.md`, which tenders are **new** vs **already seen (potential
updates)** — and do it in a way that survives crashes and is safe to re-run.

1. Use the persisted **seen-tender** records (keyed `source_id` + `external_id`) as the single
   source of truth. A listing is:
   - **new** when no seen record exists,
   - **update** when a seen record exists and the listing differs (title, deadline, addendum,
     or any addendum/change marker — `docs/04` §4.14),
   - **unchanged** otherwise (skip silently).
2. Implement the comparison so **updates** are distinct, lower-priority events, tagged for the
   email layer (`docs/04` §4.1, §4.14). A changed deadline or a new addendum must be detectable,
   and the verdict re-run only when the change is material.
3. **Correlation IDs:** every tender and every run gets a unique correlation ID at first
   detection; every log line, DB row and error it emits is stamped with it (`docs/04` §4.8).
4. **Crash safety:** the dedup pass must record what it saw **before** any downstream stage can
   fail, so a crashed run never reprocesses or re-notifies the same tender (`docs/04` §4.3).
   Standardise the point at which a tender becomes "seen".
5. **RunHistory:** create a run-history row at run start (listings found, new count) and mark it
   completed/errored at run end (`docs/04` §4.8). Concurrency-safe for the next scheduled run.
6. Expose the seam used by `prompts/10-pipeline-orchestration.md`: a clean input (discovery
   listings) → output (new set, update set, counts, correlation map).

## Out of scope

- Crawling / listings — `prompts/04-waho-discovery.md`.
- Persisting tender/document rows — `prompts/06-tender-persistence.md`.
- Document discovery/download/processing — `prompts/07/08/09-*.md`.
- Email / notification idempotency keys — `prompts/11-test-mode-email.md`.
- Repository/storage implementation details owned by `prompts/06`.

## Tests

- Fixture: same listing on two runs → second run reports zero new tenders.
- Fixture: deadline/addendum change → classified `update`, not new; material-change flag set.
- Crash test: kill the process mid-run; next run does not duplicate `seen` entries.
- Correlation ID present on every record emitted.
- Concurrency: two scheduled runs do not double-create seen records.

## Rules

- No silent failures: every surprising outcome is logged with its correlation ID
  (`docs/04` §4.3).
- Idempotent by construction; re-running a crawl never re-notifies (`docs/04` §4.7).

## Report

Files changed; dedup decision matrix; crash-safety point; RunHistory lifecycle; tests + results;
TODOs/risks.
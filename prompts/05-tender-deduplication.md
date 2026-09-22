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

# Prompt 05 — Tender Deduplication

> Paste `prompts/00-master-context.md` first.

## Read

* `PROJECT_RULES.md`
* `prompts/04-waho-discovery.md` and the **actual Prompt 04 implementation/output contract**
* `docs/04-pipeline-spec.md`

  * §4.1 stage 3
  * §4.2
  * §4.3
  * §4.5
  * §4.7 idempotency
  * §4.8 run history
  * §4.14 update events
* `docs/05-source-adapter-spec.md`

  * dedup identifiers: `source_id` + `external_id`
* `docs/03-tender-data-model.md`

  * `Tender` uniqueness
  * `status`
  * `RunHistory`
* `docs/02-technical-architecture.md` (§2.9)
* `docs/11-testing-strategy.md`
* `docs/14-acceptance-criteria.md`

Before implementing, inspect the actual normalized listing/candidate object returned by Prompt 04.

**Do not change the WAHO discovery/parser implementation unless you discover that Prompt 04 violates its documented output contract.**

---

# Task

Implement the **new-listing detection / deduplication stage**.

The stage receives normalized listing candidates from the source adapter/discovery stage and determines whether each candidate is:

* `NEW`
* `UPDATE`
* `UNCHANGED`

The deduplication identity is:

```text
source_id + external_id
```

This identity is the business key for determining whether a listing has been seen before.

The implementation must be **idempotent, crash-safe, and concurrency-safe**.

---

# 1. Deduplication Rules

Use the persisted seen-tender state as the source of truth.

For each discovery candidate:

### NEW

If no persisted seen record exists for:

```text
source_id + external_id
```

classify the candidate as:

```text
NEW
```

### UPDATE

If a seen record exists and the current candidate contains a meaningful change, classify it as:

```text
UPDATE
```

An update must include structured change information.

At minimum support detection of:

* title change
* deadline change
* addendum/change marker change
* newly detected addendum
* other explicitly configured material change

Do not treat incidental parser/formatting differences as material changes unless the specification requires them.

### UNCHANGED

If a seen record exists and no meaningful change is detected:

```text
UNCHANGED
```

Unchanged listings must not be sent downstream for reprocessing.

They may be counted in RunHistory, but they should not generate an email or AI reprocessing event.

---

# 2. Deterministic Change Detection

Implement deterministic comparison between the current listing and the persisted representation.

Do not rely on arbitrary object equality if fields contain:

* different date formatting
* insignificant whitespace
* ordering differences
* transient HTML/parser metadata

Normalize values consistently before comparison where appropriate.

Record the detected change types, for example:

```text
TITLE_CHANGED
DEADLINE_CHANGED
ADDENDUM_ADDED
CHANGE_MARKER_CHANGED
OTHER_MATERIAL_CHANGE
```

The result should expose:

```text
material_change: true | false
change_types: [...]
```

A deadline change must be independently detectable.

A newly detected addendum must be independently detectable.

Do not invent a materiality rule that contradicts the specification.

If the existing specification does not define whether a particular field is material, document the ambiguity as a TODO/risk rather than silently inventing business policy.

---

# 3. Seen-State Semantics

Define and implement one explicit point at which a listing becomes **seen**.

The critical invariant is:

> Once a candidate has been accepted by the deduplication stage, a downstream crash must not cause the same listing to be classified as NEW again on the next run.

The deduplication state must therefore be recorded **before downstream stages are allowed to process the result**.

The intended lifecycle is:

```text
discovery candidates
        ↓
deduplication transaction
        ↓
classify NEW / UPDATE / UNCHANGED
        ↓
atomically persist seen-state changes
        ↓
commit
        ↓
return downstream work
```

If the transaction fails or rolls back, the candidate must not be falsely considered persisted as seen.

Do not acknowledge a successful deduplication decision before its required database state is durable.

### Important ownership boundary

Prompt 05 owns the **deduplication decision and transaction boundary**.

Prompt 06 owns the detailed Tender/document persistence implementation.

Therefore:

* use existing repository/service interfaces where available;
* introduce a narrow repository/interface seam if required;
* do not redesign the Tender data model;
* do not move WAHO-specific parsing into the persistence layer;
* do not implement document persistence here;
* do not duplicate persistence logic that belongs to Prompt 06.

If the current data model does not provide enough persisted state to perform the required deduplication safely, stop and report the gap rather than inventing a conflicting schema.

---

# 4. Update Events

Updates are distinct from new tenders.

The deduplication result must allow downstream orchestration/email logic to distinguish:

```text
NEW
UPDATE
UNCHANGED
```

Updates must carry:

```text
material_change
change_types
```

and enough information for downstream stages to know what changed.

Updates are lower-priority events than NEW tenders as required by the pipeline specification.

Do not implement email sending in this prompt.

Do not implement AI verdict generation in this prompt.

The deduplication stage should only determine whether downstream processing is required.

---

# 5. AI Re-run Boundary

Expose whether an update is material enough to require downstream reprocessing.

At minimum:

```text
material_change = true | false
```

Do not implement the AI verdict engine here.

Do not duplicate AI materiality logic from the AI prompt.

If the existing specification explicitly defines which update types require verdict re-evaluation, expose that information as part of the dedup result.

Otherwise, document any unresolved materiality rule as a TODO rather than inventing business policy.

---

# 6. Correlation IDs

Use the project's existing correlation-ID mechanism.

Do not create a second incompatible correlation-ID system.

Every deduplication operation must be traceable to its run.

The output must provide a correlation mapping sufficient to trace:

```text
run
  ↓
source listing
  ↓
dedup decision
  ↓
persisted seen-state change
```

Use the existing project terminology for:

* run ID / correlation ID
* tender identity
* source identity

Do not confuse:

```text
source_id + external_id
```

with a correlation ID.

The business identity of a tender remains:

```text
source_id + external_id
```

Correlation IDs are for tracing execution/events.

Every log/error emitted by this stage must contain the appropriate correlation identifier according to the existing logging architecture.

---

# 7. RunHistory

Create a RunHistory record when the deduplication run starts.

Initial state should represent an in-progress run, for example:

```text
RUNNING
```

Do not attempt to populate final counts before they are known.

Track/update at least:

```text
listings_found
new_count
update_count
unchanged_count
```

At successful completion:

```text
COMPLETED
```

At an unrecoverable failure:

```text
ERRORED
```

The RunHistory lifecycle must remain reconstructable after a crash.

Do not create duplicate RunHistory rows merely because a scheduled run is retried.

Use the existing RunHistory model/semantics from the specification rather than inventing a parallel run-tracking system.

---

# 8. Concurrency Safety

The deduplication operation must be safe if two scheduled runs process the same listing concurrently.

The implementation must prevent:

```text
Run A: sees no record
Run B: sees no record
Run A: inserts NEW
Run B: inserts NEW
```

Use the database's existing uniqueness/transaction/concurrency mechanisms.

The invariant is:

```text
(source_id, external_id)
```

must not produce duplicate persisted seen records.

Do not rely solely on an application-level:

```text
SELECT → if missing → INSERT
```

check without appropriate transactional/unique protection.

Add a concurrency test proving that two simultaneous runs cannot create duplicate seen state.

---

# 9. Idempotency

Re-running the same discovery results must be safe.

Example:

### Run 1

```text
Tender A → NEW
```

### Run 2 with identical listing

```text
Tender A → UNCHANGED
```

It must not become NEW again.

It must not create a duplicate seen record.

It must not cause downstream re-notification.

Example:

### Run 1

```text
Tender A → NEW
```

### Run 2 with changed deadline

```text
Tender A → UPDATE
material_change = true
change_types = [DEADLINE_CHANGED]
```

It must not become a second NEW tender.

---

# 10. Crash Safety

Add a test that simulates a process failure during the deduplication operation.

The test must verify that after the process is restarted:

* persisted seen state is internally consistent;
* no duplicate seen record exists;
* the same candidate is not incorrectly classified as NEW;
* RunHistory reflects the interrupted/errored run according to the existing specification;
* a subsequent successful run can continue safely.

Do not weaken the test by simply mocking away the transaction.

Where practical, test the actual persistence/transaction boundary used by the application.

---

# 11. Clean Pipeline Seam

Expose a clean service boundary equivalent to:

```text
DiscoveryListings
        ↓
DeduplicationService
        ↓
DeduplicationResult
```

The result should provide, at minimum:

```text
new_listings
updates
unchanged_count
new_count
update_count
correlation_map
```

Use the project's actual domain names if they already exist.

Do not create WAHO-specific output types.

The result must be consumable by:

```text
prompts/10-pipeline-orchestration.md
```

without requiring the orchestration layer to understand WAHO parsing details.

---

# 12. Duplicate Candidates Within One Discovery Run

Handle the case where the discovery stage unexpectedly returns the same:

```text
source_id + external_id
```

more than once in a single run.

Do not emit duplicate NEW events for the same business identity.

The behavior must be deterministic and logged.

If this condition indicates a discovery/parser defect, record it as a structured warning/error according to the existing logging policy.

Do not silently create duplicate work.

---

# 13. Error Handling

No silent failures.

Unexpected outcomes must be logged with the appropriate correlation ID.

At minimum cover:

* database unavailable
* transaction failure
* unique-constraint race
* malformed candidate
* missing required deduplication identity
* invalid persisted state
* unexpected comparison failure

Do not swallow database or transaction errors merely to make the crawl appear successful.

Differentiate expected classification:

```text
UNCHANGED
```

from actual failure.

An unchanged tender is not an error.

---

# Out of Scope

Do **not** implement:

* WAHO crawling/listing extraction
* WAHO HTML parsing
* source discovery
* detail-page discovery
* attachment discovery
* document downloading
* document processing/OCR
* AI triage
* AI verdict generation
* email sending
* notification delivery
* email idempotency keys
* admin UI
* RAG/vector search
* other source adapters
* deployment work unrelated to this stage

Prompt 05 must not modify Prompt 04's WAHO parser merely to make deduplication work.

Prompt 06 owns detailed Tender/document persistence implementation.

Prompt 10 owns broader pipeline orchestration.

Prompt 11 owns test-mode email/notification behavior.

---

# Tests

Add tests alongside the implementation.

## 1. First-seen listing

Input:

```text
Tender A
```

with no existing seen state.

Expected:

```text
NEW
```

Exactly one persisted seen identity.

---

## 2. Identical second run

Run the same candidate again.

Expected:

```text
UNCHANGED
```

Expected counts:

```text
new_count = 0
update_count = 0
unchanged_count = 1
```

No duplicate persisted record.

---

## 3. Deadline change

First run:

```text
deadline = X
```

Second run:

```text
deadline = Y
```

Expected:

```text
UPDATE
material_change = true
change_types includes DEADLINE_CHANGED
```

Not NEW.

---

## 4. Addendum/change marker

First run has no addendum/change marker.

Second run contains a new addendum/change marker.

Expected:

```text
UPDATE
material_change = true
```

with the appropriate structured change type.

---

## 5. Title change

Verify title changes are detected according to the specification.

---

## 6. Different source identity

The same `external_id` from two different sources must not collide.

Example:

```text
source_A + 123
source_B + 123
```

must represent two different tender identities.

---

## 7. Duplicate candidate in one run

Provide the same:

```text
source_id + external_id
```

twice in one discovery result.

Verify no duplicate NEW event/persisted identity is produced.

---

## 8. Crash safety

Simulate process/transaction failure during deduplication.

Verify the next run does not create duplicate seen state or incorrectly reclassify already committed work as NEW.

---

## 9. Concurrency

Run two deduplication operations concurrently against the same candidate.

Verify:

* no duplicate persisted seen record;
* no duplicate NEW event;
* uniqueness constraint remains intact;
* results are deterministic according to the transaction outcome.

---

## 10. RunHistory

Verify:

```text
run starts → RUNNING
dedup executes → counts updated
success → COMPLETED
unrecoverable failure → ERRORED
```

Verify the final counts are correct.

---

## 11. Correlation IDs

Verify the correlation information is present on:

* RunHistory
* deduplication logs
* emitted dedup results/events
* errors
* persisted records where the existing schema requires it

---

## 12. Full regression

Run:

* Prompt 05 unit tests
* persistence/integration tests
* Prompt 04 tests
* full test suite
* lint
* type checking

Do not require real WAHO network access, real email, or real LLM calls for the normal automated test suite.

---

# Rules

* Use the actual Prompt 04 normalized output contract.
* Do not rewrite WAHO discovery.
* Keep WAHO-specific concepts out of generic deduplication/domain code.
* Deduplication must be idempotent by construction.
* Use database uniqueness and transactions for concurrency/crash safety.
* Do not use application-level existence checks as the only concurrency protection.
* Do not mark work successful before required state is durable.
* Do not send notifications from this stage.
* Do not run AI from this stage.
* Do not invent business rules where the specification is silent.
* No silent failures.
* Every surprising outcome must be logged with the appropriate correlation ID.

---

# Report

At the end, provide:

## 1. Files changed

List every file changed and why.

## 2. Deduplication decision matrix

Show:

| Existing seen state | Current listing            | Result    | Material change |
| ------------------- | -------------------------- | --------- | --------------- |
| none                | any valid listing          | NEW       | N/A             |
| exists              | identical                  | UNCHANGED | false           |
| exists              | title changed              | UPDATE    | true            |
| exists              | deadline changed           | UPDATE    | true            |
| exists              | new addendum/change marker | UPDATE    | true            |
| exists              | non-material difference    | UNCHANGED | false           |

Adjust the matrix if the specification requires different semantics and explain why.

## 3. Seen-state transaction point

Explain exactly:

* when a candidate becomes seen;
* what transaction protects it;
* what happens if the process crashes before commit;
* what happens if it crashes after commit but before downstream processing.

## 4. Concurrency strategy

Explain how two simultaneous runs are prevented from creating duplicate seen records.

## 5. RunHistory lifecycle

Explain:

```text
RUNNING → COMPLETED
RUNNING → ERRORED
```

and how counts are recorded.

## 6. Tests

Report exact commands and results.

Do not say "tests pass" without giving the commands/results.

## 7. Traceability

Return:

| Requirement                      | Implementation | Test | Status    |
| -------------------------------- | -------------- | ---- | --------- |
| source_id + external_id identity | ...            | ...  | PASS/FAIL |
| NEW detection                    | ...            | ...  | PASS/FAIL |
| UPDATE detection                 | ...            | ...  | PASS/FAIL |
| UNCHANGED detection              | ...            | ...  | PASS/FAIL |
| deadline change                  | ...            | ...  | PASS/FAIL |
| addendum/change marker           | ...            | ...  | PASS/FAIL |
| crash safety                     | ...            | ...  | PASS/FAIL |
| concurrency                      | ...            | ...  | PASS/FAIL |
| idempotency                      | ...            | ...  | PASS/FAIL |
| correlation IDs                  | ...            | ...  | PASS/FAIL |
| RunHistory                       | ...            | ...  | PASS/FAIL |

Only mark `PASS` when there is actual implementation/test evidence.

## 8. TODOs / Risks

List unresolved specification gaps separately from implementation defects.

## 9. Scope Check

Explicitly confirm that Prompt 05 did not implement functionality belonging to Prompts 04, 06, 07, 08, 09, 10, or 11.

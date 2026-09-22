# Prompt 06 — Tender Persistence

> Paste `prompts/00-master-context.md` first.

## Read

* `PROJECT_RULES.md`
* `prompts/05-tender-deduplication.md`
* `docs/03-tender-data-model.md` — full: entities, fields, enums, relationships, constraints, migration policy
* `docs/04-pipeline-spec.md`

  * §4.2
  * §4.3
  * §4.7
  * §4.8
  * §4.14
* `docs/02-technical-architecture.md`

  * §2.9
  * migrations
  * connection management
  * repository/data-access boundaries
* `prompts/02-database.md`
* `prompts/03-infrastructure.md`
* `docs/11-testing-strategy.md`
* `docs/14-acceptance-criteria.md`

Before implementing anything, inspect the **actual Prompt 05 implementation** and its persistence/repository interfaces.

---

# Task

Implement the **shared persistence/storage layer** used by:

* Prompt 05 deduplication
* Prompt 07 document discovery
* Prompt 08 document downloading
* Prompt 09 document processing
* Prompt 11 email/notification logic
* later pipeline orchestration

The persistence layer must remain **source-neutral and pipeline-neutral**.

No WAHO-specific concepts, HTML structures, crawler behavior, AI logic, email logic, or document-processing logic may be baked into the generic models/repositories.

---

# 1. Critical ownership boundary

Prompt 05 owns:

* deduplication decisions;
* classification as `NEW`, `UPDATE`, or `UNCHANGED`;
* material-change detection;
* the deduplication transaction semantics.

Prompt 06 owns:

* the database models/entities;
* repositories/data-access implementation;
* migrations;
* persistence of the state required by Prompt 05;
* persistence of Tender, Document, RunHistory and related entities defined by the specification;
* transactional database operations required by the Prompt 05 repository contract.

**Do not move deduplication business logic into Prompt 06.**

**Do not create a second or competing "seen tender" mechanism.**

Prompt 06 must implement the persistence seam that Prompt 05 consumes.

If Prompt 05 already introduced repository interfaces, preserve their public contract unless there is a documented specification conflict.

If a contract needs adjustment, explain why before changing it.

---

# 2. Tender persistence

Persist tenders according to `docs/03-tender-data-model.md`.

For a new tender:

```text
NEW candidate
    ↓
create Tender
```

For an existing tender classified as UPDATE:

```text
UPDATE candidate
    ↓
update the appropriate mutable Tender fields
```

For an unchanged tender:

```text
UNCHANGED
    ↓
do not create a duplicate Tender
```

Preserve the tender's original first-seen correlation identity where the data model requires it.

Do not replace the original first-seen correlation ID merely because a tender is updated.

The unique business identity remains:

```text
source_id + external_id
```

Enforce this at the database level.

Do not create a WAHO-specific identity field.

---

# 3. Model fidelity

Models must follow `docs/03-tender-data-model.md`.

Do not invent fields simply because a later pipeline stage might find them convenient.

Before adding any field, determine whether it is supported by the specification.

If a required persistence concept is missing from the current data model:

1. identify the gap;
2. determine whether an additive migration is appropriate;
3. do not silently invent a business field;
4. report the decision in the final report.

Never contradict spec v1.1.

If the specification conflicts with the existing implementation, stop and report the conflict rather than choosing arbitrarily.

---

# 4. Repository interfaces

Implement repository/service interfaces so higher-level code does not directly manipulate ORM models.

At minimum provide the persistence seams required by the existing architecture for:

### Tender

Operations such as:

* get by `source_id + external_id`
* create
* update mutable fields
* list/query as required by later stages

Use the actual domain names already established in the project.

### Seen tender / dedup persistence

Provide the repository operations required by Prompt 05, such as:

* lookup by `source_id + external_id`
* existence check where appropriate
* atomic insert/upsert/claim operation where required
* update persisted comparison state
* list by source where required

These operations must support Prompt 05's existing transaction and concurrency contract.

Do not move the classification logic itself into the repository.

The repository persists state; the deduplication service decides:

```text
NEW
UPDATE
UNCHANGED
```

---

# 5. Concurrency and uniqueness

The database must enforce:

```text
(source_id, external_id) UNIQUE
```

Do not rely only on application code.

Verify behavior when two transactions attempt to create the same tender simultaneously.

The persistence layer must safely support Prompt 05's concurrency behavior.

Document:

* uniqueness constraint;
* transaction isolation/locking strategy where relevant;
* conflict handling;
* retry behavior if required.

Do not introduce an unsafe:

```text
SELECT
if missing:
    INSERT
```

pattern as the sole protection against concurrent creation.

---

# 6. Tender status transitions

Implement status persistence using the **exact enum and transition rules in `docs/03-tender-data-model.md`**.

Do not invent status values.

Do not invent transition rules.

The repository/service must support idempotent valid transitions.

Illegal transitions must be rejected according to the specification.

Test at least:

```text
valid transition → succeeds
same valid state transition → behaves idempotently where specified
illegal transition → rejected
```

Document the actual transition matrix implemented.

Do not implement AI, document processing, or email behavior merely because those stages correspond to later statuses.

---

# 7. Document persistence foundation

Create the document rows/entities required by `docs/03-tender-data-model.md` so later prompts can use them.

Prompt 06 owns the **database representation and persistence seam**.

Prompt 07 owns attachment/link discovery.

Prompt 08 owns downloading.

Prompt 09 owns document processing/extraction/OCR.

Therefore Prompt 06 must NOT:

* crawl attachment pages;
* download files;
* calculate download checksums;
* OCR documents;
* extract document text;
* infer document content.

Document rows should exist with the appropriate initial/default status defined by the specification.

Do not invent document-processing statuses that are not specified.

---

# 8. RunHistory persistence

Implement the RunHistory persistence required by the specification.

Run lifecycle must support:

```text
RUNNING
   ↓
COMPLETED
```

and:

```text
RUNNING
   ↓
ERRORED
```

Use the exact enum/status values defined by the specification.

Persist at least the fields actually defined by the model for:

* run identity
* source
* start time
* end time
* listing count
* new count
* error/failure information
* relevant correlation information

Do not invent fields simply to satisfy this prompt.

RunHistory must survive process restarts.

Nothing about crawl/run state may exist only in memory.

A simulated failure must leave the database in a reconstructable state according to the specification.

---

# 9. Correlation IDs and auditability

Every persisted row that the specification requires to carry a correlation ID must do so.

Use the existing project correlation-ID mechanism.

Do not create a second tracing system.

Preserve the distinction between:

```text
source_id + external_id
```

which identifies the tender,

and:

```text
correlation ID
```

which identifies/traces execution.

Verify that a tender's persistence history can be queried by correlation ID sufficiently to support the audit/timeline requirement.

The v1.1 specification requires the tender journey to be reconstructable through the system's logs/data, including run history and later document/verdict/email stages.

Do not store sensitive secrets in correlation/audit fields.

---

# 10. Transaction boundaries

Define and document transaction boundaries for:

* tender creation;
* tender update;
* seen-state persistence required by Prompt 05;
* RunHistory updates;
* status transitions.

Transactions must be explicit where atomicity is required.

Do not claim crash safety merely because a repository method happens to use a database connection.

Test rollback behavior.

For example:

```text
begin transaction
    write required state
    simulated failure
rollback
```

Verify that partially committed business state is not left behind.

Where Prompt 05 requires a state transition and persistence to become durable atomically, preserve that contract.

---

# 11. Migrations

All schema changes must use additive migrations.

Rules:

* do NOT rewrite the baseline migration from `prompts/02`;
* do NOT modify historical migrations to make the current schema work;
* create a new migration for every required schema change;
* migrations must be ordered and reproducible;
* fresh database must migrate successfully to the current head;
* existing database must migrate without data loss.

If a migration changes an existing column or constraint, explain why and how it remains compatible with the migration policy.

Do not silently drop data.

---

# 12. Connection management

Follow the connection-management architecture from `docs/02-technical-architecture.md`.

Verify:

* connections are acquired/released correctly;
* transactions are closed/rolled back correctly;
* failed transactions cannot poison subsequent operations;
* worker processes can safely perform repeated runs;
* test suite does not leak connections.

Do not introduce a second database connection-management mechanism.

---

# 13. Source-neutral design

Generic persistence code must not contain:

* WAHO selectors;
* WAHO URLs;
* WAHO parser logic;
* source-specific HTML;
* WAHO-specific status values;
* crawler-specific business logic.

The persistence layer should be usable by another source adapter without modification to its models.

---

# 14. Security

Never persist:

* API keys in plaintext;
* passwords;
* authentication cookies;
* provider secrets;
* mail credentials;
* other secrets

unless the specification explicitly defines an encrypted-secret mechanism for that entity.

Never log secrets.

Do not store raw HTML bodies unless the data model explicitly declares such a field.

Do not add arbitrary raw-payload columns merely because the source adapter has raw metadata.

Respect the model fields defined in `docs/03-tender-data-model.md`.

---

# Out of Scope

Do NOT implement:

* deduplication decision logic;
* WAHO crawling;
* source discovery;
* detail-page discovery;
* attachment enumeration;
* document downloads;
* file checksum calculation;
* OCR;
* text extraction;
* table extraction;
* AI triage;
* AI verdict generation;
* email composition;
* email sending;
* notification delivery;
* admin UI;
* RAG/vector search;
* pipeline scheduling/orchestration.

Prompt 06 provides persistence infrastructure for these later stages; it does not implement them.

---

# Tests

Add tests alongside the implementation.

## 1. Tender creation

Create a valid tender.

Verify:

* correct fields persisted;
* required correlation information persisted;
* `source_id + external_id` identity enforced.

---

## 2. Tender update

Create Tender A.

Apply a valid update.

Verify:

* mutable fields update;
* original first-seen correlation identity remains intact;
* identity does not change;
* no duplicate tender is created.

---

## 3. Idempotent update

Apply the same update twice.

Verify the second operation does not create duplicate state or corrupt timestamps/status.

---

## 4. Unique constraint

Attempt to create the same:

```text
source_id + external_id
```

twice.

Verify the database constraint prevents duplication.

---

## 5. Concurrent creation

Run two transactions attempting to create the same tender concurrently.

Verify:

* only one business identity exists;
* uniqueness is enforced by the database;
* repository behavior is deterministic;
* connection/transaction state remains healthy afterward.

---

## 6. Seen-state repository

Test the exact repository operations consumed by Prompt 05.

Verify:

* lookup;
* existence;
* insert/upsert/claim behavior;
* update comparison state;
* concurrent access.

Do not duplicate Prompt 05's classification tests here; test persistence behavior.

---

## 7. Status transitions

Use the exact enum from `docs/03-tender-data-model.md`.

Verify:

* every valid transition required by the specification;
* illegal transitions rejected;
* idempotent behavior where specified.

Return the actual transition matrix in the report.

---

## 8. Document rows

Create a document row using only fields/statuses defined by the model.

Verify that later document stages can query/update it.

Do not test downloading or processing here.

---

## 9. RunHistory

Test:

```text
start → RUNNING
complete → COMPLETED
failure → ERRORED
```

Verify:

* start/end timestamps;
* counts;
* source association;
* correlation information;
* failure information;
* persistence after simulated failure.

---

## 10. Correlation query

Create representative records using a correlation ID.

Verify that the persistence layer can retrieve the records required for timeline reconstruction.

---

## 11. Transaction rollback

Start a transaction, perform writes, deliberately raise a failure, and verify rollback.

Confirm no partial business state remains where atomicity is required.

---

## 12. Migration test

From an empty/fresh database:

```text
run all migrations
```

Verify:

```text
migration head reached successfully
```

Also test migration from the existing baseline database state.

Verify there is no destructive/data-loss migration.

---

## 13. Connection lifecycle

Run repeated repository operations and failure cases.

Verify:

* no connection leaks;
* rollback works;
* later operations still succeed after a failed transaction.

---

## 14. Regression

Run:

* Prompt 04 tests
* Prompt 05 tests
* Prompt 06 unit tests
* database integration tests
* full test suite
* lint
* type checking

Record exact commands/results.

No real WAHO network access, LLM calls, or email delivery should be required for this persistence test suite.

---

# Rules

* Follow `docs/03-tender-data-model.md` exactly.
* Do not invent business fields or enums.
* Do not move Prompt 05 deduplication logic into repositories.
* Do not create a second seen-tender mechanism.
* Do not rewrite baseline migrations.
* Use additive migrations.
* Use database constraints for database invariants.
* Use transactions for atomic operations.
* Keep models and repositories source-neutral.
* Never log or persist secrets.
* Do not implement downstream stages.
* Do not silently resolve specification conflicts.

If a requirement cannot be implemented without contradicting the existing model or Prompt 05 contract, STOP and report the conflict before inventing a solution.

---

# Report

Provide:

## 1. Files changed

Every file changed and why.

## 2. Entity/column mapping

Produce:

| Specification entity/field | Implementation | Migration | Test | Status |
| -------------------------- | -------------- | --------- | ---- | ------ |

Only mark PASS with actual evidence.

## 3. Repository interfaces

List:

* interface
* method
* purpose
* caller/stage

Clearly distinguish:

```text
Prompt 05 decision logic
```

from:

```text
Prompt 06 persistence implementation
```

## 4. Status transition matrix

Show the actual allowed transitions from the specification.

## 5. Migration list

For every migration:

* migration name/version;
* tables/columns/constraints affected;
* additive/non-destructive reasoning.

## 6. Transaction/concurrency strategy

Explain:

* uniqueness enforcement;
* transaction boundaries;
* concurrent insert/update behavior;
* rollback behavior.

## 7. RunHistory lifecycle

Explain:

```text
RUNNING → COMPLETED
RUNNING → ERRORED
```

and the persisted fields/counts.

## 8. Correlation/timeline readiness

Explain how records can be queried/reconstructed by correlation ID.

## 9. Tests

Give exact commands and results.

Do not simply say "tests pass."

## 10. Open items

Separate:

* specification gaps;
* implementation TODOs;
* risks;
* assumptions.

Do not silently turn an unresolved business decision into an implementation rule.

## 11. Scope check

Explicitly confirm that no functionality belonging to Prompts 04, 05, 07, 08, 09, 10, or 11 was accidentally implemented.

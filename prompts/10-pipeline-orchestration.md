# Prompt 10 — Pipeline Orchestration

> Paste `prompts/00-master-context.md` first.

## Objective

Implement the **headless worker / crawl-run orchestrator** that coordinates the already-implemented source pipeline stages:

`04 Discovery → 05 Deduplication → 06 Persistence → 07 Document Discovery → 08 Document Acquisition → 09 Document Understanding`

Prompt 10 owns **execution order, run lifecycle, stage coordination, configuration reload, retry orchestration, failure isolation, resumability, and RunHistory integration**.

It does **not** reimplement any behavior owned by Prompts 04–09.

There is **no AI and no email** in this prompt.

---

# Read First

Read these completely before changing code:

* `PROJECT_RULES.md`
* `docs/04-pipeline-spec.md`

  * §4.1 pipeline diagram
  * §4.2 stages
  * §4.3 crash safety
  * §4.4 retry
  * §4.5 partial failures
  * §4.8 run history
  * §4.10 configuration reload / admin independence
  * §4.11 begin
* `docs/02-technical-architecture.md`

  * worker architecture
  * admin independence
* `docs/13-open-decisions.md`
* `prompts/04-waho-source-discovery.md`
* `prompts/05-tender-deduplication.md`
* `prompts/06-tender-persistence.md`
* `prompts/07-document-discovery.md`
* `prompts/08-document-download.md`
* `prompts/09-document-processing.md`
* `implementation/00-current-state.md`
* `implementation/01-decisions.md`
* `implementation/02-known-issues.md`
* `implementation/03-next-task.md`

Also inspect the actual implementations and tests for Prompts 04–09 before modifying anything.

---

# Ownership Boundary

Prompt 10 is an **orchestrator**, not a replacement implementation of earlier stages.

The ownership boundary is:

| Prompt | Owns                                                                                                                               |
| ------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| 04     | Source listing/discovery                                                                                                           |
| 05     | New/update/unchanged decision and dedup transaction semantics                                                                      |
| 06     | Tender/Document/RunHistory persistence primitives                                                                                  |
| 07     | Tender detail and attachment discovery                                                                                             |
| 08     | Document acquisition/storage                                                                                                       |
| 09     | Document extraction/understanding and bundle construction                                                                          |
| **10** | **Execution order, run lifecycle, configuration reload, stage coordination, retry orchestration, failure isolation, resumability** |
| 11     | Test-mode email                                                                                                                    |
| 12     | Audit/timeline and alert implementation                                                                                            |
| 13     | AI triage                                                                                                                          |
| 14     | AI verdict                                                                                                                         |
| 15/16  | Admin API/UI                                                                                                                       |

Do not move business logic from Prompts 04–09 into the orchestrator.

Do not create duplicate repositories, persistence models, document stores, discovery logic, download logic, or extraction logic.

The orchestrator must call the existing public interfaces/contracts of the previous stages.

---

# 1. Worker Independence

The worker must run without the admin HTTP application being available.

The worker must:

* load configuration from the persisted/configuration source directly
* not call the admin API to obtain configuration
* not require the admin web server to be running
* continue operating if the admin app is unavailable
* use the last successfully persisted configuration when no newer configuration is available
* record configuration/version information used by the run where supported

Do not introduce runtime coupling such as:

```text
worker → HTTP request → admin API → configuration
```

The intended relationship is:

```text
             ┌───────────────┐
             │ persisted     │
             │ configuration │
             └───────┬───────┘
                     │
              reload at run start
                     │
             ┌───────▼───────┐
             │ headless      │
             │ worker        │
             └───────┬───────┘
                     │
       ┌─────────────┼──────────────┐
       ▼             ▼              ▼
    source A      source B       source C
```

---

# 2. Run Scheduling and Source Isolation

Implement the worker execution boundary for enabled sources.

The worker must support:

```text
worker
  → determine enabled/due sources
  → reload configuration
  → execute one source run
  → finalize RunHistory
  → continue with other sources
```

Each enabled source must have an isolated run.

A failure in source A must not terminate the worker or prevent source B from running.

At minimum provide a clear callable boundary equivalent to:

```text
run_source(source_id)
```

and a worker-level loop that can invoke it for each enabled/due source.

If the project already has a scheduler abstraction, integrate with it rather than creating a competing scheduler.

Do not invent external scheduling infrastructure where the existing architecture does not require it.

---

# 3. Configuration Reload

Configuration must be re-read at the **start of every source run**.

Never rely on a process-wide cached configuration across independent runs.

A run should capture the configuration snapshot/version it actually used.

Required behavior:

```text
run N:
    reload config
    use config N

configuration changes

run N+1:
    reload config
    use config N+1
```

Test this by changing configuration between two runs and proving that the second run observes the new configuration without restarting the worker.

If configuration cannot be reloaded:

* use the last successfully persisted configuration where the architecture supports this
* record the configuration-load problem
* do not silently continue with an unknown configuration state

Never log secrets from configuration.

Never expose API keys, passwords, tokens, credentials, or secret configuration values in:

* logs
* exceptions
* RunHistory
* test output
* returned API objects

---

# 4. Run Identity and Correlation Identity

Do not conflate:

* `run_id`
* pipeline correlation ID
* tender identity
* tender's persisted first-seen correlation ID

A run must have a unique run identity.

Each source run must also have a correlation context that is propagated through the stages.

Example:

```text
run_id
  └── correlation context
        ├── discovery
        ├── deduplication
        ├── persistence
        ├── detail discovery
        ├── document acquisition
        └── document processing
```

The orchestrator must **not overwrite the persisted tender correlation identity established by Prompt 06** merely because the tender appears in a later run.

Use the existing correlation-ID conventions from Prompts 04–09.

Every stage invocation, structured error, and relevant log event must remain traceable to its run/source/tender context.

---

# 5. RunHistory Lifecycle

Create the RunHistory record at **run start**.

At creation time record the information that is actually known, such as:

* source
* run identity
* start time
* initial status
* correlation context
* configuration/version reference where supported

Do **not** claim final listing counts before discovery has occurred.

During the run, update the appropriate counters/status fields as the pipeline progresses.

At run end:

### Successful run

Set the final status to:

```text
COMPLETED
```

and persist final counts and timing.

### Run-level failure

Set the final status to:

```text
ERRORED
```

and persist:

* error code/category
* failed stage
* relevant correlation IDs
* counts accumulated before failure
* timing/finalization information

A worker crash must not leave the system permanently blocked.

A later run must be able to start normally.

Do not introduce a global "worker locked forever" state.

---

# 6. Pipeline Execution Order

For each source run, execute the available pipeline in this order:

```text
04 Discovery
    ↓
05 Deduplication
    ↓
06 Persistence
    ↓
07 Tender Detail / Document Discovery
    ↓
08 Document Acquisition
    ↓
09 Document Understanding
```

Do not reorder these stages.

Do not bypass an earlier stage merely because a later stage can technically operate without it.

The orchestrator must pass the output of each stage to the next stage using the established contracts.

For example:

```text
DiscoveryCandidate
    ↓
DeduplicationDecision
    ↓
Persisted Tender
    ↓
TenderDetail / Attachments
    ↓
Persisted Documents
    ↓
Stored Documents
    ↓
Extraction Results
    ↓
Tender Document Bundle
```

---

# 7. Incremental Phase-1 Wiring

Prompt 10 is being implemented incrementally.

If a later stage is genuinely not implemented yet, the orchestrator may skip that stage **only when its implementation status is explicitly known**.

A skipped stage must produce a clear structured stage status such as:

```text
SKIPPED_NOT_IMPLEMENTED
```

or the project's equivalent.

Do not silently skip a stage because:

* configuration is missing
* an expected object is absent
* a runtime error occurred
* a dependency failed
* the stage is inconvenient to execute

Those are failures or configuration conditions, not "not implemented."

A skipped stage must be distinguishable from:

```text
COMPLETED
FAILED
PARTIAL
```

Do not mark a run as fully completed merely because an unimplemented stage was skipped.

Use the existing project semantics for incremental development if they already define the distinction.

---

# 8. Stage Statuses

The orchestrator must preserve stage-level outcomes.

At minimum, each stage execution should be distinguishable as appropriate between:

```text
PENDING
RUNNING
COMPLETED
PARTIAL
FAILED
SKIPPED_NOT_IMPLEMENTED
```

Use existing project enums/types if they already exist.

Do not replace stage status with a single final run boolean.

A run should remain reconstructable after completion or failure.

---

# 9. Failure Semantics

Distinguish between:

### Run/source-level failure

Examples:

* source unreachable
* discovery cannot execute
* required stage contract fails
* unrecoverable persistence failure
* orchestrator infrastructure failure

These should cause the affected source run to become `ERRORED`.

### Item/document-level failure

Examples:

* one document download fails
* one document is corrupt
* one document cannot be OCR'd

These must respect the semantics established by Prompts 08–09.

Do not convert a document-level warning/incomplete-input condition into a whole-run crash merely because an exception exists internally.

For Prompt 09 specifically, preserve:

```text
individual document failure
    → document failure status
    → warning/incomplete input
    → tender processing may continue
```

where that is the established contract.

---

# 10. Structured Errors

Use the project's structured error codes.

Do not replace structured errors with arbitrary strings.

Examples include:

```text
source_unreachable
parser_mismatch
download_failed
document_processing_failed
configuration_load_failed
stage_failed
```

Use existing codes where they exist.

Introduce a new code only when there is a real orchestration-specific condition that is not already represented.

Every unrecoverable stage failure must retain:

* source
* run identity
* stage
* correlation context
* structured error code
* safe diagnostic detail

Never include secrets or raw document contents in error messages.

---

# 11. Retry and Backoff

Implement retry orchestration according to `docs/04-pipeline-spec.md` §4.4.

Retries must:

* be bounded
* use configured limits
* use backoff
* avoid hammering the source
* preserve correlation context
* record retry attempts
* terminate deterministically after the configured limit

Do **not** create duplicate retry loops for behavior already owned by Prompt 08.

Before adding retry behavior, inspect the Prompt 08 implementation.

The ownership should be:

```text
Prompt 08
    → document acquisition retry mechanics where defined

Prompt 10
    → orchestration-level retry/retry scheduling where defined
```

Do not accidentally multiply retries.

For example, avoid:

```text
orchestrator retries 3x
    ×
downloader retries 3x
=
9 network attempts
```

unless the architecture explicitly requires that behavior.

Document the resulting retry matrix.

---

# 12. Crash Safety

The worker must tolerate a process crash at any stage.

A crash must not cause:

* duplicate tender identities
* permanent run locks
* corrupted committed persistence
* loss of already committed seen-state
* reprocessing of successfully committed work merely because the worker restarted

Rely on the transaction/idempotency guarantees already established by Prompts 05–09.

Do not duplicate those persistence mechanisms inside the orchestrator.

After a simulated crash, the next run must be able to continue normally.

Where the pipeline supports resumability, rerunning a stage should reuse already persisted state rather than unnecessarily repeating expensive work.

---

# 13. Idempotency and Resumability

The orchestrator must be safe to rerun.

A repeated run over the same source must rely on the existing contracts for:

* tender identity
* seen-state
* document identity
* stored documents
* extraction artifacts
* bundle reuse

Do not create a second notion of tender identity.

Do not use document checksum as tender identity.

Do not introduce a second deduplication system.

The orchestrator's responsibility is to invoke the existing idempotent stages correctly.

---

# 14. Dry-Run

Provide a dry-run mode suitable for:

> "Test this source"

Default dry-run behavior must be **read-only**.

Unless the project specification explicitly defines a narrower exception, dry-run must:

* perform discovery against the source/fixture
* evaluate what stages would run
* report deduplication outcomes
* report what persistence/acquisition/processing actions would occur
* perform no business writes
* create no seen-tender state
* create no Tender/Document business records
* create no extraction artifacts
* send nothing
* modify no source state

Do not interpret dry-run as "normal run with email disabled."

The result should be an inspectable dry-run report/result object.

If RunHistory itself is explicitly defined by the architecture as an operational audit record that may be written during dry-run, follow the source specification; otherwise default to no persistence.

Test and document the actual behavior.

---

# 15. Source Isolation

If multiple sources are enabled:

```text
source A fails
    ↓
record A failure
    ↓
finalize A RunHistory
    ↓
source B still runs
```

A source failure must not terminate the entire worker process.

Likewise, a failure in one tender must not unnecessarily terminate unrelated tenders from the same source when the stage contracts permit item-level isolation.

Preserve failures for later audit/diagnostics.

---

# 16. Alert Hook

Prompt 10 does **not** implement alert delivery.

It must expose/use an orchestration-level alert hook for failures that meet the configured threshold.

Conceptually:

```text
stage failure
    ↓
record structured failure
    ↓
invoke alert hook
    ↓
continue/finalize according to failure severity
```

The actual alert transport/delivery belongs to Prompt 12.

Do not implement email, Slack, SMS, or another alert transport here.

If Prompt 12's interface does not yet exist, create only the smallest clean seam required for later integration.

Do not invent the final alerting implementation.

---

# 17. No AI / No Email

Do not implement or invoke:

* AI triage
* AI verdict
* LLM providers
* verdict formatting
* business email dispatch
* email provider failover
* notification delivery

Prompt 10 ends after document understanding.

The intended pipeline boundary is:

```text
04 → 05 → 06 → 07 → 08 → 09 → STOP
```

Prompts 13–14 will later extend this pipeline.

---

# 18. Integration Contract

The primary integration test must prove this chain:

```text
source fixture
    ↓
04 discovery
    ↓
05 deduplication
    ↓
06 persistence
    ↓
07 detail/document discovery
    ↓
08 acquisition/storage
    ↓
09 extraction/bundle
    ↓
RunHistory finalized
```

Use realistic fixtures.

Do not mock every stage into trivial success.

Mocks may be used for specific failure injection, but at least one test must exercise the actual integrated 04→09 path.

Verify persisted outputs, not merely returned Python objects.

---

# 19. Required Tests

Implement or extend tests for all of the following.

### A. Full offline source run

Prove:

```text
RunHistory START
→ discovery
→ deduplication
→ persistence
→ detail discovery
→ acquisition
→ processing
→ RunHistory COMPLETED
```

Verify final counts.

### B. Mid-run crash

Inject a deterministic failure/crash after a meaningful stage.

Then start a fresh run.

Verify:

* previous committed records remain
* no duplicate tender identity
* no permanently stuck RUNNING state
* next run succeeds
* worker can continue

### C. Configuration reload

Run once.

Change configuration.

Run again without restarting the worker.

Verify the second run observes the changed configuration.

### D. Admin app unavailable

Stop/disable the admin HTTP application.

Run the worker.

Verify that the worker still operates using persisted configuration.

### E. Source failure isolation

Enable at least two sources/test sources.

Force source A to fail.

Verify source B still runs.

### F. Stage failure

Force one orchestration stage to fail.

Verify:

* structured error
* failed stage recorded
* RunHistory becomes `ERRORED` when the failure is run-fatal
* correlation context preserved
* no silent failure

### G. Document-level partial failure

Use the Prompt 09 mixed-document fixture.

Verify that an individual document failure follows Prompt 09 semantics and does not incorrectly become a whole-run failure.

### H. Retry/backoff

Inject a retryable failure.

Verify:

* configured attempt limit
* backoff
* no unbounded retry
* retry context recorded
* final outcome deterministic

### I. Dry-run

Verify that dry-run:

* performs inspection
* writes no business state
* sends nothing
* leaves seen-state unchanged
* leaves document/extraction state unchanged

### J. Repeated run

Run the same fixture twice.

Verify:

* no duplicate tender identity
* existing state is reused appropriately
* extraction is not unnecessarily repeated
* RunHistory contains separate run records

### K. Correlation

Verify that a failure can be traced:

```text
RunHistory
→ stage execution
→ tender
→ document
→ error/log
```

without relying on ambiguous timestamps alone.

### L. Secret safety

Provide fake secret values in configuration.

Verify that they do not appear in:

* logs
* errors
* RunHistory
* test output

---

# 20. Regression Tests

Run the existing regression suite for Prompts 04–09.

At minimum verify that Prompt 10 did not alter:

* WAHO discovery behavior
* deduplication decisions
* persistence ownership
* tender identity
* document discovery
* document acquisition
* storage semantics
* ZIP safety
* document extraction
* bundle persistence
* extraction reuse/idempotency

If Prompt 10 requires modifying an earlier stage, stop and explain why before making the change.

Do not silently move earlier-stage ownership into the orchestrator.

---

# 21. Implementation Constraints

Keep the implementation minimal and compositional.

Prefer:

```text
Worker
  └── RunCoordinator
        ├── ConfigLoader
        ├── SourceScheduler
        ├── StageRunner
        ├── RetryPolicy
        └── RunHistory repository
```

or the project's existing equivalent.

Do not introduce unnecessary framework complexity.

Do not create a second persistence layer.

Do not create a second configuration system.

Do not create a second scheduler if one already exists.

Do not hard-code WAHO behavior into generic orchestration.

Source-specific behavior remains in source adapters.

---

# 22. Report

After implementation and tests, report:

1. Files changed.
2. Existing stage interfaces/contracts used.
3. Worker/run-loop design.
4. RunHistory lifecycle.
5. Distinction between run ID and correlation ID.
6. Configuration reload strategy.
7. Admin-independence verification.
8. Retry/backoff policy and ownership.
9. Failure-isolation semantics.
10. Stage status model.
11. Dry-run behavior.
12. Crash-safety implementation.
13. Idempotency/resumability behavior.
14. Alert-hook seam.
15. Full 04→09 integration test.
16. Failure-injection tests.
17. Regression tests.
18. Exact test commands and results.
19. Remaining TODOs or open decisions.
20. Any deviation from the existing architecture/spec.

Do not claim a test passed unless it actually ran.

Do not claim a behavior is implemented if it is only mocked or stubbed.

---

# Final Gate

Do not start Prompt 11, 12, 13, 14, or any later prompt.

End with exactly one of:

```text
PROMPT 10: VERIFIED — READY FOR NEXT PROMPT
```

or

```text
PROMPT 10: NEEDS FIXES
```

If the implementation has unresolved integration defects, use `NEEDS FIXES` and identify the concrete defects.

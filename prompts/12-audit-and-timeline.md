# Prompt 12 — Audit & Timeline

> Paste `prompts/00-master-context.md` first.

## Objective

Implement the **audit, observability, timeline, and alert-management layer** for the tender intelligence pipeline.

This layer must make each tender's processing history reconstructable from persisted facts and correlation IDs, complete the audit records required by the pipeline, and provide the alert-manager seam consumed by Prompts 10 and 11.

The implementation must not create a second competing pipeline, notification system, or admin application.

The central invariant is:

> A tender timeline is a read-only reconstruction of persisted events/state from existing pipeline records. It must never invent, infer, or synthesize processing events that were not persisted.

---

# 1. Read Before Implementing

Read:

* `PROJECT_RULES.md`
* `docs/00-project-brief.md`
* `docs/03-tender-data-model.md`
* `docs/04-pipeline-spec.md`

  * §4.6 incomplete inputs
  * §4.8 correlation IDs and RunHistory
  * §4.9 alerting
  * §4.11 configuration/audit requirements
* `docs/08-email-notification-spec.md`

  * §8.10 alerts
  * §8.11 attached-vs-linked logging
* `docs/10-security-spec.md`
* `docs/13-open-decisions.md`
* `prompts/10-pipeline-orchestration.md`
* `prompts/11-test-mode-email.md`
* relevant implementation-state files

Inspect the actual existing implementation before changing schemas or services.

---

# 2. Establish Ownership Boundaries

Prompt 12 owns:

* audit/observability persistence
* timeline query/reconstruction
* RunHistory completion where Prompt 10 left a persistence seam
* NotificationLog audit snapshot
* ConfigChangeLog
* AlertEvent persistence
* alert-manager orchestration
* alert throttling/deduplication
* recovery alerts
* reminder scheduling
* health-digest construction/queueing
* alert deep-link generation
* incomplete-input visibility in the timeline/audit trail

Prompt 12 consumes:

* Prompt 04 source-stage outcomes
* Prompt 05 dedup/change outcomes
* Prompt 06 persisted tender/run state
* Prompt 07 document discovery
* Prompt 08 acquisition state
* Prompt 09 processing/bundle state
* Prompt 10 worker/run lifecycle and stage outcomes
* Prompt 11 notification/provider outcomes

Prompt 12 does NOT own:

* source crawling
* tender deduplication
* tender persistence
* document acquisition
* document processing/OCR
* AI triage
* AI verdict generation
* email templates
* provider retry/backoff
* provider failover
* attachment planning
* admin HTTP endpoints
* admin UI
* general security hardening

Do not duplicate logic already owned by those prompts.

If an existing implementation needs a small persistence hook to expose information required by Prompt 12, add the narrowest compatible seam rather than moving ownership.

---

# 3. Preserve Existing Identity Semantics

Do not collapse identifiers.

These must remain distinct:

* run ID
* correlation ID
* tender identity
* tender first-seen correlation ID
* document identity
* processing/bundle identity where applicable
* notification ID
* NotificationAttempt ID
* alert ID
* configuration-change ID
* idempotency/dedupe key

The timeline must be able to correlate these identifiers without replacing them with one generic event ID.

---

# 4. Correlation Timeline

Implement a read-only timeline query seam.

Input:

```text
correlation_id
```

The query must reconstruct, from persisted rows, the relevant lifecycle:

```text
seen
→ documents discovered/fetched
→ documents acquired
→ extraction/processing
→ triage
→ verdict
→ notification
```

Only include stages for which persisted evidence exists.

Do not fabricate missing stages.

Where available, expose:

* timestamp
* stage
* status
* correlation ID
* tender ID
* run ID
* source
* document IDs
* processing artifact/bundle identity
* incomplete-input state
* notification ID
* provider used
* notification status
* relevant failure classification
* alert IDs associated with the lifecycle

The exact stage names must follow the existing pipeline specification and implementation.

---

# 5. Timeline Ordering

Timeline ordering must be deterministic.

Use persisted timestamps appropriate to each event/state.

Handle:

* identical timestamps
* retries
* multiple documents
* multiple notification attempts
* multiple providers
* update notifications
* failed stages
* recovered stages

Define and document a deterministic secondary ordering rule.

Do not depend on database insertion order.

---

# 6. Timeline Must Be Read-Only

The timeline query must not:

* mutate tender state
* create events
* create audit rows
* create notifications
* trigger retries
* trigger alerts
* update timestamps
* modify RunHistory
* modify NotificationLog

Repeated timeline queries must produce the same result unless underlying persisted state has changed.

---

# 7. Timeline Query Performance

Design the query around the expected data volume.

Add appropriate indexes for:

* correlation ID
* tender ID
* run ID
* notification/tender relationship
* alert/tender relationship
* timestamps used for timeline ordering
* other query dimensions actually required by the implementation

Avoid:

* loading the entire database
* unbounded per-document queries
* N+1 queries
* reconstructing timelines by scanning unrelated tenders

Measure reconstruction performance with a representative fixture.

The target is the specification's acceptable cost/time bound.

If the specification does not define a numeric threshold, document the measured baseline and query shape rather than inventing an arbitrary SLA.

---

# 8. RunHistory Completion

Inspect the Prompt 10 implementation first.

If RunHistory is already correctly persisted, do not create a second RunHistory implementation.

Complete or extend it only where required by the documented contract.

Verify that RunHistory can contain:

* run ID
* source
* start time
* end time
* status
* listings found
* new count
* error count
* failed correlation IDs
* relevant configuration/version context where required

Ensure cross-stage consistency.

A run that fails in a later stage must not appear successfully completed merely because discovery succeeded.

Likewise, document-level failures must not incorrectly become whole-run failures if the pipeline specification defines them as recoverable.

Preserve Prompt 10's ownership of run lifecycle semantics.

---

# 9. NotificationLog

Implement or complete the audit representation required by `docs/08-email-notification-spec.md §8.11`.

At send/finalization time, persist a snapshot containing, as applicable:

* tender identity
* notification identity
* notification type
* recipients
* attachments
* links
* provider used
* dedupe/idempotency key
* possible_duplicate
* notification status
* relevant timestamps
* correlation ID

The snapshot must represent what was actually used for that notification.

Do not store a live reference that changes when configuration changes.

---

# 10. Notification History Immutability

Prove:

```text
notification sent
→ NotificationLog snapshot persisted
→ recipient configuration edited
→ historical NotificationLog queried
```

Expected:

> historical recipients remain unchanged.

Repeat for:

* attachments
* links
* provider
* dedupe key
* notification status
* possible_duplicate

Historical audit records must not be rewritten by later configuration changes.

If correction/versioning is required by the specification, use an explicit new record rather than mutating historical facts.

---

# 11. ConfigChangeLog

Implement audit logging for configuration changes required by the specifications.

Record, as appropriate:

* change ID
* timestamp
* actor/source
* configuration area/key
* old value representation
* new value representation
* relevant metadata
* correlation ID where applicable

Never persist secrets.

Examples include:

* provider credentials
* API keys
* passwords
* tokens
* authorization headers
* private keys
* other secret material identified by the security specification

Use explicit scrubbing/redaction rules from `docs/10-security-spec.md`.

Do not rely solely on developers remembering which fields are secret.

---

# 12. Config Audit Scrubbing

Create tests with deliberately fake secrets.

Attempt configuration changes containing:

```text
API keys
passwords
tokens
authorization headers
private-key-like material
```

Verify:

* secrets are absent from ConfigChangeLog
* secrets are absent from logs
* secrets are absent from exceptions
* non-secret configuration metadata remains auditable

If the specification requires a redaction marker, use the exact documented representation.

---

# 13. Alert Manager

Implement the alert manager described in `docs/04-pipeline-spec.md §4.9`.

The alert manager should accept normalized alert events from upstream stages.

It must not contain business logic for determining whether a crawler, AI engine, provider, or document processor is actually unhealthy beyond the trigger conditions specified.

Its responsibilities are:

```text
alert trigger
→ normalize
→ correlate
→ throttle/dedupe
→ persist AlertEvent
→ route to developer alert list
→ recovery/reminder handling
```

---

# 14. Alert Triggers

Implement the documented trigger conditions, including where specified:

* source fails N runs
* parser mismatch
* AI failure
* invalid AI output after retry
* AI budget reaches 80%
* AI budget reaches 100%
* email fails across every provider
* provider circuit breaker opens
* daily health digest

Do not invent thresholds.

Use configuration/specification values where defined.

If a trigger depends on a metric owned by another prompt, consume its normalized event/metric rather than duplicating its calculation.

---

# 15. Alert Event Schema

Persist sufficient information to reconstruct why an alert occurred.

An AlertEvent should contain, according to the specification:

* alert ID
* alert type
* severity
* source/system component
* timestamp
* correlation ID where applicable
* run ID where applicable
* tender ID where applicable
* status/state
* relevant trigger metadata
* recovery relationship where applicable
* reminder information where applicable
* timeline deep link

Do not put full tender documents or raw document text into alerts.

Do not put secrets into alerts.

---

# 16. Alert Throttling

Implement deterministic throttling/deduplication.

For repeated identical failures, verify:

```text
failure
→ one alert
→ repeated failure
→ suppressed/throttled
→ reminder at configured interval
```

The exact identity key must follow the specification.

Do not accidentally deduplicate unrelated alerts merely because they have the same text.

Alert identity should distinguish the relevant:

* alert type
* source/component
* failure condition
* scope
* time/state context

Use the documented contract where it defines these dimensions.

---

# 17. Recovery Alerts

When an unhealthy condition becomes healthy:

```text
unhealthy
→ alert
→ continued unhealthy state
→ recovery
```

verify:

* one recovery alert is generated
* recovery is linked to the original unhealthy condition
* repeated healthy observations do not generate unlimited recovery alerts
* a later recurrence creates a new unhealthy alert cycle

Recovery must be stateful enough to distinguish:

```text
still unhealthy
```

from:

```text
healthy after previously unhealthy
```

---

# 18. Reminder Cadence

Implement configurable reminder intervals.

Verify:

* reminders are not generated before the configured interval
* reminders occur when the interval is reached
* reminders do not create unlimited duplicates at every poll/run
* changing the configured interval affects future reminder scheduling according to the documented configuration semantics

Do not hardcode reminder cadence.

---

# 19. Alert Delivery Boundary

Prompt 11 owns the email/provider delivery machinery.

Prompt 12 owns alert decisions and persistence.

Therefore Prompt 12 must call the existing Prompt 11 notification seam rather than implementing another mail provider.

Verify:

```text
AlertEvent
→ alert manager
→ Prompt 11 notification seam
→ Test Mode routing
→ provider
```

The alert manager must not directly call a provider adapter if Prompt 11 already owns that boundary.

---

# 20. Test Mode Safety for Alerts

Verify that alerts follow the configured developer alert list and existing Test Mode safety rules.

Business tender recipients must never be used for operational alerts.

Test:

* normal alert
* recovery alert
* reminder
* daily digest
* provider failure while delivering an alert

Verify all remain within the intended developer/alert-recipient path.

---

# 21. Alert Deep Links

Every alert that is associated with a tender/run/correlation context should carry the timeline deep-link information required by the specification.

The link must identify the correct:

* correlation/tender/run context
* timeline route/query target

Do not implement the Admin UI itself.

Provide the backend/query seam and stable link construction that Prompt 16 can consume.

---

# 22. Incomplete Inputs

Propagate `incomplete_inputs` from Prompt 09 into the audit/timeline trail.

Verify that when document processing results in:

```text
incomplete_inputs = true
```

the timeline exposes that state.

The state must not disappear simply because:

* another document succeeded
* AI triage completed
* AI verdict completed
* email was sent

If the notification layer already records the incomplete-input condition, expose the existing persisted fact rather than creating a duplicate representation.

The timeline must make it possible to distinguish:

```text
all required inputs processed
```

from:

```text
processing completed with incomplete inputs
```

---

# 23. Full Lifecycle Fixture

Create a complete deterministic fixture representing:

```text
source discovery
→ tender seen
→ deduplication
→ persistence
→ document discovery
→ document acquisition
→ document processing
→ incomplete-input condition where applicable
→ triage result
→ verdict
→ notification
→ provider attempt
→ NotificationLog
→ alert
```

Run the timeline query using the correlation ID.

Verify that the result reconstructs all persisted stages in deterministic order.

Include provider/profile information for the notification where persisted.

---

# 24. Failure Timeline

Create a fixture with failures at different stages:

* source failure
* parser mismatch
* document failure
* processing failure
* incomplete inputs
* AI failure/invalid output
* notification provider failure
* alert/recovery

Verify that the timeline distinguishes:

* successful stages
* failed stages
* retried attempts
* skipped stages
* incomplete-input state
* notification failure
* recovery

Do not represent a skipped stage as successfully completed.

---

# 25. Cross-Stage Correlation

Verify that the same tender can be traced across:

```text
RunHistory
Tender
Document
processing artifacts/bundle
triage/verdict outputs
NotificationLog
AlertEvent
```

using the correct correlation/tender identities.

Test with multiple tenders processed in the same run.

Verify that:

* Tender A's timeline does not contain Tender B events
* Tender B's timeline does not contain Tender A events
* run-level information can still identify that both belonged to the same run

---

# 26. Multiple Runs / Updates

Test a tender appearing in multiple runs.

Verify the timeline can represent:

```text
first seen
→ first processing
→ first notification
→ later run
→ material update
→ new document
→ reprocessing
→ update notification
```

Do not overwrite the original lifecycle with the latest state.

Historical events must remain reconstructable.

---

# 27. Daily Health Digest

Implement the scheduling/building seam for the daily health digest required by the specification.

Verify that the digest can summarize the relevant persisted health information.

At minimum test:

* healthy period
* source failures
* parser mismatch
* AI failures
* email/provider failures
* breaker state
* recovery events

The digest must not contain secrets or full document text.

If Prompt 11 owns final email delivery, queue/send through its notification seam rather than creating a separate mail mechanism.

---

# 28. Alert Cost / Query Efficiency

Measure alert-related queries and timeline reconstruction.

Verify that the implementation does not perform:

* full-table scans for ordinary timeline queries
* N+1 queries over documents/notifications/alerts
* repeated expensive aggregation for every timeline request when persisted state can be indexed

Document:

* important indexes
* representative dataset size
* query count
* observed runtime
* any known scaling limits

Do not invent performance numbers.

Record actual measurements.

---

# 29. Concurrency

Test concurrent:

* timeline reads
* alert triggers
* repeated identical failure events
* recovery event plus repeated failure event
* configuration changes
* notification-log writes

Verify:

* duplicate alerts are not created incorrectly
* historical NotificationLog snapshots remain immutable
* ConfigChangeLog remains complete
* timeline reads remain consistent
* alert state transitions are atomic where required

---

# 30. Crash / Restart Safety

Inject process termination at important alert-manager boundaries:

```text
before AlertEvent persistence
after AlertEvent persistence
before notification enqueue
after notification enqueue
during reminder scheduling
during recovery handling
```

Verify restart does not produce:

* lost alerts
* duplicate recovery alerts
* duplicate reminders
* duplicate notification requests
* corrupted alert state

Use durable state rather than process memory for decisions that must survive restart.

---

# 31. Security Verification

Search all Prompt 12 logs and persisted records for:

* credentials
* API keys
* tokens
* authorization headers
* raw document text
* unnecessary document contents
* private URLs that should not be exposed
* secrets inside alert payloads
* secrets inside ConfigChangeLog

Verify correlation IDs and operational metadata remain available without leaking protected content.

---

# 32. Admin Boundary

Do not implement:

* admin pages
* admin navigation
* admin HTTP controllers
* admin authentication
* timeline UI

Instead expose stable service/query contracts that Prompt 15/16 can consume.

Document the expected query inputs and returned fields.

---

# 33. Existing Implementation Compatibility

Before changing any schema:

1. inspect existing migrations
2. inspect existing RunHistory implementation
3. inspect existing NotificationLog implementation
4. inspect existing alert/event tables
5. inspect existing configuration persistence
6. inspect Prompt 10/11 integration points

Prefer additive migrations.

Do not create duplicate tables representing the same concept.

If a required table already exists, extend it only when necessary.

Preserve existing data.

---

# 34. Regression Testing

Run all relevant tests for:

* Prompt 04
* Prompt 05
* Prompt 06
* Prompt 07
* Prompt 08
* Prompt 09
* Prompt 10
* Prompt 11

Pay particular attention to:

* correlation IDs
* RunHistory
* document state
* `incomplete_inputs`
* notification idempotency
* NotificationAttempt
* Test Mode
* provider failure handling
* existing alert hooks

Prompt 12 must not regress earlier pipeline behavior.

---

# 35. Required Tests

At minimum implement and execute tests covering:

### Timeline

* complete lifecycle reconstruction
* stage ordering
* multiple tenders in one run
* multiple runs for one tender
* failed stage
* incomplete inputs
* notification/provider information
* timeline query performance

### RunHistory

* successful run
* failed run
* partial/document-level failures
* failed correlation IDs
* start/end consistency
* repeated/restarted worker behavior

### NotificationLog

* immutable recipient snapshot
* immutable attachment snapshot
* immutable link snapshot
* provider snapshot
* dedupe key
* possible duplicate
* status

### ConfigChangeLog

* ordinary config change
* secret scrubbing
* multiple changes
* actor/source metadata

### Alerts

* source failure threshold
* parser mismatch
* AI failure
* invalid AI output
* budget 80%
* budget 100%
* all-provider email failure
* breaker opened
* throttling
* recovery
* reminder cadence
* daily digest
* restart recovery
* concurrent duplicate trigger

### Security

* no secrets in logs
* no secrets in alerts
* no raw document text in alerts
* no secret values in ConfigChangeLog

---

# 36. Fix Policy

If verification or implementation reveals a defect:

1. Determine which prompt owns the defect.
2. Fix only Prompt 12-owned defects.
3. Add a regression test.
4. Re-run the affected test.
5. Re-run the complete Prompt 12 suite.
6. Re-run regression tests for Prompts 04–11.

Do not silently absorb Prompt 15/16, Prompt 17, or other later responsibilities.

If an upstream contract is missing or incorrect, document the contract mismatch unless the defect clearly belongs to Prompt 12.

---

# 37. Report

Report:

1. files changed
2. schema/migration changes
3. timeline query design
4. indexes added
5. RunHistory mapping
6. NotificationLog mapping
7. ConfigChangeLog mapping
8. AlertEvent mapping
9. alert trigger table
10. throttling/recovery/reminder behavior
11. daily digest behavior
12. incomplete-input propagation
13. alert deep-link contract
14. performance measurements
15. concurrency results
16. crash/restart results
17. security/logging results
18. Prompt 04–11 regression results
19. failures discovered
20. fixes made
21. remaining TODOs/open decisions

Do not claim a test passed unless it was actually executed.

Do not claim a timeline is reconstructable merely because the service returns a response; inspect the underlying persisted records.

Do not claim alert delivery works by directly testing a provider if Prompt 11 already owns that provider boundary. Verify the Prompt 12 → Prompt 11 seam.

---

# 38. Final Gate

Prompt 12 is complete only when:

* timelines reconstruct from persisted facts
* correlation IDs correctly join the lifecycle
* timeline queries are read-only
* timeline ordering is deterministic
* RunHistory is complete and consistent
* NotificationLog snapshots are immutable
* ConfigChangeLog records configuration changes without secrets
* AlertEvent persistence works
* alert triggers match the documented conditions
* alert throttling works
* recovery alerts work
* reminder cadence works
* daily health digest seam works
* alert delivery uses the Prompt 11 notification boundary
* alert deep links are available
* incomplete inputs are visible
* concurrency is safe
* restart/crash recovery is safe
* secrets/raw document text do not leak
* Prompt 04–11 regression tests pass
* no admin UI was implemented
* no second email/provider system was implemented
* no retry/backoff logic was duplicated from Prompt 11
* no AI logic was implemented
* no unrelated prompt responsibilities were absorbed

If all requirements pass, report exactly:

```text
PROMPT 12: IMPLEMENTED — READY FOR BEHAVIORAL VERIFICATION
```

Do not begin Prompt 13 until the separate Prompt 12 behavioral/integration verification passes.

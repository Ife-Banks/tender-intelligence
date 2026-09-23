# Prompt 11 — Test-Mode Email & Notification Layer

> Paste `prompts/00-master-context.md` first.

## Objective

Implement the **notification/email delivery layer** that consumes completed tender verdict results and prepares/delivers notifications under **Test Mode**.

The notification layer must be production-structured but development-safe.

The central safety requirement is:

> **While Test Mode is ON, no tender notification may be delivered to a business recipient.**

All tender notifications must be routed exclusively to the configured development/test recipient list.

This prompt implements the notification infrastructure and behavior required for later production use, but **does not authorize business-recipient sending or Test Mode deactivation**.

---

# 1. Read First

Read these completely before changing code:

### Core

* `PROJECT_RULES.md`
* `source-docs/tender-intelligence-spec-v1.1.md`

### Specifications

* `docs/08-email-notification-spec.md` — full §8.1–§8.17
* `docs/04-pipeline-spec.md`

  * §4.2 stages
  * §4.4 retry
  * §4.7 idempotency
  * §4.9 alerting
  * §4.14 update emails
* `docs/03-data-model.md`
* `docs/10-security-spec.md`
* `docs/12-deployment.md`
* `docs/13-open-decisions.md`

### AI contracts

Read:

* `prompts/13-ai-triage.md`
* `prompts/14-ai-verdict-engine.md`

These define the outputs consumed by the notification layer.

### Existing implementation

Inspect:

* `implementation/00-current-state.md`
* `implementation/01-decisions.md`
* `implementation/02-known-issues.md`
* `implementation/03-next-task.md`

Also inspect the actual code and tests for Prompts 04–10.

Do not assume the specifications and current implementation are identical. Preserve established contracts where they already exist and document any discrepancy rather than silently inventing behavior.

---

# 2. Ownership Boundary

Prompt 11 owns:

* notification preparation
* recipient routing
* provider abstraction
* provider selection/failover
* delivery attempts
* circuit breaker
* attachment planning
* notification templates
* Test Mode enforcement
* notification idempotency
* durable notification outbox
* update notification behavior

Prompt 11 does **not** own:

### Prompt 13

AI triage.

### Prompt 14

AI applicability verdict generation.

### Prompt 12

Deep audit/timeline and alert-event persistence.

### Prompt 15/16

Admin API/UI.

### Prompt 17

General security hardening.

### Prompt 18/19

Final QA/code review.

Do not duplicate these responsibilities.

---

# 3. Notification Input Contract

The notification layer must consume an already-produced tender decision/verdict.

It must not:

* call the LLM directly
* perform AI triage
* calculate applicability
* reinterpret the verdict
* create a new AI decision

The conceptual input is:

```text id="d2r4ya"
Tender
  +
Tender documents / bundle metadata
  +
AI verdict
  +
Evidence
  +
Confidence
  +
Incomplete-input state
  +
Recipient configuration
  +
Notification configuration
```

The exact domain types must follow the existing project contracts.

If Prompt 13/14 are not yet implemented, create the smallest clean interface required to consume their eventual output.

Do not implement fake AI behavior merely to make Prompt 11 appear complete.

---

# 4. MailProvider Abstraction

Implement the provider abstraction defined by `docs/08-email-notification-spec.md §8.2`.

The abstraction must support provider-independent operations.

At minimum capture:

* provider type
* encrypted/write-only credentials
* from address
* from name
* reply-to
* priority
* active/inactive state
* capabilities

Capabilities must include:

```text id="9ryz2q"
max_attachments
max_attachment_mb
max_message_mb
daily_limit
rate_limit_per_min
needs_verified_domain
```

Do not expose credentials through:

* logs
* API responses
* exceptions
* debug output
* test reports

Credentials must follow the project's security requirements.

---

# 5. First Provider Adapter

Implement the first provider adapter specified by the project documentation.

If the specification identifies the provider as `Sendlib`, implement that adapter according to the documented contract.

If the repository uses a different established provider name, follow the repository/specification rather than inventing a new provider.

Provider-specific code must remain inside the adapter.

The rest of the notification system must depend on the `MailProvider` abstraction.

Providers 2 and 3 remain TBD where `docs/13-open-decisions.md` says they are unresolved.

Do not invent additional providers.

---

# 6. Provider Chain

Implement provider selection/failover according to `docs/08-email-notification-spec.md §8.3`.

The provider chain must:

1. consider active providers
2. respect provider priority
3. check provider capability constraints
4. attempt delivery through the appropriate provider
5. retry transient failures according to policy
6. skip permanent failures where appropriate
7. continue to the next eligible provider
8. record every attempt
9. identify the provider that ultimately delivered the notification

Do not hide failed attempts.

---

# 7. Retry Classification

Distinguish:

### Transient failures

Examples:

* timeout
* temporary network failure
* HTTP 429
* HTTP 5xx where specified as retryable

These should use bounded retry with exponential backoff.

### Permanent failures

Examples:

* invalid credentials
* invalid recipient
* provider configuration error
* unsupported request

These should not be retried indefinitely.

Follow the exact classifications defined by `docs/08`.

Do not invent arbitrary retry behavior.

---

# 8. NotificationAttempt

Every provider attempt must be recorded through the project's `NotificationAttempt` model/entity or existing equivalent.

Capture appropriate information such as:

* notification identity
* provider
* attempt number
* timestamp
* outcome
* error classification/code
* correlation context
* safe provider response metadata

Never store secrets.

Never store unnecessary sensitive provider payloads.

---

# 9. Circuit Breaker

Implement the documented circuit-breaker behavior.

The circuit breaker must support:

```text id="umw4cn"
CLOSED
   ↓ repeated qualifying failures
OPEN
   ↓ cooldown
HALF-OPEN / recovery test
   ↓ success
CLOSED
```

Use the project's specified states if different.

Verify:

* threshold is configurable where specified
* cooldown is respected
* calls are blocked while the circuit is open
* recovery is attempted after cooldown
* successful recovery closes the circuit
* failure during recovery keeps the provider unavailable
* the event is observable through the appropriate alert hook

Do not implement the full alert-delivery system here.

---

# 10. Whole-Chain Failure

If no eligible provider can deliver a notification:

```text id="0q6bda"
provider chain unavailable
        ↓
durable pending_retry outbox entry
        ↓
notification remains recoverable
```

The notification must not simply disappear.

The persisted fallback signal must follow the specification, including the required red-banner operational signal where applicable.

The outbox must survive:

* process restart
* worker restart
* provider outage

Do not rely solely on in-memory queues.

---

# 11. Test Mode — Mandatory Safety Boundary

Test Mode must remain **ON by default**.

While Test Mode is ON:

```text id="m1q9k7"
Business recipient configuration
        ↓
Test Mode routing
        ↓
Development/Test recipient list
        ↓
[TEST] notification
```

There must be no path by which a normal business recipient accidentally receives a tender notification while Test Mode is enabled.

Do not rely on the UI to enforce this.

The safety check must exist in the notification/delivery layer itself.

---

# 12. Test Recipient Configuration

Implement the development/test recipient list according to §8.4–§8.5.

Support the specified recipient properties:

```text id="nd5w4q"
alert_types[]
min_severity
active
```

The first development recipient must be seedable from the configured environment variable so that the notification system can operate before the admin application is configured.

Recipients must come from configuration.

Do not hard-code email addresses into business logic or tests except where a fixture explicitly requires a test value.

---

# 13. Last Active Development Recipient Protection

The system must refuse to deactivate or delete the final active development/test recipient.

Verify both:

```text id="ld4mp8"
deactivate last active recipient → rejected
delete last active recipient → rejected
```

The system may permit:

```text id="u3q2lk"
add another active recipient
→ then deactivate/delete previous recipient
```

if consistent with the specification.

The safety invariant is:

> Test Mode must never be left without an active development recipient through an ordinary configuration operation.

---

# 14. Test Mode Subject

Every Test Mode tender notification must contain:

```text id="r8kz9x"
[TEST]
```

as the required subject prefix defined by §8.

Verify that the prefix cannot be omitted accidentally.

Do not duplicate the prefix if the notification is retried.

---

# 15. Recipient Routing Tests

Verify:

### Test Mode ON

Business recipients are ignored for delivery.

Only development/test recipients receive the notification.

### Test Mode OFF

Do not use production/business delivery as part of this prompt unless the project explicitly provides a safe isolated test harness.

The go-live authorization remains outside Prompt 11.

Do not disable Test Mode simply to prove the provider works.

---

# 16. Attachment Planner

Implement §8.8.

The planner must calculate the complete intended attachment set.

Then evaluate providers against their capabilities.

Preferred behavior:

```text id="j6hl0a"
whole attachment set fits provider 1
        ↓
use provider 1
```

If the complete set does not fit:

```text id="5q6jlu"
find appropriate provider
        ↓
whole set fits?
     ↙       ↘
   yes        no
   ↓           ↓
attach all   attach what fits
             +
             secure expiring links
```

If nothing fits:

```text id="b9b2gy"
all documents → secure expiring links
```

Record the exact result separately:

```text id="t5o4kp"
attachments[]
links[]
```

Do not simply append links to the email without recording what happened.

---

# 17. Attachment Limits

Respect:

* maximum attachment count
* maximum attachment size
* maximum message size
* provider capabilities
* configured limits
* email encoding overhead

Document the expected base64/message-size inflation as specified by the project.

Do not assume raw file size equals encoded email size.

The planner must evaluate the final message payload against the relevant provider limits.

---

# 18. Secure Expiring Links

Where documents cannot be attached, generate secure expiring links according to the existing architecture.

Default expiry:

```text id="0j0zqe"
14 days
```

unless overridden by configuration.

Do not expose:

* storage paths
* internal filesystem paths
* unrestricted document URLs
* permanent public URLs

Links must follow the project's access-control/security model.

---

# 19. Notification Template

Implement the exact template structure from §8.15.

The notification must contain the required sections:

1. Background
2. Requirements
3. Exact deadline date/time/timezone
4. Why We Can / Cannot Apply
5. Final verdict:

   * `APPLY`
   * `DO NOT APPLY`
   * `APPLY WITH CONDITIONS`
6. Confidence
7. Notes/footer

The footer must identify relevant document state:

* documents attached
* documents linked with expiry
* failed documents
* `incomplete_inputs`

If the tender is marked urgent, include the required URGENT marker.

In Test Mode, include `[TEST]` in the subject.

---

# 20. Plain Text and HTML

Every notification must support:

* plain text
* HTML

The substantive information must remain available in plain text.

Do not make HTML rendering the only usable representation.

Create deterministic template fixtures so the output can be compared in tests.

---

# 21. Deadline Handling

The notification must display:

* deadline date
* deadline time
* original/source timezone where required

Do not silently convert the deadline into an ambiguous local time.

Follow the established deadline/timezone model from the pipeline specification.

---

# 22. Verdict Integrity

The notification must reproduce the verdict produced by the AI verdict stage.

It must not:

* change `APPLY` to another verdict
* infer a different verdict
* omit required evidence
* invent evidence
* state that a company lacks something merely because evidence is absent

If `incomplete_inputs=true`, reflect that state in the notification footer/notes according to §8.15.

---

# 23. Recipient Model

Implement the business tender-recipient model defined by the specification.

Support the required fields:

```text id="5w8x5b"
delivery
recipient_scope
receives
active
```

Recipients must come from configuration.

Do not hard-code production/business recipients.

While Test Mode is enabled, the business-recipient model may exist and be validated, but delivery must still route exclusively to development/test recipients.

---

# 24. Notification Idempotency

Implement §8.12.

The deduplication key is:

```text id="wq3x4j"
tender
+
verdict
+
recipient-set hash
```

Store the idempotency key according to the project's data model.

Also include it in the outbound email header as specified.

The same tender/verdict/recipient set must not generate repeated notifications on normal reruns.

Test:

```text id="y0xj8u"
run notification twice
        ↓
exactly one delivery
```

---

# 25. Provider Timeout / Possible Duplicate

If a provider times out after the request may have reached the provider, the system cannot always know whether delivery occurred.

Follow the specified behavior:

```text id="u4g7zi"
provider timeout
     ↓
possible_duplicate = true
```

Do not automatically assume delivery definitely failed.

Do not automatically send unlimited duplicates.

Test the ambiguous outcome path explicitly.

---

# 26. Update Emails

Implement §8.14 and §4.14.

Update notifications must:

* have the appropriate lower priority
* identify relevant changes
* include new/changed documents where required
* use the current deadline
* reuse the existing verdict when a material re-evaluation is not required
* trigger verdict re-evaluation only when the specification says the change is material

Do not duplicate the deduplication/material-change logic owned by Prompt 05.

Prompt 11 should consume the existing change classification.

---

# 27. Notification State / Outbox

Implement the durable notification lifecycle defined by the specification.

At minimum distinguish appropriate states such as:

```text id="z4zvuo"
PENDING
SENDING
SENT
FAILED
PENDING_RETRY
POSSIBLE_DUPLICATE
```

Use the project's actual enum/model if already defined.

State transitions must be durable.

A process restart must not erase a pending notification.

---

# 28. Concurrency

Verify that two workers cannot accidentally send the same idempotent notification twice under normal concurrent execution.

Use the existing database transaction/uniqueness mechanisms where appropriate.

Do not rely only on application-level:

```text
if not sent:
    send()
```

because two concurrent workers may both observe `not sent`.

Test concurrent notification attempts.

---

# 29. Provider Limits and Rate Limiting

Respect:

* provider daily limits
* provider rate limits
* maximum attachments
* maximum message size
* provider-specific restrictions

Do not hammer a provider after rate limiting.

Use the documented backoff behavior for 429/rate-limit responses.

---

# 30. Security

Follow `docs/10-security-spec.md`.

Never log:

* SMTP/API credentials
* encryption keys
* access tokens
* recipient secrets
* private storage URLs
* document contents unnecessarily

Do not expose encrypted credentials through read APIs.

Credential writes must be write-only where specified.

Test secret redaction.

---

# 31. No Business Go-Live

Do not implement a production switch that can casually disable Test Mode.

The actual go-live process belongs to the later deployment/release gate.

Prompt 11 must leave:

```text id="ppz3t4"
Test Mode = ON
```

as the safe default.

If configuration technically supports Test Mode OFF, ensure the notification layer still has the safeguards required by the specification and do not exercise real business delivery during testing.

---

# 32. Tests

Implement behavioral/integration tests for all major requirements.

### Provider

* provider adapter against a mock HTTP service
* successful delivery
* timeout
* HTTP 429
* HTTP 5xx
* permanent provider error
* invalid credentials
* provider fallback

### Retry

* transient retry
* exponential backoff
* maximum retry count
* no infinite retry

### Circuit breaker

* threshold reached
* circuit opens
* cooldown
* recovery attempt
* successful recovery
* failed recovery

### Whole chain failure

* all providers unavailable
* durable `pending_retry`
* restart
* notification remains recoverable

### Test Mode

* Test Mode ON
* business recipients configured
* only development/test recipients receive notification
* `[TEST]` subject prefix
* no business recipient delivery path

### Recipient protection

* cannot deactivate final active development recipient
* cannot delete final active development recipient

### Attachment planner

Test all three:

1. whole set fits
2. partial fit
3. nothing fits

Verify:

```text id="x8fj6q"
attachments[]
links[]
```

exactly.

### Template

Verify:

* new tender
* update tender
* APPLY
* DO NOT APPLY
* APPLY WITH CONDITIONS
* confidence
* deadline/timezone
* evidence
* incomplete inputs
* failed documents
* attached documents
* expiring links
* URGENT
* `[TEST]`

### Idempotency

* repeated same notification
* concurrent attempts
* different verdict
* different recipient set
* changed material update

### Possible duplicate

* provider timeout
* `possible_duplicate=true`
* no uncontrolled resend

### Security

* credentials never logged
* secrets never returned
* private storage information not leaked

---

# 33. Integration Test

At least one integration test must exercise:

```text id="kn2a2k"
Tender
 ↓
existing verdict artifact
 ↓
notification preparation
 ↓
recipient routing
 ↓
attachment planning
 ↓
template rendering
 ↓
idempotency
 ↓
provider adapter
 ↓
mock provider
 ↓
NotificationAttempt
 ↓
final notification state
```

Do not mock every layer.

The integration test must prove the actual notification pipeline works as a unit.

Use a mock provider/server so no real business email is sent.

---

# 34. Regression Protection

Run existing regression tests for Prompts 04–10.

Verify that Prompt 11 has not altered:

* tender discovery
* deduplication
* persistence
* document acquisition
* document processing
* pipeline orchestration
* RunHistory
* correlation behavior

Prompt 11 should consume those outputs rather than modifying their semantics.

---

# 35. Report

After implementation, report:

1. Files changed.
2. Notification architecture.
3. MailProvider interface.
4. First provider adapter.
5. Provider failover behavior.
6. Retry/backoff policy.
7. Circuit-breaker implementation.
8. Durable outbox behavior.
9. Test Mode enforcement.
10. Development-recipient protection.
11. Attachment planner behavior.
12. Secure-link behavior.
13. Template implementation.
14. Idempotency implementation.
15. Possible-duplicate handling.
16. Update-email behavior.
17. Security controls.
18. Integration tests.
19. Regression tests.
20. Exact test commands and results.
21. Known limitations.
22. TODOs/open decisions.

Do not claim real provider delivery unless it was actually tested against an approved test endpoint.

Do not claim business-recipient delivery has been validated.

---

# 36. Final Safety Gate

Before declaring Prompt 11 complete, verify all of the following:

* Test Mode defaults to ON.
* Business recipients cannot receive tender emails while Test Mode is ON.
* Development/test recipients are configurable.
* The last active development recipient cannot be removed.
* Provider credentials are protected.
* Provider retries are bounded.
* Permanent errors fail over correctly.
* Circuit breaker works.
* Whole-chain failure creates durable `pending_retry`.
* Notification attempts are recorded.
* Attachment planning respects provider limits.
* Expiring links work according to the project's security model.
* Templates match §8.15.
* Idempotency prevents duplicate notifications.
* Possible provider duplicates are flagged.
* Update emails follow §8.14.
* Concurrent delivery attempts are safe.
* No AI logic was implemented here.
* No business go-live was enabled.
* Prompt 04–10 regressions pass.

If all requirements are genuinely implemented and verified, end with:

```text id="1b5v7w"
PROMPT 11: VERIFIED — READY FOR NEXT PROMPT
```

If material defects remain:

```text id="4r8k2c"
PROMPT 11: NEEDS FIXES
```

Do not begin Prompt 12, 13, 14, or any later prompt automatically.

# Prompt 15 — Admin API

> Paste `prompts/00-master-context.md` first.

## Objective

Implement the **Admin API**: the authenticated read/write control surface that the Admin UI will consume in the next prompt.

The Admin API must be a **thin API layer over the existing shared database and existing domain/service boundaries**.

It must not become a second implementation of the worker, crawler, document processor, AI engine, notification engine, audit system, or configuration logic.

Before writing code, inspect the actual repository and existing implementations from Prompts 04–14 and reuse their authoritative services/repositories/contracts.

---

# 1. READ THESE FIRST

Read:

### Project rules

* `PROJECT_RULES.md`

### Admin

* `docs/09-admin-app-spec.md` — full document

### Data model

* `docs/03-data-model.md`

### Pipeline

* `docs/04-pipeline-spec.md`

### AI

* `docs/07-ai-verdict-spec.md`

### Email

* `docs/08-email-notification-spec.md`

### Security

* `docs/10-security-spec.md`

### Open decisions

* `docs/13-open-decisions.md`

Pay particular attention to:

* O11 — admin users/authentication
* O3/O4/O9/O12

Do **not** invent decisions for unresolved open decisions.

Also inspect the actual implementation and tests from:

* Prompt 10 — worker/orchestration
* Prompt 11 — notification/email
* Prompt 12 — audit/observability
* Prompt 12.1 — deadline resolution
* Prompt 13 — triage
* Prompt 14 — Stage B verdict

---

# 2. ADMIN API OWNERSHIP

Prompt 15 owns:

* authentication/authorization boundary exposed by the API
* Admin/Viewer role enforcement
* API request validation
* API response contracts
* resource CRUD/read endpoints
* configuration reads/writes
* audit invocation for mutating operations
* safe secret write-only behavior
* dry-run endpoint orchestration through existing source interfaces
* health/timeline query surfaces
* test-connection/test-email API boundaries
* stable API contracts for the future Admin UI

Prompt 15 does **not** own:

* source crawling implementation
* source adapter implementation
* tender deduplication
* tender persistence semantics
* document acquisition
* document processing/OCR
* Stage A triage
* Stage B verdict
* deadline resolution
* email retry/failover
* attachment planning
* Alert Manager internals
* audit-event persistence internals where Prompt 12 already owns them
* actual authentication-provider/business-user decisions from O11
* Admin UI
* RAG/vector search

If an existing service already owns a behavior, the API must call that service rather than reimplementing it.

---

# 3. FIRST TASK: INSPECT THE EXISTING ARCHITECTURE

Before implementation, identify:

1. API framework already used by the project
2. repository/service layer
3. configuration storage
4. Settings model
5. source registry/configuration model
6. LLM profile/provider models
7. role-assignment model
8. mail provider/configuration model
9. recipient model
10. Knowledge Base models/storage
11. audit/config-change service
12. Alert Manager
13. timeline query service
14. health/run-history query service
15. existing authentication assumptions

Produce a short implementation mapping before changing code.

Do not introduce a second ORM, database abstraction, configuration store, or service layer without a documented reason.

---

# 4. AUTHENTICATION AND ROLE MODEL

O11 is unresolved.

Therefore:

* do not choose a production authentication provider
* do not invent a business-user list
* do not hard-code real administrators
* do not pretend O11 is resolved

Implement a **config-driven role model** with at least:

```text
Admin
Viewer
```

The API must have an explicit authenticated actor/context abstraction so the eventual authentication mechanism can be attached without redesigning endpoint authorization.

Every protected endpoint must receive an actor identity and role through that abstraction.

For local/test execution, provide a clearly isolated test authentication mechanism if needed.

Do not leave production endpoints accidentally unauthenticated.

---

# 5. AUTHORIZATION MATRIX

Define and enforce a central authorization policy.

At minimum:

| Resource/action       |  Admin |                                Viewer |
| --------------------- | -----: | ------------------------------------: |
| Health read           |    YES |                                   YES |
| Sources read          |    YES |                                   YES |
| Sources write         |    YES |                                    NO |
| Source dry-run        |    YES |                                    NO |
| Tenders read          |    YES |                                   YES |
| Tender timeline read  |    YES |                                   YES |
| KB read               | per §9 |                                per §9 |
| KB write              |    YES |                                    NO |
| LLM providers read    |    YES | restricted according to security spec |
| LLM provider write    |    YES |                                    NO |
| Role assignment write |    YES |                                    NO |
| Recipient read        |    YES |            according to security spec |
| Recipient write       |    YES |                                    NO |
| Mail provider read    |    YES | restricted according to security spec |
| Mail provider write   |    YES |                                    NO |
| Triage/urgency read   |    YES |                                   YES |
| Triage/urgency write  |    YES |                                    NO |
| Settings read         |    YES |            according to security spec |
| Settings write        |    YES |                                    NO |
| Audit log read        |    YES |                       according to §9 |
| Test connection       |    YES |                                    NO |
| Send test email       |    YES |                                    NO |

Do not blindly copy this table if `docs/09` or `docs/10` specifies something different.

Where the specification is authoritative, the specification wins.

The important requirement is that authorization is:

* centralized
* tested
* enforced at the API layer
* not implemented ad hoc in individual handlers

---

# 6. API RESPONSE CONTRACTS

Define stable response schemas.

Do not expose ORM/database objects directly.

Every endpoint should have explicit request/response DTOs or equivalent schemas.

Responses must:

* have stable field names
* have documented nullability
* distinguish missing vs empty where meaningful
* avoid secrets
* avoid internal database implementation details
* expose IDs needed by the Admin UI
* expose timestamps consistently
* expose pagination metadata where lists can grow

Use the project's existing API conventions if already established.

Do not invent incompatible conventions.

---

# 7. HEALTH API

Implement the Health screen API defined by `docs/09`.

It must expose, at minimum where supported by the existing data:

* per-source health
* last successful run
* last failed run
* failure category
* 7-day counts
* 30-day counts
* new tender counts
* verdict counts
* notification failure counts
* provider-chain status
* stuck queue indication
* relevant alert/health status

Do not recrawl sources to calculate health.

Health is a **read surface over persisted state**.

Do not call external providers merely to populate ordinary Health reads.

If a metric cannot currently be calculated from persisted data, document it rather than inventing a value.

---

# 8. SOURCES API

Implement:

* list sources
* get source
* create source
* update source
* enable/disable source
* source configuration validation
* "Test this source"

### Important ownership rule

The Admin API must not implement scraping logic.

For:

```text
POST /sources/{id}/test
```

use the existing source adapter/registry abstraction.

The dry run must:

* fetch the source according to its configured adapter
* parse/discover candidates
* return the observed result
* expose useful parser/configuration errors
* generate correlation/run context where appropriate
* write **nothing** to seen-tender state
* write no normal tender persistence state
* send no email
* invoke no AI verdict
* not mutate production crawl state

The test endpoint must not secretly become a partial production pipeline.

Verify whether the existing Prompt 10 dry-run semantics can be reused.

---

# 9. SOURCE CONFIGURATION EXTENSIBILITY

Do not hard-code WAHO into the Admin API.

The API must expose the source registry/configuration model that allows the existing supported source types to be represented.

Inspect the source-extensibility audit completed before Prompt 13.

For each configured source, the API should represent the source type/configuration needed by its adapter.

Do not claim that arbitrary websites are configurable without code unless the existing source adapter architecture actually supports that source type.

If a source requires a new adapter class, the API must not pretend that configuration alone is sufficient.

---

# 10. TENDERS API

Implement tender browsing with:

* pagination
* filtering where specified by `docs/09`
* stable identifiers
* source
* external ID
* title
* status
* deadline/resolution state where available
* creation/update timestamps
* relevant verdict state

Do not perform new crawling from a tender list endpoint.

Do not run AI merely because a tender is viewed.

---

# 11. TENDER TIMELINE API

Implement:

```text
GET /tenders/{id}/timeline
```

or the exact route convention defined by the project.

Use the existing Prompt 12 timeline query/read model.

Do not reconstruct the timeline independently inside the Admin API.

The timeline should expose the persisted sequence including, where applicable:

```text
seen
→ documents discovered
→ documents acquired
→ extraction
→ triage
→ deadline resolution
→ verdict
→ notification
```

Each event should retain:

* timestamp
* stage
* status
* correlation ID
* relevant safe metadata
* failure information where applicable

Do not fabricate events merely because an endpoint expects them.

---

# 12. KNOWLEDGE BASE API

Implement the KB operations required by `docs/09`:

* current version
* version history
* upload
* pasted text
* supported file types:

  * `.docx`
  * `.pdf`
  * `.md`
  * `.txt`
* edit where supported
* version comparison/diff
* token-budget indicator

Reuse the existing KB/domain model.

Do not implement RAG/vector search.

Do not silently change KB version semantics.

### Viewer restriction

Enforce the security specification exactly.

If `docs/09` says viewers cannot see KB contents, then:

* Viewer may receive metadata only if allowed
* Viewer must not receive KB document content
* Viewer must not receive raw KB text
* Viewer must not receive sensitive KB-derived content through another endpoint

Test this through actual API responses.

### Upload safety

Validate:

* allowed file types
* file size limits
* malformed files
* safe filenames
* path traversal
* storage errors

Do not expose raw storage paths.

---

# 13. LLM PROVIDER API

Implement the provider/profile management required by `docs/09`:

* profiles CRUD
* role assignment
* active/inactive state
* model configuration
* capability metadata
* usage
* budget information
* provider connection test
* `approved_for_company_docs`

Do not make provider approval decisions automatically.

The Admin API merely exposes the configuration control.

---

# 14. APPROVED-FOR-COMPANY-DOCS TOGGLE

Changing:

```text
approved_for_company_docs
```

is a security-sensitive configuration mutation.

It must:

* require Admin role
* validate the new value
* persist it
* create an audit/config-change event
* identify actor
* identify entity
* record changed field
* never record provider secret values
* never expose secrets in the response

Do not allow Viewer access to mutate it.

Verify that Prompt 14's data-policy enforcement reads the same authoritative configuration.

---

# 15. LLM "TEST CONNECTION"

Implement the Admin API endpoint for:

```text
Test connection
```

The actual provider call must use the existing LLM provider abstraction.

The test should return only the documented safe information, such as:

* success/failure
* latency
* resolved model
* provider
* supported JSON capability where testable
* vision capability where testable
* safe error category/message

Do not return:

* API keys
* authorization headers
* raw provider secrets
* full prompts
* company KB
* sensitive company documents

Use a minimal one-line test prompt as defined by the specification.

Do not invoke the full verdict pipeline.

Do not create a business verdict.

Do not send company KB data unless the specification explicitly requires it for the capability test.

---

# 16. RECIPIENT API

Implement two recipient categories:

```text
tender recipients
dev alert recipients
```

Provide:

* list
* create
* update
* enable/disable
* delete where permitted

Reuse the existing Prompt 11 recipient semantics.

Critically enforce:

> The last active dev-alert recipient cannot be deactivated or deleted.

This must be enforced transactionally so two concurrent requests cannot remove the last active recipient.

Do not duplicate or weaken Prompt 11's safety rule.

---

# 17. MAIL PROVIDER API

Implement:

* provider list
* provider configuration
* chain ordering
* active/inactive
* capabilities
* breaker status
* test email endpoint

Reuse Prompt 11's provider abstraction.

Do not reimplement:

* retry logic
* exponential backoff
* failover
* circuit breaker
* attachment planning
* notification idempotency

The Admin API only exposes/controls those existing mechanisms.

---

# 18. "SEND TEST EMAIL"

The endpoint must be test-only.

It must not become a business notification path.

Verify:

* Test Mode is enforced
* recipient is an approved dev/test recipient according to the project configuration
* `[TEST]` subject behavior remains intact
* no business recipients are used
* no tender notification is generated
* provider chain is used through the existing notification service
* fake providers are used in tests
* secrets are never returned

If the specification says a successful test email closes/resets an open breaker, reuse the existing provider abstraction rather than modifying breaker state directly in the API.

---

# 19. TRIAGE & URGENCY API

Implement configuration read/write for:

* include keywords
* exclude keywords
* sectors
* regions
* minimum contract value
* relevance threshold
* urgency window

Do not invent business defaults.

Preserve the Prompt 13 semantics:

* unset remains unset
* no rule does not accidentally discard tenders
* Stage A remains the authoritative evaluator
* Admin API merely changes configuration

Validate values but do not decide business policy.

---

# 20. SETTINGS API

Implement the settings surface defined by `docs/09`.

At minimum:

* Test Mode
* alert thresholds
* retention settings

### Test Mode

Test Mode must default to ON according to the specification.

Turning Test Mode OFF is a security-sensitive mutation.

Require:

* Admin authorization
* explicit action
* audit record
* actor identity
* changed field
* timestamp

Do not allow a generic update endpoint to disable Test Mode without producing the required audit event.

Do not silently default to production mode if the setting is missing.

---

# 21. AUDIT LOG API

Expose the audit/configuration history defined by Prompt 12 and `docs/09`.

Do not create a second audit store.

Read from the authoritative existing audit model/service.

Audit entries should expose safe metadata such as:

* actor
* action
* entity type
* entity ID
* changed fields
* timestamp
* correlation ID where applicable

Never expose:

* API keys
* passwords
* tokens
* authorization headers
* private credentials
* secret values

Verify that changing a configuration after an audit event does not rewrite historical audit data.

---

# 22. SECRETS ARE WRITE-ONLY

This is mandatory.

For every secret-bearing resource:

### Write

Allow:

* initial secret
* rotation
* replacement

### Read

Never return the secret itself.

The response may expose only safe metadata such as:

```text
configured: true
```

and an optional last-four representation only if explicitly permitted by `docs/10`.

Never return:

```text
api_key
password
smtp_password
access_token
secret
authorization_header
private_key
```

even if the database model contains them.

Test this recursively:

* normal response
* detail response
* list response
* validation error
* exception
* audit response
* test-connection response
* test-email response
* debug response

---

# 23. AUDIT EVERY MUTATION

Every mutating Admin API operation must reach the existing audit/config-change mechanism.

At minimum:

* actor
* action
* resource/entity
* entity ID
* changed fields
* timestamp
* correlation ID where appropriate

Never record secret values.

Do not audit read-only GET operations unless the specification explicitly requires it.

Do not create duplicate audit records if the underlying domain service already creates the authoritative audit record.

Inspect existing service behavior before adding API-level audit calls.

---

# 24. READ/WRITE SPLIT

Follow `docs/09` §9.10.

Keep read endpoints separate from mutation paths.

Do not create generic:

```text
PATCH /anything
```

that accepts arbitrary fields and bypasses authorization/audit validation.

Mutations should have explicit schemas and allowed fields.

Reject unknown or privileged fields where appropriate.

---

# 25. WORKER INDEPENDENCE

The Admin API must not require the worker to be running.

For ordinary Admin API reads:

```text
worker unavailable
```

must not make the API unavailable.

Health should report worker/run state from persisted information.

Source dry-run is an explicit operation and may execute source adapter code, but must remain isolated from the worker's normal run loop.

Do not make the API call the worker process internally.

---

# 26. NO INLINE CRAWLING

The Admin API must not contain source-specific crawling logic.

Do not write:

```text
requests.get(...)
BeautifulSoup(...)
```

inside endpoint handlers.

Use:

```text
Admin API
→ source registry/service
→ SourceAdapter
```

for source testing.

The same principle applies to:

* LLM providers
* mail providers
* notification
* timeline
* health
* audit

Use authoritative existing services.

---

# 27. ERROR CONTRACT

Define stable API error responses.

Errors should distinguish at least:

* authentication failure
* authorization failure
* validation failure
* not found
* conflict
* dependency failure
* dry-run/source failure
* provider test failure
* protected-resource mutation
* last-dev-recipient violation

Do not leak:

* stack traces
* secrets
* database connection strings
* provider credentials
* raw internal exception payloads

Use the project's existing error format if one exists.

---

# 28. CONCURRENCY / PROTECTED MUTATIONS

Test concurrent mutations for security-sensitive resources.

At minimum:

### Last dev recipient

Two simultaneous deactivation/delete requests must not both succeed.

### Test Mode

Concurrent settings updates must preserve audit correctness.

### Provider approval

Concurrent updates must not create inconsistent state.

### Source enable/disable

Must use the authoritative configuration persistence mechanism.

Use database transactions/constraints where appropriate.

---

# 29. DATABASE / MIGRATION DISCIPLINE

Do not create duplicate models for existing entities.

If schema changes are required:

* use additive migrations
* preserve existing data
* do not drop production data
* do not silently rewrite existing records
* document migration impact

Inspect whether Prompt 12/14 already created the required models before adding anything.

---

# 30. TESTS

Tests must remain offline.

Use fake providers/adapters.

Do not call:

* real WAHO
* real UNGM
* real TenderDetail
* real All Business Africa
* real LLM provider
* real Sendlib/mail provider

from the automated test suite.

Implement at least:

### Authentication/authorization

* Admin allowed
* Viewer allowed where appropriate
* Viewer blocked from protected writes
* unauthenticated request rejected

### Sources

* CRUD
* validation
* enable/disable
* dry-run
* dry-run writes nothing
* dry-run sends no email
* source adapter failure returned safely

### Tenders

* pagination
* filtering
* timeline retrieval
* missing tender

### KB

* upload
* supported formats
* invalid format
* version creation
* version history
* diff
* token indicator
* Viewer restriction

### LLM

* profile CRUD
* role assignment
* provider test
* safe response
* approved_for_company_docs audit

### Recipients

* CRUD
* active/inactive
* last active dev recipient protection
* concurrent last-recipient mutation

### Mail

* provider configuration
* chain order
* breaker status
* test email
* Test Mode enforcement

### Triage

* configuration read/write
* unset values remain unset
* validation

### Settings

* Test Mode default ON
* Test Mode OFF requires audit
* Viewer cannot disable it

### Audit

* mutation creates audit entry
* secret values absent
* historical entries immutable

### Security

* secret omission from every response
* secret omission from errors
* secret omission from audit records
* authorization enforcement

---

# 31. INTEGRATION TEST

Create one offline end-to-end Admin API fixture covering:

```text
authenticated Admin
        ↓
create/configure source
        ↓
test source dry-run
        ↓
inspect tender
        ↓
inspect timeline
        ↓
configure triage
        ↓
configure LLM profile
        ↓
assign verdict role
        ↓
configure test recipient
        ↓
configure mail provider
        ↓
send test email through fake provider
        ↓
inspect audit records
```

Do not send a real email.

Do not call a real source.

Verify actual persisted state after each mutation.

---

# 32. SECURITY SWEEP

After implementation, search the API code for:

```text
password
secret
api_key
token
authorization
private_key
smtp
credential
```

Inspect every occurrence.

Also inspect response serializers/schemas to ensure secret fields cannot leak through nested objects.

Search for:

```text
model_dump()
dict()
__dict__
ORM serialization
```

and verify that database models are not being returned wholesale.

---

# 33. DOCUMENTATION / API CONTRACT

Document:

* endpoint list
* HTTP methods
* request schemas
* response schemas
* authorization requirements
* error responses
* pagination
* dry-run semantics
* secret behavior
* audit behavior

Use the project's existing API documentation mechanism.

Do not create documentation that claims functionality not actually implemented.

---

# 34. REQUIRED REPORT

Produce a report containing:

## Files changed

List every changed file.

## Endpoint inventory

For every endpoint:

```text
METHOD
PATH
ROLE
PURPOSE
READ/WRITE
AUDITED?
SECRET-BEARING?
```

## Specification mapping

Map endpoints to:

* `docs/09-admin-app-spec.md`
* `docs/03-data-model.md`
* `docs/08-email-notification-spec.md`
* `docs/10-security-spec.md`

## Existing service reuse

Document which existing services were reused for:

* source testing
* health
* timeline
* LLM test
* mail test
* audit
* configuration

## Authorization matrix

Show Admin/Viewer behavior.

## Secret review

Explain how secrets are prevented from responses/logs/audit.

## Tests

Report:

* test count
* passing
* failing
* integration tests
* security tests

## Known limitations

Include unresolved Open Decisions without inventing decisions.

---

# 35. IMPLEMENTATION GATE

Do not start Prompt 16.

Do not implement the Admin UI.

Do not implement unrelated worker/AI/email functionality.

Do not resolve O11 or other open business decisions.

After implementation and tests, finish with exactly one:

```text
PROMPT 15: IMPLEMENTED — READY FOR INDEPENDENT BEHAVIORAL VERIFICATION
```

or:

```text
PROMPT 15: NEEDS FIXES — DO NOT START INDEPENDENT VERIFICATION
```

The first line means only that the implementation is ready for a separate verification pass. It does **not** mean Prompt 15 has been independently verified.

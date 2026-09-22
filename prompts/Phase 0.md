# Phase 0 — Tender Intelligence Project Foundation

You are now acting as the implementation engineer for the Tender Intelligence project.

The documentation phase is complete.

Do NOT redesign the product.

Do NOT reinterpret the requirements.

Do NOT implement future phases.

Your job in this task is to implement **Phase 0 — Project Foundation** only.

---

## 1. READ THE SPECIFICATION FIRST

Before changing any code, read:

* `PROJECT_RULES.md`
* `README.md`
* `docs/00-project-brief.md`
* `docs/01-product-requirements.md`
* `docs/02-technical-architecture.md`
* `docs/03-data-model.md`
* `docs/04-pipeline-spec.md`
* `docs/10-security-spec.md`
* `docs/11-testing-strategy.md`
* `docs/12-deployment.md`
* `docs/13-open-decisions.md`
* `docs/14-acceptance-criteria.md`
* `prompts/00-master-context.md`
* `prompts/01-architecture.md`
* `prompts/02-database.md`
* `prompts/03-infrastructure.md`

Also inspect the existing repository before creating files.

---

# 2. YOUR ROLE FOR THIS TASK

You are implementing the technical foundation.

You are NOT implementing:

* WAHO crawling
* Tender scraping
* Document downloading
* OCR
* AI triage
* AI verdicts
* Email delivery
* Admin UI
* RAG/vector search
* Additional source adapters

Those belong to later phases.

If you discover that something is required for the foundation but is not sufficiently specified, document the issue rather than inventing a business decision.

---

# 3. IMPLEMENT THE APPLICATION FOUNDATION

Create the initial application structure based on the architecture documentation.

Use the technology stack already selected in the project documentation.

If the documentation does not definitively select a technology, use the simplest reasonable implementation and record the choice as a technical implementation decision. Do not invent business requirements.

The foundation should include the appropriate separation for:

* application/core configuration
* database
* models
* repositories/data access
* pipeline abstractions
* source adapter interface
* document processor interface
* LLM client interface
* mail provider interface
* logging
* security/secrets
* worker
* admin/API foundation if required by the architecture
* tests

Keep interfaces clean and dependency boundaries explicit.

---

# 4. CONFIGURATION

Implement centralized configuration.

Configuration must support environment-specific values without hardcoding secrets.

At minimum account for the configuration categories defined in the specification:

* database
* application environment
* Test Mode
* logging
* storage
* LLM configuration
* mail configuration
* scheduling
* security-related configuration

Secrets must come from secure configuration/environment mechanisms.

Do NOT commit secrets.

Do NOT print secrets in logs.

Do NOT return secrets through normal application responses.

---

# 5. DATABASE FOUNDATION

Implement the database foundation described in:

`docs/03-data-model.md`

Create the initial schema/migrations required by Phase 0.

Use migrations.

Do not create a mechanism that requires manually editing production databases.

Do not implement speculative tables merely because they might be useful later.

Where the documentation explicitly defines entities required for the foundation, implement them.

Where an entity belongs to a later phase, decide whether it should be deferred rather than prematurely implementing business logic around it.

---

# 6. LOGGING

Implement structured application logging.

Logs should support:

* timestamp
* severity
* component/module
* event
* correlation ID where applicable
* useful contextual identifiers

Never log:

* API keys
* passwords
* access tokens
* encrypted secrets
* sensitive credentials

Logging should make future pipeline debugging possible.

---

# 7. CORRELATION IDS

Implement the foundation for correlation IDs.

The architecture must allow a future tender-processing run to be traced across:

* worker
* source adapter
* document processing
* AI calls
* notifications
* errors

Do not implement the full tender timeline yet.

Implement the infrastructure required to support it.

---

# 8. TEST MODE

Implement the foundational Test Mode configuration.

Test Mode must default to ON according to the project specification.

The architecture must make it difficult for future code to accidentally send business notifications while Test Mode is enabled.

Do not bypass Test Mode.

Do not implement actual email sending in this phase.

---

# 9. PROVIDER INTERFACES

Create the foundational interfaces/abstractions for:

### SourceAdapter

The future interface must support source-specific implementations without putting WAHO-specific logic into the core pipeline.

### DocumentProcessor

The future interface must allow different document-processing implementations.

### LLMClient

The future interface must support:

* provider profile
* model
* structured output
* retries
* provider fallback

Do not implement actual AI calls yet unless absolutely required by the foundation.

### MailProvider

The future interface must support multiple providers and failover.

Do not implement actual email delivery yet.

---

# 10. ERROR HANDLING

Create consistent application-level error handling.

Errors should:

* be structured
* preserve useful context
* support correlation IDs
* avoid leaking secrets
* distinguish expected operational failures from programming errors

Do not swallow exceptions silently.

---

# 11. WORKER FOUNDATION

Create the initial headless worker entry point.

The worker should be capable of:

1. starting
2. loading configuration
3. initializing dependencies
4. creating a correlation ID
5. logging startup
6. running a placeholder pipeline/run
7. logging completion/failure
8. shutting down cleanly

Do not implement tender crawling yet.

The worker must not depend on the admin application being available.

---

# 12. ADMIN FOUNDATION

Only implement the minimum foundation required by the architecture.

Do NOT build the full admin UI.

If the architecture requires an API/application layer, create the basic application structure and health endpoint necessary for future phases.

The admin application must not become a dependency required for the worker to operate.

---

# 13. HEALTH CHECK

Implement a basic health mechanism appropriate to the selected architecture.

It should distinguish, where practical:

* application health
* database connectivity

Do not create an elaborate monitoring system yet.

---

# 14. TESTING

Create tests for the foundation.

At minimum test:

* configuration loading
* required configuration validation
* Test Mode default
* secret masking/non-logging behavior where practical
* database connectivity/configuration
* migrations
* correlation ID creation/propagation
* provider interface contracts
* worker startup/shutdown
* health endpoint if applicable

Tests must be deterministic.

Do not use real external APIs.

Do not use real LLM providers.

Do not send real emails.

---

# 15. LOCAL DEVELOPMENT

Create the minimum local development setup required by the selected architecture.

If Docker is specified/selected, provide appropriate local containers.

If PostgreSQL is the selected database, make local development able to run PostgreSQL without requiring a production service.

Do not introduce unnecessary infrastructure.

---

# 16. DOCUMENTATION

Update the README only where necessary to explain:

* how to install dependencies
* how to configure the local environment
* how to run migrations
* how to run tests
* how to start the worker
* how to start the development API if applicable

Do not create undocumented product behavior.

---

# 17. QUALITY RULES

Before implementation:

1. Inspect the repository.
2. Read the relevant docs.
3. Identify any ambiguity.
4. Produce a short implementation plan.

Then implement.

After implementation:

1. Run the test suite.
2. Run lint/type checks if configured.
3. Inspect the migration.
4. Inspect for secrets accidentally committed or logged.
5. Review the changed files against `PROJECT_RULES.md`.
6. Check that no later-phase functionality was accidentally implemented.

---

# 18. DO NOT DO THESE THINGS

Do NOT:

* build the complete application
* scrape WAHO
* call production LLMs
* send emails
* implement RAG
* add a vector database
* invent company information
* invent recipients
* invent API credentials
* invent production infrastructure
* bypass Test Mode
* weaken validation
* remove tests to make implementation easier
* modify the specification documents to justify implementation decisions
* perform unrelated refactoring

---

# 19. FINAL REPORT

When finished, report:

### Implemented

List the major foundation components implemented.

### Files changed

List the important files created or modified.

### Tests

List:

* tests run
* test results
* lint/type-check results if applicable

### Architecture decisions

List technical decisions made during implementation.

Clearly distinguish decisions that were required by the specification from implementation choices.

### Open issues

List anything that prevented complete implementation.

### Specification conflicts

Report any conflict discovered between the implementation and the documentation.

### Next recommended task

Do not implement it.

Identify the next phase that should be tackled after this task.

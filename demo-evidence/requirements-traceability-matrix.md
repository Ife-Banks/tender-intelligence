# Tender Intelligence v1.1 — Requirements-to-Implementation Traceability Audit

**Audit mode:** read-only repository/evidence audit; no product code changed; Prompt 17 not started.  
**Authority:** `source-docs/tender-intelligence-spec-v1.1.md` plus current decisions in `docs/13-open-decisions.md` and governing `PROJECT_RULES.md`. Where v1.1 labels a feature `[PROPOSED]`, `[DEFERRED]`, or leaves a business choice open, that disposition is separate from implementation status.  
**Baseline:** working-tree HEAD `16b654383a0f6633f8eeaea4bc0bbe9b098bc883`; checkout already contained extensive modified/untracked implementation files, so attribution to a specific prompt is not inferred from Git history.  
**Status rule:** `VERIFIED` requires identified behavioral/test evidence, not code presence alone. `IMPLEMENTED — UNVERIFIED` means a code path exists but the requested behavior has not been independently demonstrated. `PARTIAL` means evidence reveals incomplete coverage or a known limitation. `MISSING` means no implementation was found for an in-scope requirement. `BLOCKED BY OPEN DECISION` means behavior cannot responsibly be finalized without a decision/input. `NOT APPLICABLE` is used for explicitly out-of-scope/deferred functionality. `CONTRADICTED` means observed behavior violates the requirement.

## Executive result

The implementation is substantial and several areas have good automated and independent behavioral evidence (worker orchestration, persistence, acquisition, processing, notifications, Test Mode, Stage A triage, and selected Admin API behavior). The audit does **not** support the claim that every mandatory v1.1 requirement is verified. Known gaps include generic source extensibility, Stage A KB-derived defaults, the absence of independent Prompt 14 verification, incomplete Admin/Provider runtime behavior, business/provider decisions, and an unrelated WAHO deadline parser regression.

### Status counts (102 atomic trace rows; each requirement is counted once)

| Implementation status | Count |
|---|---:|
| VERIFIED | 51 |
| IMPLEMENTED — UNVERIFIED | 13 |
| PARTIAL | 27 |
| MISSING | 2 |
| BLOCKED BY OPEN DECISION | 2 |
| CONTRADICTED | 0 |
| NOT APPLICABLE | 7 |

These counts cover the 102 trace rows in sections A–E, across all dispositions. The separate open-decision and acceptance crosswalk rows in sections F–G are linked to, not added as extra requirements. The machine-readable result is generated from the section A–E trace rows.

## A. Architecture, goals, sources, and discovery

| ID | Spec | Atomic requirement | Disposition | Implementation status | Implementation evidence | Test/behavioral evidence | Gap / note |
|---|---|---|---|---|---|---|---|
| ARC-001 | §4.1, §10 | Core runs as a headless worker, separate from Admin UI | MANDATORY | VERIFIED | `src/tender_intelligence/worker/main.py`; `orchestrator/worker.py::Worker` | `tests/integration/test_worker.py`; worker tests passed in Prompt 16C audit | Admin starts separately; no browser state in worker. |
| ARC-002 | §4.1 | Admin API/UI and worker use shared DB and document storage | MANDATORY | IMPLEMENTED — UNVERIFIED | `admin/main.py::create_app`; `db/engine.py`; `storage/interface.py` | Admin/persistence tests use shared injected engine/storage | Current production deployment sharing not independently inspected. |
| ARC-003 | §4.1 | Worker reloads configuration at each run, without retaining stale settings across runs | MANDATORY | VERIFIED | `orchestrator/config.py::ConfigLoader`; `orchestrator/coordinator.py::RunCoordinator` | `tests/integration/test_config_loader.py`; `test_orchestrator_pipeline.py`; AC G5 | Passes code/test trace; no production long-lived multi-run soak test. |
| ARC-004 | §4.1 | Admin outage does not stop scheduled worker using saved configuration | MANDATORY | VERIFIED | Worker entry point and DB-backed `ConfigLoader`, independent of Admin app composition | `tests/integration/test_worker.py`; AC A2-related worker path | No separate live outage drill. |
| ARC-005 | §4.1, §5.11 | Admin source test/connection checks are dry-run/test-only, not inline production work | MANDATORY | VERIFIED | `admin/api.py` source/provider test routes; existing coordinator/service seams | `test_real_coordinator_source_dry_run_has_no_persisted_side_effects`; provider tests are mocked | Actual third-party calls were deliberately not made. |
| ARC-006 | §2, §5.8 | Common operational configuration changes take effect without code deployment | MANDATORY | PARTIAL | DB-backed Admin API configuration endpoints; worker `ConfigLoader` | `test_ui_payload_shapes_persist_config_and_audit_without_external_services` | Supported fields work in tests; generic source type/config-only extensibility and deployed reload are incomplete/unverified. |
| SRC-001 | §5.1 | Source records hold name, URLs, type, frequency, languages, auth, active state, parser config, optional recipient scope | MANDATORY | VERIFIED | `db/models/sources.py::Source`; source DTOs in `admin/api.py` | `tests/integration/test_admin_api_prompt15.py`; source persistence tests | Secrets are write-only/encrypted; source fields verified through API payload. |
| SRC-002 | §5.1 | Sources can be added, edited, disabled without deployment | MANDATORY | VERIFIED | `admin/api.py` source CRUD; `config/loader.py` | `test_source_crud_and_dry_run_have_no_pipeline_writes`; Prompt 16B integration persistence test | Only currently supported adapter types can actually run. |
| SRC-003 | §5.1 | Active source reachability/config validation warns rather than crashing the application | MANDATORY | PARTIAL | `orchestrator/registry.py`; source scheduler/worker error handling | Source failure and health tests | Startup validation of every active source's reachability on each reload is not evidenced; operational reachability occurs during crawl. |
| SRC-004 | §5.1 | Source abstraction isolates source-specific parsing and permits a new source of a supported type by configuration alone | MANDATORY | PARTIAL | `interfaces/source.py::SourceAdapter`; `orchestrator/registry.py::AdapterRegistry`; `sources/waho.py::WahoPaginatedAdapter` | `demo-evidence/source-extensibility-audit.md` | Audit found only WAHO-bound adapter, hard-coded WAHO detail routes and no generic paginated adapter; config-only claim is not established. |
| SRC-005 | §5.1 | Paginated HTML listing strategy | MANDATORY | PARTIAL | `sources/waho.py::WahoPaginatedAdapter` | `tests/unit/test_waho_discovery.py`; source extensibility audit | WAHO-specific implementation works; generic type strategy not demonstrated. |
| SRC-006 | §5.1 | Filtered/faceted HTML and configured query filters | MANDATORY | MISSING | No generic filtered/faceted adapter/config behavior found | No matching behavioral test found | All Business Africa example remains unimplemented. |
| SRC-007 | §5.1 | Search-form-driven listing strategy | MANDATORY | MISSING | No search-form/UNGM adapter found | No matching test found | UNGM example remains unimplemented. |
| SRC-008 | §5.1 | RSS/Atom and JSON API source types are easy to add as their own types | PROPOSED/FUTURE | NOT APPLICABLE | No RSS/Atom or JSON API adapters | No tests | Table calls these future sources; treat as extension path, not delivered v1 adapter requirement. |
| DISC-001 | §5.2 | Active sources run on configured schedules | MANDATORY | PARTIAL | `orchestrator/scheduler.py::SourceScheduler`; `orchestrator/worker.py::Worker` | scheduler/worker integration tests | Local schedule mechanism exists; no verified production scheduler process/deployment cadence. |
| DISC-002 | §5.2 | Crawling respects robots.txt, rate limits, User-Agent, and error backoff | MANDATORY | VERIFIED | `sources/polite.py::PoliteHttpClient`, `CrawlPolicy` | `tests/integration/test_source_extensibility.py`, `test_waho_discovery.py`; AC S4 | External live-site policy not independently tested in this audit. |
| DISC-003 | §5.2 | Persist seen tender identity per source and avoid reprocessing/re-notifying unchanged listings | MANDATORY | VERIFIED | `dedup/service.py::DedupService`; tender unique identity model | `tests/integration/test_dedup_service.py`; `tests/integration/test_orchestrator_pipeline.py`; AC W1/N4 | Provider ambiguous-timeout exception is separately recorded under mail requirements. |
| DISC-004 | §5.2 | Material updates (e.g., addendum/deadline) are distinct lower-priority events and can use update template | MANDATORY | PARTIAL | `dedup/classify.py`; coordinator update path; `mail/templates.py` update rendering | `tests/unit/test_dedup_classify.py` | Material update detection exists; end-to-end update notification/regeneration contract is not independently verified and update template is marked proposed. |
| DISC-005 | §5.2 | Source-level failure is logged and raises system alert; no silent watcher failure | MANDATORY | VERIFIED | `orchestrator/worker.py`; `orchestrator/alerts.py`; `audit/alert_manager.py` | `tests/integration/test_worker.py`, `test_alert_manager.py`; AC S1/S2 | Alert delivery itself is only safe with configured dev recipient/provider. |

## B. Documents and Knowledge Base

| ID | Spec | Atomic requirement | Disposition | Implementation status | Implementation evidence | Test/behavioral evidence | Gap / note |
|---|---|---|---|---|---|---|---|
| DOC-001 | §5.3 | Discover all documents on tender detail page, including ZIP contents recursively | MANDATORY | VERIFIED | `sources/waho.py`; `acquisition/service.py`; `acquisition/zip.py` | WAHO attachment and ZIP tests; `tests/integration/test_acquisition_service.py`; AC W2 | Scope is demonstrated for WAHO adapter and supported archives. |
| DOC-002 | §5.3 | Preserve original name/source URL and store durable local/cloud copy | MANDATORY | VERIFIED | `acquisition/service.py`; `storage/interface.py`; `storage/local.py` | acquisition persistence tests; AC W2 | Cloud-backed deployment not selected. |
| DOC-003 | §5.3 | Record type, size, language guess, checksum and avoid duplicate refetch | MANDATORY | VERIFIED | `db/models/documents.py`; `acquisition/checksums.py`; `processing/languages.py` | `test_acquisition_service.py`; `test_processing_languages.py`; AC W2/W3 | Language detection is best-effort and reports confidence/unknown. |
| DOC-004 | §5.3 | One document failure does not block other tender documents; flag missing inputs | MANDATORY | VERIFIED | `acquisition/service.py`; `processing/service.py`; coordinator partial outcomes | `TestFailureTolerance`; `test_one_failed_document_does_not_fail_the_run`; AC D3/D5/FH5 | Prompt 10 independent report documents per-file failure path. |
| DOC-005 | §5.4 | Native PDF extraction | MANDATORY | VERIFIED | `processing/pdf.py::extract_pdf` | `tests/unit/test_processing_pdf.py::TestNativeText` | Extraction suite passed. |
| DOC-006 | §5.4 | Scanned/image PDF OCR; vision model optional only if supported/tested | MANDATORY + optional proposal | PARTIAL | `processing/ocr.py::TesseractOcrEngine`; `processing/pdf.py`; LLM profile capability fields | scanned/mixed PDF tests; AC D2 | OCR path exists, but end-to-end scanned non-English WAHO content reflected in verdict is not independently demonstrated; vision path/provider testing remains open. |
| DOC-007 | §5.4 | DOCX extraction preserves paragraphs/tables/order | MANDATORY | VERIFIED | `processing/docx.py::extract_docx`; structured representation | `tests/unit/test_processing_docx.py`; AC D1 | Unit fixtures prove extraction structure, not all production files. |
| DOC-008 | §5.4 | Preserve/tag multilingual content or translate before AI | MANDATORY | PARTIAL | `processing/languages.py`; document bundle representation | language detection tests; WAHO EN/FR/PT parser fixtures | End-to-end verdict behavior across languages is unverified; translation implementation not found. |
| DOC-009 | §5.4 | Preserve table structure for tender requirements | MANDATORY | VERIFIED | `processing/representation.py`; PDF/DOCX table extraction | PDF table and DOCX table tests; AC D1 | WAHO report records extraction support. |
| DOC-010 | §5.4 | Persist reusable structured bundle and avoid repeat OCR/extraction | MANDATORY | VERIFIED | `processing/store.py`; deterministic extraction/bundle keys | `tests/unit/test_processing_store.py`; AC D4 | Version/config invalidation is tested. |
| KB-001 | §5.5, §7 | Editable/uploadable structured KB without deployment | MANDATORY | VERIFIED | `KnowledgeBaseVersion`; `admin/api.py` KB upload/read/diff routes; `storage` | `test_kb_available_without_login_and_recipient_last_active_guard`; Prompt 16B persistence test | Live KB production content not supplied; UI/API surface exists. |
| KB-002 | §5.5 | Each save creates immutable version, content hash and timestamp; verdict references used version | MANDATORY | PARTIAL | `db/models/knowledge.py`; KB Admin service/API; `db/models/verdicts.py` | KB upload/persist tests; verdict engine integration tests | Versioning exists; independent historical-version end-to-end audit is absent with Prompt 14 verification debt. |
| KB-003 | §5.5 | KB has documented sections including company facts, capabilities, projects, qualifications, partners, gaps and pursue/skip profile | MANDATORY | BLOCKED BY OPEN DECISION | KB schema stores versioned opaque content; Appendix A explicitly deferred | Prompt 13 audit found KB-derived triage defaults unavailable | OPEX has not supplied approved capability data/template; no business KB content may be invented. |
| KB-004 | §5.5 | Compare whole KB plus typical bundle against configurable share of context; show warning | MANDATORY | PARTIAL | KB token count and LLM profile context fields; KB Admin DTO includes token budget | Prompt 15 API tests; Admin UI integration test | Warning threshold defaults remain unsettled/config-driven; actual UI state and warning boundary not independently verified. |
| KB-005 | §5.5, §3, §14 K5 | v1 uses whole KB; no embeddings/vector/RAG; retrieval is phase 2 | MANDATORY (absence in v1) | VERIFIED | No vector/retrieval infrastructure in `src`; verdict context service uses versioned KB | Prompt 14 implementation review and repository search | Phase 2 retrieval expressly excluded. |

## C. AI triage and verdict

| ID | Spec | Atomic requirement | Disposition | Implementation status | Implementation evidence | Test/behavioral evidence | Gap / note |
|---|---|---|---|---|---|---|---|
| AI-001 | §5.6 | Stage A is before Stage B and filters irrelevant tenders before full assessment | MANDATORY | VERIFIED | `triage/service.py`; `orchestrator/coordinator.py` stage ordering | `tests/integration/test_orchestrator_pipeline.py`; Prompt 13 independent report | Stage B callback/handoff contract does not implement AI in triage. |
| AI-002 | §5.6 | Triage rules include/exclude keywords, sectors, regions, minimum value, threshold | MANDATORY | PARTIAL | `triage/service.py`; `Setting.triage_rules` | Prompt 13 verification matrix | Rule behavior tested; KB-derived defaults are not resolved (KB-003, O5–O7). |
| AI-003 | §5.6 | Unset targeting values remain open; no invented sectors/regions/value defaults | MANDATORY | VERIFIED | Triage config defaults are unset/open | Prompt 13 blank-default behavioral tests and code search | O5/O6/O7 still open. |
| AI-004 | §5.6 | Triage passes by default when no disqualifying rule matches | MANDATORY | VERIFIED | `triage/service.py` rule decision | Prompt 13 critical default-pass test | — |
| AI-005 | §5.6 | Triage failures are distinct from discard and are durable/visible | MANDATORY | VERIFIED | `TriageResult`; coordinator stage/timeline persistence | Prompt 13 exception/failure/timeline tests | — |
| AI-006 | §5.6 | Stage B only runs on triage PASS | MANDATORY | VERIFIED | coordinator callback/handoff gate | Prompt 13 stage handoff tests | Stage B real production integration remains separately unverified. |
| AI-007 | §5.6 | Stage B builds assessment from tender bundle, complete applicable KB version and assigned verdict role | MANDATORY | IMPLEMENTED — UNVERIFIED | `verdict/service.py`; `db/models/verdicts.py`, `llm.py`, `knowledge.py` | `tests/integration/test_verdict_engine.py` exists and full suite passed except unrelated parser test | Prompt 14 independent behavioral report is absent. |
| AI-008 | §5.6, §8.1 | Verdict includes background, requirements, deadline and requirement-by-requirement comparison | MANDATORY | IMPLEMENTED — UNVERIFIED | `verdict/service.py`; strict schema/prompt; `mail/templates.py` | verdict-engine tests; no independent report | Need independent evidence-chain, full response and persisted-state audit. |
| AI-009 | §5.6 | Verdict enum is APPLY / DO NOT APPLY / APPLY WITH CONDITIONS and confidence | MANDATORY | IMPLEMENTED — UNVERIFIED | verdict schema/model enum in `db/models/verdicts.py`, `verdict/service.py` | verdict-engine tests | Derivability from requirements has not been independently proved. |
| AI-010 | §5.6 quality rules | Every supported capability claim cites a real KB section or is marked unverified | PROPOSED (quality rule) | IMPLEMENTED — UNVERIFIED | verdict semantic validation in `verdict/service.py` | unit/integration verdict tests | No independent adversarial citation test report. |
| AI-011 | §5.6 quality rules | Unsupported negative capability claims use “no evidence on file” language | PROPOSED (quality rule) | IMPLEMENTED — UNVERIFIED | verdict prompt/semantic validation | verdict tests | Independent evidence-based challenge not completed. |
| AI-012 | §5.6 | Strict structured JSON/schema validation; invalid result retries exactly once then `verdict_failed` + alert | PROPOSED (quality rule) | IMPLEMENTED — UNVERIFIED | verdict schema/parser/validator; alert hook | `tests/integration/test_verdict_engine.py` | No separate independent Prompt 14 verification; confirm retry count and downstream failure contract. |
| AI-013 | §5.6 | `incomplete_inputs=true` propagates and is visible to the human | MANDATORY | IMPLEMENTED — UNVERIFIED | bundle, verdict model and formatter fields | verdict/notification tests | End-to-end incomplete-bundle-to-email behavior not independently audited. |
| AI-014 | §5.6 | Oversized bundle policy maps per-document summaries, reduces to verdict, records map/reduce | MANDATORY | IMPLEMENTED — UNVERIFIED | verdict context assembly and map/reduce path in `verdict/service.py` | verdict-engine tests | No independent oversized fixture/provenance audit. |
| AI-015 | §5.6 | Deadline is UTC plus original timezone; email renders Nigerian time and notice timezone | MANDATORY | PARTIAL | `deadline/model.py`, `deadline/service.py`, tender model; `mail/templates.py` | deadline unit/integration tests | Known French WAHO parser case fails; Prompt 12.1 authoritative boundary not fully independently verified. |
| AI-016 | §5.6 | Urgency derives from configurable deadline window, independent of verdict | MANDATORY | IMPLEMENTED — UNVERIFIED | `Setting.urgency_window_days`; verdict service | verdict tests exist | Boundary matrix not independently verified. |
| AI-017 | §5.9 | Primary/fallback role profiles honor approval policy before any KB-containing call | PROPOSED | IMPLEMENTED — UNVERIFIED | verdict service profile gate and `LLMProfile.approved_for_company_docs` | verdict tests exist | Exact payload-to-provider boundary and fallback policy require independent adversarial test. |
| AI-018 | §5.9.6 | Monthly budget warning at 80%, pause Stage B at 100%, triage continues/awaiting_budget | MANDATORY | PARTIAL | Settings and LLMCall usage/cost fields; verdict budget guard hook | tests exist in verdict engine | Production monthly accounting/alerts and blocked-state behavior not independently verified. |

## D. Notification, provider and Test Mode requirements

| ID | Spec | Atomic requirement | Disposition | Implementation status | Implementation evidence | Test/behavioral evidence | Gap / note |
|---|---|---|---|---|---|---|---|
| MAIL-001 | §5.7, §8.1 | One new-tender email for every triage-passing tender, including DO NOT APPLY verdict | MANDATORY | PARTIAL | `notifications/service.py::NotificationService`; coordinator notification path | notification service/orchestrator tests | No real end-to-end all-source delivery; provider settings not fully configured in environment. |
| MAIL-002 | §5.7 | Provider-aware attachment planner chooses provider fitting all files or uses secure links | MANDATORY | VERIFIED | `mail/planner.py`; provider capabilities; `notifications/service.py` | `tests/unit/test_planner.py`; mail link tests; AC N3 | Secure archive path is tested separately. |
| MAIL-003 | §5.7 | Every document is either attached or securely linked; exact attached/linked names logged | MANDATORY | PARTIAL | notification planner, archive link signer, NotificationLog snapshots | planner/link/notification tests | Full real tender→provider output and link access not independently demonstrated. |
| MAIL-004 | §5.7 | Links signed, expiring, configurable expiry; document names listed | MANDATORY | VERIFIED | `mail/links.py`; `notifications/archive.py`; settings | `tests/unit/test_mail_links.py`; `test_notification_security.py` | Production archive origin/storage choice remains open (O12). |
| MAIL-005 | §5.7, §8 | Emails have proper sender, plain text and HTML body | MANDATORY | VERIFIED | `mail/templates.py`; Sendlib adapter/provider configs | `tests/unit/test_mail_templates.py`; Sendlib adapter tests | Verified template/adaptor behavior, not sender-domain production deliverability. |
| MAIL-006 | §5.10.1 | Separate tender and dev-alert recipient lists with routing fields and validation | MANDATORY | VERIFIED | `db/models/recipients.py`; `notifications/admin.py`; `notifications/router.py`; Admin API | `tests/integration/test_seed_and_recipients.py`; Admin API tests | Business recipient data remains operator-supplied/open. |
| MAIL-007 | §5.10.1 | Prevent disabling/deleting last active dev recipient; seed first from env | MANDATORY | VERIFIED | `notifications/recipients.py::RecipientGuard`; `config/seed.py` | recipient guard/seed tests; Prompt 15 API tests | Current env has no operator-supplied production recipient. |
| MAIL-008 | §5.10.1 | Store actual recipients at send time; later edits do not rewrite history | MANDATORY | VERIFIED | `NotificationLog` recipient snapshot; notification service | `tests/integration/test_notification_service.py`; model persistence tests | — |
| MAIL-009 | §5.10.2 | Two-to-three provider chain; transient retry/backoff then fallback | MANDATORY | PARTIAL | `mail/chain.py`; `notifications/service.py`; `MailProvider` configuration | `tests/unit/test_mail_chain.py` verifies retry/failover | Current environment has no verified live second provider; full real chain not validated. |
| MAIL-010 | §5.10.2 | Permanent provider errors skip to next provider and raise alert | MANDATORY | VERIFIED | provider chain classification + alert hook | `tests/unit/test_mail_chain.py`; alert tests | Live provider outage not invoked. |
| MAIL-011 | §5.10.2 | Every provider attempt persisted; final log identifies delivered provider | MANDATORY | VERIFIED | `NotificationAttempt`, `NotificationLog`; notifications service | notification integration tests; Prompt 12 report | — |
| MAIL-012 | §5.10.2 | Circuit breaker skips provider after configured failures, cooldown, alert; success closes | MANDATORY | VERIFIED | `mail/breaker.py`; notifications integration | `tests/unit/test_mail_breaker.py`, mail-chain breaker integration | — |
| MAIL-013 | §5.10.2 | Dedupe key and outbound header; ambiguous timeout can be `possible_duplicate` | MANDATORY | VERIFIED | notification persistence/idempotency and Sendlib adapter headers | `tests/unit/test_mail_chain.py::TestPossibleDuplicate`; service tests | Provider-specific true acceptance ambiguity cannot be tested without real service. |
| MAIL-014 | §5.10.2 | Whole chain down persists pending_retry, retries on schedule and health banner remains | MANDATORY | VERIFIED | durable notification outbox states; `notifications/banner.py` | notification service and banner tests; health Admin test | Scheduling production dispatcher remains deployment-dependent. |
| MAIL-015 | §5.10.3 | Sendlib adapter uses provider contract without logging credentials and supports response metadata | MANDATORY | IMPLEMENTED — UNVERIFIED | `mail/sendlib.py::SendlibProvider` | `tests/unit/test_sendlib_adapter.py`; Prompt 12 report | Real credentials/recipient not supplied; no live connectivity or verified sender. |
| MAIL-016 | §5.10.3 | Provider 2/3 and Sendlib tier/sender/domain choices | OPEN DECISION | BLOCKED BY OPEN DECISION | `docs/13-open-decisions.md` O3/O4; config supports provider chain | Sendlib report: no live provider configured | No second/third provider selected; Sendlib sender mailbox consent/tier remain undecided. |
| SAFE-001 | §5.12 | Test Mode default ON; all tender notifications route only to dev list and subject `[TEST]` | PROPOSED | VERIFIED | `notifications/test_mode.py::TestModePolicy`; DB `Setting` default; recipient router | `tests/unit/test_test_mode.py`; notification security and Admin API test-mail tests | Actual running Admin DB reported ON in prior 16C check. |
| SAFE-002 | §5.12 | Turning Test Mode OFF is explicit and audit-logged | PROPOSED | VERIFIED | Admin settings route + `ConfigChangeLog`; notification settings | `test_settings_write_is_available_without_login_and_test_mode_off_is_audited` | Production remains Test Mode ON; no toggle performed in this audit. |
| SAFE-003 | §5.12 | Test source/provider/email actions are test-only and cannot reach business recipients | PROPOSED | VERIFIED | Admin dry-run/test routes and notification policy | Prompt 15 API integration tests with fake services | No live provider interaction. |
| SAFE-004 | §5.9.4, §5.12 | Unapproved verdict profile receives placeholder/public-safe KB only | PROPOSED | IMPLEMENTED — UNVERIFIED | `verdict/service.py` data-policy check; profile approval column | verdict tests exist | Independent exact payload boundary verification outstanding. |

## E. Admin, audit, observability, data model, non-goals

| ID | Spec | Atomic requirement | Disposition | Implementation status | Implementation evidence | Test/behavioral evidence | Gap / note |
|---|---|---|---|---|---|---|---|
| ADM-001 | §5.11 | Health screen shows per-source last success/failure, 7/30 counts, provider chain and stuck queue | MANDATORY | PARTIAL | `admin/api.py::admin_health`; static Health renderer | health dashboard tests; live 200 response | Actual `/api/v1/health/dashboard` succeeds; other operational reads show 500 (F16C-01). |
| ADM-002 | §5.11 | Source screen list/add/edit/enable/disable/dry-run | MANDATORY | VERIFIED | Admin API source endpoints; static UI Sources screen | Admin API + UI-shaped integration tests; 16A route report | Generic adapter coverage remains SRC-004 gap. |
| ADM-003 | §5.11 | Tender list/detail with timeline by correlation ID | MANDATORY | PARTIAL | Admin tender/timeline API; `TimelineService`; UI screen | Prompt 15 timeline test; live `/api/v1/tenders` returns 500 | Live tender list unavailable in current runtime; timeline test itself passes isolated DB. |
| ADM-004 | §5.11 | KB screen versioning/diff/token budget | MANDATORY | PARTIAL | KB API; KnowledgeBaseVersion; UI KB screen | Prompt 15 KB and Prompt 16B integration tests | Full UI/runtime test and business KB update changing verdict not proven. |
| ADM-005 | §5.11 | LLM profile/role/test/approval/usage screens | MANDATORY | PARTIAL | API profile/role/usage routes; static LLM screen | mocked profile test and UI integration | No configured production factory; live `/llm/usage` currently 500; test connection may be unavailable without injected factory. |
| ADM-006 | §5.11 | Separate tender and dev recipient management | MANDATORY | VERIFIED | recipient API/admin service/UI | Admin API tests; UI test | Business recipient choices open. |
| ADM-007 | §5.11 | Mail provider chain/capabilities/test/breaker screen | MANDATORY | PARTIAL | Mail provider API and static UI; notification chain model | UI/API config tests; no real provider calls | Live test-provider and chain failover not configured. |
| ADM-008 | §5.11 | Triage & urgency configuration screen | MANDATORY | VERIFIED | API triage/settings; static screen; Setting model | Prompt 16B persistence integration; Prompt 13 tests | KB-derived targeting defaults unresolved. |
| ADM-009 | §5.11 | Settings screen includes Test Mode, alert thresholds, retention | MANDATORY | PARTIAL | `/settings` API; static Settings UI; Setting model | settings API test and live GET settings | Verify all fields and worker next-run behavior; some alert/business thresholds open. |
| ADM-010 | §5.11 | Audit screen shows every configuration change without secrets | MANDATORY | VERIFIED | ConfigChangeLog service/API; static Audit screen | API audit/redaction tests; no-login live `/audit` 200 | Human actor/roles explicitly deferred by O11; actor is service identity. |
| OBS-001 | §6.1 | Each tender and pipeline run has unique traceable correlation ID across all records | MANDATORY | VERIFIED | `core/correlation.py`; tender/run models; coordinator/timeline | timeline and orchestrator integration tests; AC G3/AU1 | Prompt13/Prompt10 reports demonstrate correlation propagation. |
| OBS-002 | §6.1 | Each stage stores status plus machine-readable error code, not only final result | MANDATORY | VERIFIED | `orchestrator/status.py`; run/timeline models | `tests/integration/test_orchestrator_pipeline.py`; docs/10 verification | — |
| OBS-003 | §6.1 | Structured logs include timestamp/correlation/source/stage/status/error category | MANDATORY | VERIFIED | `logging/structured.py`; worker/coordinator log calls | `tests/unit/test_logging.py`; AC G4 | A centralized query UI for all log classes is not provided. |
| OBS-004 | §6.1 | Run history has start/end/counts/errors/failed correlation IDs | MANDATORY | VERIFIED | `db/models/runs.py`; coordinator finalization | docs/10 verification; pipeline tests; AC G1 | — |
| OBS-005 | §5.10.4, §6.1 | Alerts are throttled, deduplicated, recoverable, and visible via dev list/health fallback | MANDATORY | PARTIAL | `audit/alert_manager.py`; alert delivery/banners | `tests/integration/test_alert_manager.py`; `test_worker.py` | Persisted alert and throttling logic exist; operational delivery requires actual dev recipient/provider (absent in Sendlib report). |
| OBS-006 | §6.1 | Run history/stage logs/verdicts retained for configured minimum | MANDATORY | PARTIAL | retention setting and data models | tests cover config persistence | Retention enforcement/job and minimum behavior not independently evidenced. |
| SEC-001 | §6.2 | All source/LLM/mail/OAuth secrets encrypted at rest with master key outside DB | MANDATORY | VERIFIED | `crypto/secrets.py`; source/LLM/mail encrypted columns | crypto and Admin API adversarial secret tests | Live secret values are absent; tests use synthetic sentinel. |
| SEC-002 | §6.2 | API/UI/logs/errors/audit never expose stored secret values | MANDATORY | VERIFIED | safe DTOs, error handlers, audit changes fields only | Admin API adversarial secret test and UI secret-safety tests | — |
| SEC-003 | §6.2 | Full prompts/KB/company contents are not logged; data recipients documented before go-live | MANDATORY | PARTIAL | LLMCall metadata-only model; security docs | security/logging tests and docs | Go-live third-party/owner security note is not present as a complete approved artifact. |
| SEC-004 | §5.11 / current O11 | Admin app requires authenticated Admin/Viewer roles | PROPOSED in source v1.1; superseded by current O11 decision | NOT APPLICABLE | `docs/13` O11; no auth module or route gate | Prompt 16C independent report: no-login UI/API verified | Internal/private network assumption remains; this does not authorize public exposure. |
| DATA-001 | §7 | Core entities (Source, Tender, Document, Verdict, KB version, LLM profile/role/call, mail provider/attempt, recipient, alert, run, settings, audit) have persistence mappings/migrations | MANDATORY | VERIFIED | `db/models/*`; migrations `0001`–`0012` | `tests/integration/test_migrations.py`; persistence tests | Reserved AdminUser remains unused; it is not active identity behavior. |
| DATA-002 | §7 | Operational rows retain verdict/provider/recipient/document/deadline provenance fields | MANDATORY | PARTIAL | `db/models/verdicts.py`, `documents.py`, `mail.py`, `deadline.py` | model and integration tests | Prompt14 historical/correlation/KB/profile metadata has not received independent behavioral verification. |
| NFR-001 | §6 | Crashed crawl preserves seen state and next scheduled run proceeds | MANDATORY | VERIFIED | transactional coordinator and dedup persistence | orchestrator crash/retry tests; AC G2/FH1 | — |
| NFR-002 | §6 | Reprocessing does not send duplicate notifications, except flagged ambiguous provider timeout case | MANDATORY | VERIFIED | dedupe/outbox, notification keys, provider chain | mail chain idempotency/possible duplicate tests; AC N4/N5 | Full external provider behavior remains inherently not verified. |
| NFR-003 | §6 | Daily digest/dashboard exposes checked/new/verdict/failure health | MANDATORY | PARTIAL | Admin health dashboard aggregation; `audit/health_digest.py` | health/dashboard tests | Actual digest delivery and live dashboard error cases incomplete. |
| NONGOAL-001 | §3 | No application submission automation | NON-GOAL | NOT APPLICABLE | No application submission component found | no submission tests | — |
| NONGOAL-002 | §3 | v1 has no Slack/Teams/SMS delivery; email only | NON-GOAL | NOT APPLICABLE | Email notification abstraction only | mail tests | Channel expansion is future work. |
| NONGOAL-003 | §3, §5.5 | No embeddings/vector/RAG retrieval in v1 | NON-GOAL | NOT APPLICABLE | None found | source search | Phase 2 only. |
| NONGOAL-004 | §3 | No multi-tenant shared-hosting feature in v1 | NON-GOAL | NOT APPLICABLE | Single tenant model/config | no multi-tenant tests | — |
| NONGOAL-005 | §9 | Specific suggested stack/tools are guidance, not mandatory requirements | NON-GOAL/GUIDANCE | NOT APPLICABLE | Python/FastAPI/SQLAlchemy/static JS selected | repository inspection | No compliance finding is made against non-mandatory suggested tools. |

## F. Open decisions and explicitly proposed/deferred requirements

| ID | Spec | Decision/input | Disposition | Implementation status | Evidence / impact |
|---|---|---|---|---|---|
| OPEN-001 | §12.2, O3 | Sender mailbox and consent for Sendlib relay | OPEN DECISION | BLOCKED BY OPEN DECISION | `demo-evidence/sendlib-config-report.md`: no operator sender/authorization supplied. |
| OPEN-002 | §12.2 | Sendlib Free or Pro tier | OPEN DECISION | BLOCKED BY OPEN DECISION | Determines file/recipient/rate capabilities; not chosen. |
| OPEN-003 | §12.2 | SPF/DKIM for future conventional provider | DEFERRED | NOT APPLICABLE | Required only when selected provider requires verified domain. |
| OPEN-004 | §12.2, O12 | Exact production model/provider credentials and approval for company KB | OPEN DECISION | BLOCKED BY OPEN DECISION | No production profile approval/credentials supplied; tests use mocks. |
| OPEN-005 | §12.2, Appendix A | Authoritative KB template and OPEX capability content | DEFERRED/OPEN | BLOCKED BY OPEN DECISION | Prompt13 says no KB-derived triage defaults until canonical KB content exists. |
| OPEN-006 | §12.3 | Tender recipient identities/routing by source/verdict/urgency | OPEN DECISION | BLOCKED BY OPEN DECISION | Recipient seam exists; business addresses/rules must be supplied by OPEX. |
| OPEN-007 | §12.3 | Whether business operations list receives selected system alerts | OPEN DECISION | BLOCKED BY OPEN DECISION | No assumed operations recipients. |
| OPEN-008 | §12.3 | Target sectors, regions, contract types, minimum values | OPEN DECISION | BLOCKED BY OPEN DECISION | O5/O6/O7 remain open; unset defaults pass through. |
| OPEN-009 | §12.3 | Paid source access/registration (e.g. TenderDetail) | OPEN DECISION | BLOCKED BY OPEN DECISION | No credentials/access decision. |
| OPEN-010 | §12.3 | Monthly AI spend budget | OPEN DECISION | BLOCKED BY OPEN DECISION | Guard seam exists; no business budget may be invented. |
| OPEN-011 | §12.3 | Document archive/storage physical deployment location | OPEN DECISION | BLOCKED BY OPEN DECISION | Local storage abstraction exists; production target unresolved. |
| OPEN-012 | §12.3 | Provider 2/3 selection and legal/ToS review per source | OPEN DECISION | BLOCKED BY OPEN DECISION | Provider chain abstraction exists; selections/legal approvals absent. |

## G. v1.1 acceptance-criteria crosswalk

The following acceptance criteria are separately cross-referenced to the atomic rows above. “Covered” means the criterion is represented in the register, not that it passes.

| v1.1 §11 criterion | Trace IDs | Current disposition |
|---|---|---|
| New tender on each active source triggers within a crawl cycle | SRC-002, DISC-001, DISC-003, MAIL-001 | PARTIAL; no every-active-source end-to-end/live delivery proof. |
| Email has all three sections plus links/attachments footer | AI-008, MAIL-002–005 | PARTIAL; formatter tests exist; complete pipeline output not independently verified. |
| Every document attached or reliably linked; report attached-vs-linked | DOC-001–004, MAIL-002–004 | PARTIAL; planners/links tested, no live end-to-end. |
| No duplicate notification except rare flagged timeout ambiguity | DISC-003, MAIL-013, NFR-002 | VERIFIED in deterministic provider tests. |
| Add/disable/reschedule source without deployment | SRC-002, DISC-001 | PARTIAL due generic source support and production scheduling proof. |
| KB version update demonstrably changes verdict | KB-002, AI-007 | IMPLEMENTED — UNVERIFIED; no independent Prompt14 verification. |
| Scanned non-English PDF content reflected in verdict | DOC-006, DOC-008, AI-008 | PARTIAL; component tests only, end-to-end not demonstrated. |
| Source outage alerts and throttles reminders | DISC-005, OBS-005 | PARTIAL; alert logic tested, live delivery/config absent. |
| Correlation ID reconstructs full journey under one minute | OBS-001–004, DATA-002 | PARTIAL; persisted timeline supported, full Prompt14 verdict/provider trace still unverified. |
| Health view shows last success/failure | ADM-001, NFR-003 | PARTIAL; dashboard exists, two live endpoints fail and provider config empty. |
| LLM profile change/test applies next call without redeploy | ADM-005, ARC-003 | PARTIAL; profile persistence tested, live provider test factory/configuration unavailable. |
| Unapproved provider cannot receive KB, including fallback | AI-017, SAFE-004 | IMPLEMENTED — UNVERIFIED. |
| Recipient editing, last dev recipient guard, send-time snapshot | MAIL-006–008 | VERIFIED in API/service tests. |
| Provider 1 failure uses provider 2 and logs failover | MAIL-009–011 | PARTIAL; deterministic chain tests pass, deployed provider 2 absent. |
| Provider limits cause working expiring links | MAIL-002–004 | VERIFIED at component level; end-to-end output remains partial. |
| Test Mode blocks business recipients and prefixes `[TEST]` | SAFE-001–003 | VERIFIED by mock-backed tests. |
| All-provider outage retries later and health banner shown | MAIL-014, OBS-005 | VERIFIED in persistence/banner tests; deployed scheduled recovery not exercised. |

## H. Prior verification debt and audit limitations

- Prompt 13 independent report: **NEEDS FIXES** — KB-derived pursue/skip defaults unavailable; known WAHO deadline test failure also recorded.
- Prompt 14: implementation report only; no independent behavioral-verification report is present. Prompt 14 requirements remain `IMPLEMENTED — UNVERIFIED` or `PARTIAL` unless a specific lower-level behavior is covered by tests.
- Prompt 15 independent report: **NEEDS FIXES** (historical gate); current Prompt 16C changed auth scope and tests, but the new live Admin runtime still returns 500 for tenders and LLM usage.
- Prompt 16A independent report: **NEEDS FIXES / verification incomplete** (tablet/mobile and browser-console evidence absent).
- Prompt 16B independent report: **NEEDS FIXES**; prior live-mode defects were recorded. Prompt 16C addresses auth scope, not all earlier UI integration verification debt.
- Prompt 16C independent report: **NEEDS FIXES**; see F16C-01/F16C-02 there.
- Full repository test run immediately preceding this audit: 618 passed, one known unrelated `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` failed.
- A full external-provider / production-email / production-LLM run was not performed; no real business email or production credential was used.
- The working tree was already modified before this audit. This audit added only traceability evidence artifacts.

## I. Gap register and correction order

| Gap | Requirement IDs | Gap | Suggested owner / next action |
|---|---|---|---|
| GAP-001 | SRC-004–007, DISC-001 | Source extensibility does not establish generic config-only support; filtered and search-driven examples are absent. | Source-adapter implementation owner; review `demo-evidence/source-extensibility-audit.md` before any new source prompt. |
| GAP-002 | KB-003, AI-002 | No canonical OPEX KB schema/content; triage KB-derived defaults cannot be loaded. | OPEX supplies approved KB content/template; do not invent targeting values. |
| GAP-003 | AI-007–018, DATA-002 | Prompt 14 has no independent behavioral gate covering evidence, deadlines, policies, retries, persistence and failure contracts. | Run independent Prompt 14 verification before declaring verdict requirements verified. |
| GAP-004 | MAIL-009, MAIL-015–016, OPEN-001–002, OPEN-012 | No confirmed sender/tier/second provider credentials; Sendlib not live-tested and chain deployment absent. | Operator/provider selection and safe Test Mode provider verification. |
| GAP-005 | DOC-006, DOC-008 | Scanned multilingual text has component coverage but not acceptance-level end-to-end verdict evidence. | Add deterministic and approved real-document verification; no production LLM without data approval. |
| GAP-006 | OBS-005–006, NFR-003 | Alert delivery, retention enforcement and daily digest require deployment/config proof. | Operational implementation and owner decisions for recipients/storage/retention. |
| GAP-007 | ADM-001, ADM-003, ADM-005 | Actual Admin `/api/v1/tenders` and `/api/v1/llm/usage` endpoints return generic 500 in current runtime; UI's 403 copy refers to deferred roles. | Diagnose live API/database/runtime before further Admin closure; 16C report records evidence. |
| GAP-008 | AI-015 | One French WAHO “date limite” fixture fails to parse; deadline layer still has known regression. | Deadline-resolution owner; fix independently and rerun affected/full suite. |
| GAP-009 | ADM-007, MAIL-003, MAIL-009 | End-to-end provider-capability selection, actual attached/linked payload, and deployed failover are not verified together. | Notification owner/operator; staged test with approved test providers only. |
| GAP-010 | SEC-003 | Go-live third-party data-sharing inventory with named owners is incomplete. | Security/go-live owner, before production. |

## Current gate

**REQUIREMENTS TRACEABILITY AUDIT: COMPLETE** (repository was audited; mandatory requirements are not all verified).  
Mandatory rows presently include verified, implemented-unverified, partial, missing, blocked and contradicted states; this is a gap inventory, not a release approval.

**Next action:** Begin correction planning from GAP-001 after preserving business-open decisions as blockers. Do not start Prompt 17 on the strength of this audit; first handle the higher-priority correctness/integration gaps and complete the outstanding Prompt 14 verification.

# 11 — Testing Strategy

> Source of truth: v1.1 (acceptance criteria §11, quality rules §5.6, failure behaviour §5.2/§5.9/§5.10, NFRs §6). This strategy maps test layers to concrete requirements. Tests are expected alongside meaningful implementation changes (PROJECT_RULES #13).

## 11.1 Test layers

### Unit tests
- Target: pure logic — parser config resolution, dedupe key computation, recipient routing, triage rule evaluation, verdict strict-schema validation, attachment-planner capability math, timezone/UTC conversions (WAT display), thumbnail-free formatting helpers.
- No network, no external services.

### Integration tests
- Adapter against fixtures (offline). Extraction pipeline against fixture files (native PDF / scanned PDF / DOCX / ZIP / multilingual). Mail dispatcher against a fake provider in provider-chain order. LLM client against a fake OpenAI-compatible server (JSON-mode, retries, fallback, data-policy refusal). Database round-trips for pipeline state.

### Source-adapter fixtures
- WAHO fixture pages: listing page(s), a second paginated page, detail page(s), attachment links, edge cases (missing deadline, no title, French/Portuguese content, scanned docs). Regenerate from live site before release; keep a stable fixture commit.

### Document-processing fixtures
- Native-text PDF, scanned PDF (image-only), mixed PDF, DOCX, ZIP containing mixed files, oversized bundle, unreadable/corrupt file, non-PDF binary linked as "PDF".

### AI schema-validation tests
- Valid JSON → accepted; missing field → retried once; wrong enum (`APPLY`, `DO NOT APPLY`, `APPLY WITH CONDITIONS` only) → retried once; invalid after retry → `verdict_failed` + alert path; citations present vs missing ("unverified" downgrade); "no evidence on file" phrasing enforced.

### Mock LLM tests
- Timeout → retry count then fallback; 429/5xx → same; fallback refusal when it is not `approved_for_company_docs` and KB content is at stake; per-call `LLMCall` record written.

### Mail-provider tests
- Provider chain ordering; transient retry/backoff; permanent error skips + alert; whole-chain-down → `pending_retry` + red banner; breaker open/cooldown/close via test email; attached-vs-linked correctness; recipient snapshot; `possible_duplicate` flag on ambiguous timeouts; dedupe header present.

### Failure/retry tests
- All scenarios in §11.3 must have at least one test that asserts correct status codes and correlation-ID continuity.

### Idempotency tests
- Re-running a crawl over identical listing fixtures: zero duplicate notifications; seen-tenders stable; `RunHistory` increments.

### Test Mode tests
- With Test Mode ON: no tender email reaches a business recipient; dev list receives it; `[TEST]` prefix present; a dry-run writes nothing to seen-tenders and emails no one; off-switch requires an audit-logged admin action.

### Admin dry-run tests
- "Test this source", "Test connection", "Send test email" produce no operational writes and no business sends.

### Security tests
- Secrets-blocking: no secret value in any log line; API responses omit secret values; secrets never in `ConfigChangeLog`; unapproved-provider refusal (primary & fallback); viewer role blocked from KB and secrets; signed links expire; master key not in repo.

### End-to-end tests
- WAHO fixture → detect new → fetch → extract → triage pass → verdict → formatted email (Test Mode, dev recipient) → notification log. A second run produces no duplicate. A KB version change flips a "no evidence on file for GS1 lead" gap to a match (v1.1 §11).

### Hostile/failure testing
- See §11.2. System must survive these and stay loud-and-safe.

## 11.2 Failure scenarios the system must survive

| # | Scenario | Expected behaviour |
|---|---|---|
| F1 | Source is down / unreachable | Structured log + throttled dev alert; watcher keeps trying; no crash |
| F2 | Source structure changed (parser mismatch) | `parser_mismatch` alert; run fails loudly; no silent "0 new tenders" forever |
| F3 | Source returns empty list repeatedly | RunHistory reflects runs; no false confidence |
| F4 | One of ten attachments 404s | Other nine continue; missing doc flagged; `incomplete_inputs = true` |
| F5 | ZIP contains corrupt member | That member fails; rest of archive processed; status per file |
| F6 | Scanned PDF OCR fails | `ocr_failed` recorded; document flagged missing-for-AI; verdict still possible with `incomplete_inputs` |
| F7 | OCR/vision half-succeeds | Mixed status recorded per document |
| F8 | LLM call times out | Retry (default 2) → fallback → alert if exhausted; attempts recorded |
| F9 | LLM returns invalid JSON (twice) | `verdict_failed`; alert; **raw notice still emailed** with banner |
| F10 | Budget reaches 100% | Stage B paused; tender queued `awaiting_budget`; triage continues |
| F11 | Mail provider 1 transient error then success | Retry logs; delivered; no duplicate |
| F12 | Mail provider 1 permanent error | Skip + alert; provider 2 delivers; failover recorded |
| F13 | All mail providers fail | `pending_retry` persists; red banner; retried on schedule |
| F14 | Ambiguous provider timeout during failover | Possible duplicate flagged `possible_duplicate`; logged; not silent |
| F15 | Crawl crashes mid-run | Seen-tenders not lost; next scheduled run proceeds |
| F16 | Admin app down | Worker keeps running on last saved config |
| F17 | Last active dev recipient removed/deactivated | Refused; error surfaced |
| F18 | Unapproved fallback profile would receive KB | Refused; no company data sent |
| F19 | Oversized bundle | Map/reduce summarisation; flagged in audit |
| F20 | Deterministic Emails leak in Test Mode | No business recipient ever receives; `[TEST]` prefix verified |

## 11.3 Delivery requirements

- Unit + integration test suites runnable without live credentials (fixtures and fakes).
- A "send test email"-safe harness for mail tests (never real Sendlib in CI by default; a sandbox/probe endpoint or fake).
- Coverage boundaries: every stage has at least one happy-path and one failure-path test.
- Error-code taxonomy must match §6.1 named codes where relevant (`source_unreachable`, `parser_mismatch`, `document_download_failed`, `ocr_failed`, `ai_call_timeout`, `ai_invalid_output`, `budget_exceeded`, `email_send_failed`, `provider_failover`).

## 11.4 Confirmed vs proposed

- **Confirmed:** layers above derive from explicit v1.1 requirements and acceptance criteria.
- **Proposed:** specific test frameworks, CI details, coverage percentages.
- Testing must never be removed to make a hidden defect pass; tests are part of the definition of done (PROJECT_RULES).
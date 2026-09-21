# 14 — Acceptance Criteria

> Source: v1.1 §11 (authoritative). v1.0 §11 criteria that v1.1 revised are noted. Where nothing is given, criteria are written as Given/When/Then statements derived directly from v1.1 requirements — do not treat this as new business behaviour.

## 14.1 Infrastructure

- **G1** Given a running worker, When a crawl of any active source completes, Then a `RunHistory` row records start/end, listings found, new count, error count and failed correlation IDs.
- **G2** Given a crashed run, When the next scheduled run starts, Then previously seen tenders are preserved and no duplicate notifications are produced.
- **G3** Given any single tender, When its full history is queried by correlation ID, Then the timeline (fetch → extraction → triage → verdict → email, incl. provider/profile used) is reconstructable in under a minute without reading raw scraper output.
- **G4** Given a structured-log sink, When an event occurs, Then a JSON record exists with timestamp, correlation ID, source, stage, status and a machine-readable error code.
- **G5** Given configuration changes, When the next run starts, Then the worker uses the new values with no redeploy and no restart (config reload at run start).

## 14.2 Source ingestion

- **S1** Given a source outage, When the watcher detects a failure, Then a throttled alert reaches the dev list (not silence), and repeated failures do not spam.
- **S2** Given a source whose parser structure changes, When the adapter mismatches, Then a `parser_mismatch` alert is raised.
- **S3** Given an active source, When a source is added/disabled/re-scheduled via configuration alone, Then it takes effect with no deployment (verified by adding a live test source).
- **S4** Given a polite crawler, When any request is made, Then robots.txt, rate limits, a real UA and error backoff are respected.

## 14.3 WAHO

- **W1** Given the WAHO adapter, When a listing page is crawled, Then new listings are detected, deduped by `(source_id, external_id)`, and previous ones are not reprocessed or re-notified.
- **W2** Given a WAHO detail page, When attachments are discovered, Then every linked document (incl. docs inside ZIPs) is fetched and recorded with filename, source URL, storage copy, type/size/language and checksum.
- **W3** Given WAHO French/English/Portuguese content, When extraction completes, Then language is preserved/tagged so the AI stage can work in the right language.
- **W4** Given a previously-seen WAHO tender with a new addendum or extended deadline, When the watcher re-checks it, Then a lower-priority update event is processed per the update template.
- **W5** Given the WAHO adapter, When a dry-run "Test this source" executes, Then it shows what it would find without writing to seen-tenders and without emailing anyone.

## 14.4 Document processing

- **D1** Given native PDF, DOCX and ZIP inputs, When extraction runs, Then text is extracted with structure (incl. tables) preserved per source requirements.
- **D2** Given a scanned/non-English PDF, When extraction runs, Then it is correctly read/OCR'd and its content is reflected in the verdict (also addresses the v1.1 §11 scanned-PDF criterion).
- **D3** Given one broken attachment out of many, When the fetcher runs, Then the other documents are processed and the broken one is flagged (`incomplete_inputs`, email footer).
- **D4** Given reusable extraction output, When the AI stage re-runs, Then documents are not re-OCR'd/re-extracted a second time.
- **D5** Given a corrupt/unreadable attachment, When processed, Then a per-file failure is recorded without blocking the pipeline.

## 14.5 Triage (Stage A)

- **T1** Given a clearly irrelevant notice (wrong sector/region), When triage runs, Then it is filtered before any full AI call.
- **T2** Given admin-configurable triage rules, When rules/threshold change, Then the next run honours them (no redeploy).
- **T3** Given a triage-stage error, When evaluation cannot be determined, Then the tender is not silently dropped as irrelevant (warning/alert path).
- **T4** Given high-volume sources, When tested, Then triage demonstrably filters before hitting AI (load-test criterion, v1.1 Phase 4).

## 14.6 Verdict (Stage B)

- **V1** Given a surviving tender, When the verdict runs, Then structured JSON is produced containing Background, Requirements (with exact deadline date+time+timezone), Why-we-can-cannot (requirement-by-requirement, citing KB sections), a verdict enum value (APPLY / DO NOT APPLY / APPLY WITH CONDITIONS), a confidence score, and an urgency flag.
- **V2** Given an output that fails JSON/schema validation, When validated, Then it is retried once; if still invalid, the tender is marked `verdict_failed`, an alert is raised, and the raw notice is still emailed with an "automatic assessment unavailable" banner.
- **V3** Given any "meets requirement" claim, When the verdict is audited, Then the claim cites its KB section, or is marked "unverified".
- **V4** Given a missing capability unrelated to a documented fact, When a gap is phrased, Then it reads "no evidence on file for X", never an invented negative capability claim.
- **V5** Given a tender with failed attachments, When the verdict completes, Then `incomplete_inputs = true` and the email says so.
- **V6** Given a tender bundle + KB exceeding the profile's context window, When handled, Then per-document summarisation (map) precedes verdict on summaries (reduce) and the event is recorded.
- **V7** Given an urgent deadline within the configurable window, When the verdict completes, Then the urgency flag is set regardless of verdict.
- **V8** Given a completed verdict, When inspected, Then it records llm_profile_id, model, knowledge_base_version_id and prompt_version.

## 14.7 Knowledge base

- **K1** Given a KB update, When saved, Then a new immutable version is created with content hash and timestamp.
- **K2** Given a KB version change, When a subsequent verdict compares (e.g. adding a GS1 certificate), Then a prior "no evidence on file for GS1 lead" gap flips to a match, demonstrating the change (v1.1 §11).
- **K3** Given an unapproved verdict provider, When KB is available, Then the system sends only the placeholder/non-sensitive version (data policy).
- **K4** Given the KB, When token budget is checked, Then the admin shows a warning if it exceeds the configurable share of the profile's context window.
- **K5** Given RAG, When v1 is reviewed, Then no vector store / embeddings / retrieval component exists. (v1 excludes RAG.)

## 14.8 Notifications

- **N1** Given a new tender on any active source, When triage passes it, Then an email is triggered within one crawl cycle with no manual intervention.
- **N2** Given a pass-through tender, When the email is composed, Then it contains the three §8.1 sections plus the attachments/links footer, correctly populated.
- **N3** Given a tender with documents, When not all fit a provider's limits, Then the excess documents are delivered as working, expiring links listed by name, and the email states attached-vs-linked.
- **N4** Given the same tender, When crawled twice in normal operation, Then exactly one notification email is produced (idempotency).
- **N5** Given an ambiguous provider timeout during failover, When a duplicate might occur, Then it is rare, flagged `possible_duplicate`, and logged.
- **N6** Given provider 1 disabled or failing, When an email is needed, Then it goes out through provider 2 and the log shows the failover.
- **N7** Given all providers failing, When the message cannot be sent, Then it stays `pending_retry`, is retried on schedule, and the health view shows a red banner.
- **N8** Given an eligible send, When the email is posted, Then the dedupe key (tender + verdict + recipient-set hash) is stored and present as an email header.
- **N9** Given provider credentials are bad or quota exhausted, When the chain runs, Then it skips to the next provider AND raises an alert.
- **N10** Given recipient list edits, When a notification is later inspected, Then the recipients snapshot at send time is retained and no duplicate send occurs for new members until the next relevant tender.

## 14.9 Test Mode

- **M1** Given Test Mode ON, When a tender email is generated, Then no business recipient receives it, it goes only to the dev list, and the subject is prefixed `[TEST]`.
- **M2** Given Test Mode, When turned off, Then it is an explicit, audit-logged admin action; default state is ON until go-live.
- **M3** Given "Test this source" / "Test connection" / "Send test email", When executed, Then they are dry-runs/test-only and write nothing to seen-tenders and send nothing to business recipients.
- **M4** Given an unapproved profile in the verdict role, When a verdict runs, Then only placeholder KB content is sent.

## 14.10 Admin

- **A1** Given the admin app, When a source/recipient/profile/settings change is saved, Then it is audit-logged (actor, entity, fields — never secret values) and picked up on the worker's next run.
- **A2** Given the admin app down, When the worker's schedule fires, Then the worker still runs on the last saved configuration.
- **A3** Given LLM profile changes (base URL, API key, model), When "Test connection" passes, Then the next pipeline call uses the new profile with no redeploy.
- **A4** Given the LLM provider screens, When an `approved_for_company_docs` flag is toggled, Then it is an explicit audit-logged action and the gate applies immediately.
- **A5** Given recipient management, When the last active dev recipient is targeted for deactivation/deletion, Then the operation is refused.
- **A6** Given the health dashboard, When viewed, Then per-source last successful run and last failure category are shown at a glance, plus 7/30-day counts and chain status.
- **A7** Given knowledge-base admin, When versions are compared, Then a diff between versions is available with a token-budget indicator.

## 14.11 Security

- **SE1** Given any secret (source auth, LLM key, mail credential, OAuth token), When stored/used, Then it is encrypted at rest, write-only, never returned by APIs, and never present in logs or ConfigChangeLog.
- **SE2** Given the encryption master key, When referenced, Then it exists outside the database (env var or secrets manager).
- **SE3** Given a profile with `approved_for_company_docs = false`, When a call would include KB content, Then the content is refused, including when the profile is a fallback.
- **SE4** Given a Viewer role, When the admin app is used, Then knowledge base and secrets are never exposed.
- **SE5** Given an unsigned or expired link, When accessed, Then access is denied (links are signed and expiring).
- **SE6** Given production deployment, When go-live is reviewed, Then a security note lists every third party that sees data, with an owner.

## 14.12 Audit

- **AU1** Given every tender/document/verdict/email, When recorded, Then each is logged with timestamps and the tender's correlation ID for answering "did we see this, and what did the AI say?" months later.
- **AU2** Given stage execution, When a stage completes, Then its status is recorded (success/failure/warning with machine-readable code), not merely its result.
- **AU3** Given retention settings (default e.g. 12 months), When audit data ages, Then run history, stage logs and verdicts are retained for the configured minimum.

## 14.13 Failure handling

- **FH1** Given a crash mid-crawl, When recovery occurs, Then seen-tenders are not lost and the next run proceeds.
- **FH2** Given an LLM timeout/rate-limit/5xx, When handled, Then retry (default 2) then fallback run, with attempts recorded.
- **FH3** Given the budget guard at 100%, When Stage B is requested, Then Stage B pauses, tenders queue `awaiting_budget`, and triage continues.
- **FH4** Given the budget guard at 80%, When usage grows, Then an alert is raised.
- **FH5** Given a document that cannot be downloaded/extracted, When the tender proceeds, Then `incomplete_inputs = true` and the human is told which document failed.

## 14.14 Deployment

- **DP1** Given a fresh environment, When bootstrapped, Then the first dev alert recipient is seeded from an environment variable.
- **DP2** Given Test Mode, When a new environment is created, Then Test Mode defaults ON.
- **DP3** Given production, When a conventional (domain-requiring) provider is configured, Then SPF/DKIM DNS records exist for its domain.
- **DP4** Given a backlog flush, When sending resumes, Then the dispatcher throttles to provider rate limits (e.g. Sendlib 30/min Free / 300/min Pro).
- **DP5** Given the notification path, When an email is sent, Then our own notification log is the source of truth (never third-party provider logs).

---

## Mapping to v1.0 criteria (historical)

- v1.0 "six sections/fields in §8" — **revised** by v1.1 §0.3: three sections + attachments/footer.
- v1.0 "notify OPEX via email" on source failure — **revised**: dev list (v1.1 §5.2).
- v1.0 single ~20 MB attachment threshold — **superseded** by capability-aware attachment planner (v1.1 §5.7).
- v1.0 RAG-as-v1 — **removed** from v1 (v1.1 §3, §5.5). K5 codifies its absence.
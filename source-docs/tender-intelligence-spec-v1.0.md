Tender Intelligence & Auto-Notification System
Technical Specification — v1.0
Prepared for: OPEX Consulting Limited / RegTech365 Prepared by: Emmanuel (Senior Product Manager) Audience: Intern / Developer building the system

1. Problem Statement
On the morning of 18 September 2026, someone at OPEX Consulting finally opened the WAHO tender portal and found this:

"The West African Health Organization (WAHO), under AfDB Grant No. 2100155041318, has invited Expressions of Interest for consultancy services relating to the development of a regional pharmaceutical product traceability system... The deadline for the EOI submission is today, Friday, 18 September 2026, at 1:00 PM Nigerian time."

It was already too late. The notice had been live for weeks. By the time anyone at OPEX read it, there were hours left, not days — and even if there had been time, nobody had pulled together the "why we cannot apply" gaps in advance: no demonstrable GS1/traceability experience on file, no pre-assembled reference table, no consortium lined up to cover the GS1-certified Lead Consultant requirement. The opportunity wasn't lost because OPEX was unqualified to pursue it — it was lost because nobody was watching, and even when someone did look, there was no fast way to tell "is this even worth our time" from "this is a must-apply."

This is not a one-off. OPEX and RegTech365 sit inside a market — GRC, ITGRC, fintech regulatory technology, compliance platforms — where the highest-value opportunities are published by scattered, differently-structured bodies: regional health/development bodies like WAHO, UN agencies through UNGM, commercial aggregators like TenderDetail, pan-African platforms like All Business Africa, and others that will appear over time. Each of these publishes dozens to thousands of notices, in multiple languages, most of which are irrelevant to OPEX's actual capabilities. A human checking five to ten sites a day, reading every notice and every attached PDF, and then judging fit against OPEX's real track record, is not a sustainable process — it's exactly how the WAHO deadline was missed, and it is how the next ten will be missed too.

What OPEX actually needs is a system that never sleeps: it watches a configurable list of tender sources, picks up new postings automatically, pulls down every attached document (RFPs, TORs, annexes — regardless of format or language), reads and understands them, and then — using a living record of what OPEX/RegTech365 has actually done, is certified for, and can credibly claim — renders a verdict: apply, or don't, and why, in a format the team can act on within minutes, not days. That verdict has to arrive as an email, with every supporting document already attached, before the deadline is a rounding error away.

This document specifies that system.

2. Goals
Never miss a relevant high-value tender again because nobody was watching a source.
Cut evaluation time from hours to minutes by having AI produce a structured go/no-go verdict, not just a raw notice.
Ground every verdict in OPEX's real capability record, not generic guesswork — via an uploadable, growable knowledge base of company documents.
Make sources pluggable: adding WAHO, UNGM, TenderDetail, All Business Africa, or any future source should be a configuration change, not a code change.
Never lose a document: every attachment on a tender notice must be captured and delivered with the notification.
Support multiple languages and document formats without manual pre-processing (WAHO notices are routinely French/English/Portuguese; documents arrive as PDF, DOCX, ZIP).
3. Non-Goals (for this version)
The system does not submit applications on OPEX's behalf. It notifies humans, who apply.
It does not need to cover every tender site on the internet on day one — it needs to make adding the next one trivial.
Slack/Teams/SMS notification is out of scope for v1 — email only, as explicitly requested. Design the notification layer so other channels can be added later without rework (see §9).
4. System Overview
At a high level, the system is a pipeline with six stages, wrapped by a configuration layer and a knowledge base:

Source Registry
configurable list of tender sites

Watcher / Crawler Engine

New-Listing Detector
dedup against seen tenders

Document Fetcher
downloads every attachment

Document Understanding
OCR + text + table extraction, multi-language

AI Relevance & Verdict Engine

Company Knowledge Base
capability docs, past projects,
certifications, staff profiles

Verdict Formatter
builds the assessment report

Email Dispatcher
attaches all source documents

Notification Log / Audit Trail

In plain language: the Watcher checks each configured source on a schedule. When it finds a listing it hasn't seen before, it downloads the listing page and every document attached to it. It extracts the actual text/content of those documents (even scanned PDFs, even French). It feeds that content, plus OPEX's own capability record, to an AI model whose only job is to answer: is this relevant, can we credibly bid, and why or why not. It writes that answer into the exact report format OPEX already uses (see §8), and emails it out with the original documents attached. Everything that happens is logged so nothing is sent twice and nothing silently fails.

5. Functional Requirements
5.1 Source Registry (dynamic configuration)
This is the piece that makes the system extensible. Sources must not be hardcoded.

Each source is a record with, at minimum:
name (e.g. "WAHO Tenders")
base_url / listing_url
source_type — an enum describing how to read it (see below)
crawl_frequency (e.g. every 6 hours)
language(s) expected
auth (if the source requires login/API key — optional, encrypted at rest)
active flag (true/false — lets ops disable a source without deleting its history)
parser_config — a small structured block telling the crawler how to find listing rows, detail links, and attachment links on that specific site (see 5.2)
Sources must be addable, editable, and disable-able without a code deployment. At minimum this means a config file (YAML/JSON) reloaded on schedule, or — better, for a non-technical PM to use later — a simple admin screen with a form and a "Test this source" button that runs one crawl and shows what it found.
On startup and on each config reload, the system validates every active source is reachable and logs a warning (not a crash) for any that isn't.
Source types to support from day one (each site structures its tenders differently, so the crawler needs a strategy per type, not one hardcoded scraper):

Type	Example	Notes
Paginated HTML list	WAHO Tenders, TenderDetail	Follow "next page" links until no new items found
Filtered/faceted HTML list	All Business Africa (?status=open)	Respect query-string filters defined in config
Search-form-driven	UNGM (requires submitting a search form, no static listing)	Needs a small per-source adapter that fills and submits the form, or uses the site's API/export if one exists
RSS/Atom feed	(future sources may offer this)	Trivial to add — treat as its own type
JSON API	(future sources, e.g. OCDS-based portals like South Africa's eTenders, which All Business Africa itself links to)	Prefer this over HTML scraping wherever a source exposes one
Design note for the intern: don't try to build one universal scraper that "understands" any tender site. Build a small SourceAdapter interface (list_new_tenders(), get_detail(tender_id), get_attachments(tender_id)) and implement one adapter per source_type. Adding a new site of a type you already support (e.g. another paginated-HTML tender board) should require only a new config entry, not new code. Adding a genuinely new type of site is the only case that needs new code — and even then, only a new adapter class.

5.2 Watcher / Crawler Engine
Runs each active source on its configured schedule.
Must be polite: respect robots.txt where present, rate-limit requests, use a real user agent, and back off on errors rather than hammering a site.
Detects new listings only — needs a persistent record of tender IDs/URLs already seen per source (see §7 data model), so re-runs don't reprocess or re-notify on the same tender.
Must also detect updates to a previously-seen tender (e.g. a new addendum document, or an extended deadline) as a distinct, lower-priority event — this matters a lot for WAHO-style notices where documents get added after initial publication.
On a source-level failure (site down, structure changed, blocked), log the failure with enough detail to debug, and notify OPEX via email that a source needs attention — a silently broken watcher is worse than no watcher, because it creates false confidence.
5.3 Document Fetcher
For every new (or updated) tender, download every linked document on its detail page — RFPs, TORs, annexes, EOI forms, procurement plans, ZIP archives (unzip and recurse).
Preserve original filenames and source URLs; store a local/cloud copy (don't rely on the source URL remaining valid — WAHO/TenderDetail links can rot).
Record file type, size, language (best guess), and a checksum, so duplicate attachments aren't refetched or re-sent.
Handle failures per-document (one broken link shouldn't block the other nine documents on the same tender) and flag missing documents in the eventual verdict/email rather than failing silently.
5.4 Document Understanding
This is the "read and understand the attached documents" requirement, and it has to work across:

Native PDFs — direct text extraction.
Scanned/image PDFs — OCR (the WAHO TORs and AMI notices are frequently scanned or mixed).
DOCX — direct text extraction (WAHO explicitly publishes .docx TORs).
Multi-language content — WAHO alone publishes in French, English, and Portuguese in the same notice. Extraction must preserve/tag language so the AI stage can work in the right language, or translate to a working language before assessment.
Tables — deadlines, eligibility criteria, and evaluation matrices are frequently in tables; a naive text dump loses structure that matters for the verdict (e.g. "10+ years' experience" as a line item).
Output of this stage: one structured "document bundle" per tender — plain text + metadata per document, ready to hand to the AI stage. This bundle should be reusable (don't re-OCR the same PDF twice if the AI stage is re-run).

5.5 Company Knowledge Base ("what we do / have done")
This is the piece that makes the AI verdict specific to OPEX instead of generic. It must be:

A place to upload documents, at minimum: company profile/capability statement, certificates of incorporation, past project reference lists, completion certificates, staff CVs/qualifications, professional certifications (ISO, GS1, etc.), sector experience write-ups, consortium/partner agreements on file.
Editable over time — this is a living record. As OPEX completes new projects or gains new certifications, someone should be able to add a document (or a short structured note) without a deployment.
Structured enough to be queried — don't just dump files in a folder. Each upload should carry basic metadata: document type (capability statement / reference / certificate / CV / other), date, tags (e.g. "GRC", "fintech", "public health", "GS1", "West Africa"), and a short human-written summary of what it proves. This metadata is what lets the AI stage retrieve the right three or four documents for a given tender instead of stuffing everything into one giant prompt.
Internally, this is a small retrieval store: when a new tender needs assessing, the system searches the knowledge base for the documents most relevant to that tender's requirements (by tag/keyword and by semantic similarity) and hands only those to the AI — this is standard retrieval-augmented generation (RAG), and the intern should treat it as its own component, not an afterthought bolted onto the AI call.
5.6 AI Relevance & Verdict Engine
Given a tender's extracted document bundle and the retrieved, relevant slice of the knowledge base, the AI stage must produce a structured verdict containing exactly the sections OPEX already uses (see §8 for the full template), in particular:

Background — plain-language summary of what's being procured, who's procuring it, reference/grant numbers, and the scope.
Requirements — extracted eligibility criteria, required experience, required documents/evidence, deadline (date and time, with timezone — this is what got missed last time).
Why we can/cannot apply — the actual gap analysis: for each requirement, does OPEX's knowledge base show evidence of meeting it, partially meeting it, or not meeting it at all? This section must name the specific gap (e.g. "no GS1-certified Lead Consultant on file"), not just say "not a fit."
A verdict/recommendation field: APPLY, DO NOT APPLY, or APPLY WITH CONDITIONS (e.g. "only if we can secure a GS1-certified partner within N days"), plus a confidence score.
A deadline urgency flag — if the deadline is within a configurable threshold (e.g. 5 business days), mark it urgent regardless of the verdict, so a borderline-relevant but time-critical notice doesn't get buried.
Two-stage AI processing is recommended rather than one big call per tender:

Stage A — cheap triage: a lightweight pass (can even be rule/keyword-based initially) that filters out obviously irrelevant notices (wrong sector entirely, wrong region, etc.) before spending a full AI call on it. This matters because sources like All Business Africa or TenderDetail publish thousands of unrelated notices (school furniture, army boots, road maintenance) — most of it should never reach the expensive AI step.
Stage B — full assessment: only for what survives triage, run the full document-understanding + RAG + verdict generation described above.
5.7 Notification (Email)
One email per new/updated tender that passes triage (relevant enough to be worth a human's two minutes, even if the eventual verdict is "do not apply" — OPEX still wants visibility, per the original request).
Email body follows the template in §8 exactly.
Every supporting document from the tender's detail page must be attached to the email, or, if total size exceeds a reasonable email attachment limit, linked via a secure download link with the documents listed by name in the body — but attach directly by default; only fall back to links when attachments would push the email over a safe size threshold (configurable, e.g. 20MB).
Recipients configurable per source or globally (a list, not a hardcoded address).
Deliverability basics: proper From address, SPF/DKIM on the sending domain, plain-text + HTML versions of the body.
5.8 Configuration & Extensibility
Restating this because it's the explicit ask: the whole system must be reconfigurable without code changes for the common operations:

Add a source
Disable/remove a source
Change crawl frequency
Change notification recipients
Add/update knowledge base documents
Adjust the relevance triage threshold / urgency window
A config file is the minimum bar; a small internal admin page is the better bar (and will matter once this is handed off past the intern stage).

6. Non-Functional Requirements
Reliability: a crashed run should not lose track of what's already been seen or block the next scheduled run.
Idempotency: re-running a crawl must never send a duplicate notification for the same tender.
Auditability: every tender seen, every document fetched, every verdict generated, and every email sent must be logged with timestamps — OPEX needs to be able to answer "did we see this one, and what did the AI say?" months later.
Security: source credentials (if any) encrypted at rest; knowledge base documents (which may contain sensitive company/financial info) access-controlled.
Cost control: because AI calls cost money per document/tender, the triage stage (§5.6) exists specifically to avoid running full AI assessment on every single notice from high-volume sources.
Observability: a simple daily digest or dashboard of "sources checked, new tenders found, verdicts issued, failures" so health of the system is visible without reading raw logs.
6.1 Auditability & Failure Diagnosis (Production Requirement)
This is explicitly a production requirement, not a nice-to-have: when something breaks, it must be trivial to find out what broke, where, and why — without reading through raw scraper output line by line.

Every tender's journey must be traceable end-to-end. Give each tender (and each individual pipeline run) a unique correlation ID the moment it's first detected, and stamp every subsequent log line, database row, and error with it. It should be possible to ask "what happened to tender X?" and get one linear timeline back: seen → documents fetched (which ones, which failed) → text extracted (which succeeded, which needed OCR, which failed) → AI triage result → full AI verdict (which knowledge-base documents were retrieved, what the model returned, how long it took) → email composed → email sent/failed.

Every pipeline stage records a status, not just a result. Don't just store "verdict: APPLY" — store that stage 3 (document extraction) succeeded for 4 of 5 attachments, which one failed and why, and that stage 5 (AI verdict) ran despite the missing attachment, so a reviewer immediately knows the verdict may be based on incomplete information rather than assuming everything went cleanly.

Structured logging, not free text. Every log line should be a structured record (JSON or equivalent) with at minimum: timestamp, correlation ID, source name, stage name, status (success/failure/warning), and a machine-readable error code/category where relevant (e.g. source_unreachable, parser_mismatch, document_download_failed, ocr_failed, ai_call_timeout, email_send_failed). Free-text stack traces can be attached as detail, but the top-level record must be filterable/queryable — "show me every parser_mismatch in the last 7 days" needs to be a five-second query, not a grep-and-hope.

A run history table/view, independent of individual tender records: for every scheduled crawl of every source, log start time, end time, number of listings found, number new, number of errors, and a link to the correlation IDs of anything that failed. This is what answers "is WAHO's watcher actually running, and when did it last succeed?" at a glance.

Alerting on breakage, not just logging it. A source adapter failing silently is the exact failure mode this whole system exists to prevent (see §5.2) — so any stage failure above a configurable threshold (e.g. a source fails 2 runs in a row, or an AI call fails outright, or an email fails to send) must trigger an internal notification of its own, separate from the tender-notification emails, so a broken watcher gets fixed in hours, not discovered weeks later when a deadline is missed again.

Retention: keep run history, per-tender stage logs, and generated verdicts for a defined minimum period (e.g. 12 months) so past decisions can be reviewed and the pipeline's accuracy over time can be assessed — this also matters if OPEX ever needs to explain, after the fact, why a particular tender was or wasn't flagged.

A single "health" view (even a simple internal page or a scheduled summary email) showing, per source: last successful run, last failure (if any) and its category, and rolling counts of new tenders / verdicts / notification failures over the past 7 and 30 days. This is the thing to check first when someone asks "is this thing actually working?"

Source: id, name, type, url, parser_config, schedule, active, last_run_at, last_error

Tender: id, source_id, external_id/url, title, published_date, deadline (date+time+timezone), raw_metadata, status (new/updated/processed), first_seen_at

Document: id, tender_id, filename, source_url, storage_path, mime_type, language, checksum, extracted_text_ref

KnowledgeBaseDoc: id, doc_type, tags[], summary, storage_path, uploaded_at, uploaded_by

Verdict: id, tender_id, recommendation (enum), confidence, background_summary, requirements_summary, gap_analysis, urgency_flag, generated_at, model_version

NotificationLog: id, tender_id, verdict_id, recipients[], sent_at, attachments[], status (sent/failed), error

8. Email Output Format (must match this structure)
Every notification email must follow this exact structure — this is the template OPEX already uses, and the AI's job is to populate it, not redesign it:

Subject: [SOURCE] Expression of Interest – Assessment Report: <Tender Title>

1. Background
<Plain-language summary: procuring body, grant/project ID if present,
scope of the assignment.>

2. Requirements for [EOI/RFP/Tender]
<Bulleted eligibility/experience/document requirements, extracted from
the notice and its attachments. Include the exact deadline — date, time,
and timezone.>

3. Why We Can / Cannot Apply
<Point-by-point comparison against OPEX's knowledge base. Name specific
gaps or specific matches. End with a clear recommendation line:
APPLY / DO NOT APPLY / APPLY WITH CONDITIONS (+ confidence).>

[Attachments: every document found on the tender's detail page]
9. Suggested Technical Approach (guidance, not mandate)
The intern should feel free to substitute equivalent tools, but as a starting point:

Scheduler: cron or a simple job queue (e.g. Celery, or even a basic scheduled script) — no need for heavy orchestration at this scale initially.
Crawling: requests/httpx + BeautifulSoup for static HTML; a headless browser (Playwright) only for sources that require JS rendering or form submission (e.g. UNGM's search-driven listing).
Storage: a relational database (Postgres is fine) for the structured data in §7, plus object storage (or just a well-organized filesystem/bucket) for the raw documents.
Document extraction: PDF text libraries for native PDFs, an OCR engine for scanned ones, a DOCX parser for Word files.
AI/LLM layer: any capable model API, called twice — once for cheap triage (can start as pure keyword/tag matching, upgraded to a model later), once for the full verdict generation with retrieved knowledge-base context (RAG).
Email: any transactional email API (or SMTP) that supports attachments and HTML bodies reliably.
Config: start with a version-controlled YAML file for sources; graduate to a small admin UI once the pipeline is proven.
Keep the source adapter and AI verdict engine as the two most decoupled, swappable pieces — those are the parts most likely to need new implementations over time (new site structures, better models).

10. Phased Delivery Plan
Because this is a first build for an intern, don't attempt all of this at once. Suggested phases:

Phase 1 — Prove the pipeline on one source Pick WAHO (most structured, clearest example) end-to-end: crawl → detect new → fetch docs → extract text → manually-templated email with attachments. No AI yet. Goal: prove nothing gets missed.

Phase 2 — Add the AI verdict Build the knowledge base upload mechanism (even a simple folder + metadata file is fine at first), wire in the two-stage triage/verdict AI, and match the email to the exact template in §8.

Phase 3 — Generalize to multiple sources Add the SourceAdapter abstraction, implement adapters for TenderDetail and All Business Africa, and prove that adding UNGM (the hardest one, form-driven) only requires one new adapter, not a rewrite.

Phase 4 — Configuration & hardening Move source config out of code, add the failure-notification path (§5.2), add the audit log/dashboard, and load-test against a high-volume source (All Business Africa's hundreds of open tenders) to confirm triage is filtering correctly before it hits AI.

11. Acceptance Criteria
The system is done for v1 when:

 A new tender posted on any active configured source triggers an email within one crawl cycle, with no manual intervention.
 The email contains all six sections/fields described in §8, correctly populated.
 Every document attached to the original tender notice is attached to (or reliably linked from) the email.
 The same tender never generates two notification emails.
 A source can be added, disabled, or have its schedule changed via configuration alone — verified by adding a test source live, with no deployment.
 A new document can be added to the knowledge base and demonstrably changes a subsequent verdict (e.g. adding a GS1 certificate flips a prior "cannot apply due to no GS1 lead" gap to a match).
 A scanned, non-English PDF attachment is correctly read and its content reflected in the verdict.
 A source outage produces an internal alert, not silence.
 Given any single tender, its entire processing history (fetch → extraction → triage → verdict → email) can be reconstructed from logs/data in under a minute, by correlation ID, without reading raw scraper output.
 A health view/summary shows, per source, last successful run and last failure category at a glance.
12. Open Questions for the Intern to Flag Back (don't guess on these — ask)
Which mailbox/domain should send these emails, and who are the actual recipients (and does that list differ by source or urgency)?
Are there sources that require paid access or registration (TenderDetail appears to be subscription-based) — does OPEX have/want an account, or should that source be scraped from the public free-tier pages only?
What's the acceptable AI cost budget per month, given some sources (All Business Africa, TenderDetail) publish very high volumes?
Where should the knowledge base and document archive physically live (cloud storage the company already uses, vs. something new)?
Legal/ToS check: some sites may restrict automated scraping in their terms — worth a quick read per source before building an adapter for it, and preferring official APIs/feeds where they exist (e.g. OCDS-based portals).
# 03 — Data Model

> Source of truth: v1.1 §7 (confirmed entity list). Field sets are given largely as-is from v1.1. This document separates **confirmed requirements** from a **proposed schema implementation**; the coding AI must not exceed confirmed fields with invented behaviour (e.g. invented business rules, enum values not in the source).

## Reading this document

- **Confirmed** = named/implied directly by v1.1 §7 or functional sections. These are requirements.
- **Proposed** = a reasonable encoding of a confirmed concept (types, indexes, nullable-ness). Label `[PROPOSED]`; subject to change by the implementing team.
- Every entity records `correlation_id` or references one as appropriate for traceability.

---

## 3.1 Confirmed entities

| Entity | Purpose | Status |
|---|---|---|
| `Source` | A configurable tender site. | Confirmed (v1.1 §5.1, §7) |
| `Tender` | A listing found on a source, with status and correlation_id. | Confirmed (§7) |
| `Document` | One file attached to a tender. | Confirmed (§7) |
| `KnowledgeBaseVersion` | Immutable version of the KB document (replaces v1.0 `KnowledgeBaseDoc`). | Confirmed (§7; v1.0 `KnowledgeBaseDoc` superseded) |
| `Verdict` | Stage B structured assessment of a tender. | Confirmed (§7) |
| `LLMProfile` | A saved LLM provider configuration. | Confirmed (§5.9.1, §7) |
| `LLMRoleAssignment` | Maps a role to a profile (+ optional fallback). | Confirmed (§5.9.3, §7) |
| `LLMCall` | Per-call usage/metadata record. | Confirmed (§5.9.6, §7) |
| `MailProvider` | A mail provider configuration with capabilities and breaker state. | Confirmed (§5.10.2, §7) |
| `NotificationLog` | Message-level record incl. recipients snapshot, attachments vs links. | Confirmed (§5.7, §5.10.1, §7) |
| `NotificationAttempt` | Per-provider attempt record. | Confirmed (§5.10.2, §7) |
| `Recipient` | One recipient on the tender or dev-alert list. | Confirmed (§5.10.1, §7) |
| `AlertEvent` | An open/recovered alert. | Confirmed (§5.10.4, §7) |
| `RunHistory` | Per-source crawl run summary. | Confirmed (§6.1, §7) |
| `Settings` | System-wide toggles and thresholds. | Confirmed (§7) |
| `ConfigChangeLog` | Audit trail of configuration changes (never secret values). | Confirmed (§7, §5.9.5) |
| `AdminUser` | Admin app account. | Confirmed (§7) |
| `KnowledgeBaseDoc` (phase 2) | Per-file metadata records for retrieval. | Deferred (phase 2/5, §5.5) |

---

## 3.2 Entity details

### Source
- **Purpose:** configurable record per tender site; the extensibility backbone.
- **Fields (confirmed):** `id`, `name`, `type` (`source_type`), `url` (`base_url`/`listing_url`), `parser_config`, `schedule` (`crawl_frequency`), `active`, `last_run_at`, `last_error`, optional `recipient_scope` (restricts tender recipients for this source).
- **Also confirmed:** optional `auth` (encrypted at rest), `language(s)` expected.
- **Relationships:** 1–N `Tender`; 1–N `RunHistory`; optional link to `AlertEvent`; `recipient_scope` references recipients.
- **Constraints:** unique `name` `[PROPOSED]`; `active` should be safe to re-enable (history preserved).
- **Indexing:** by `active`, `schedule`. `[PROPOSED]`
- **Audit:** add/edit/disable via `ConfigChangeLog`.
- **Security:** auth credentials encrypted at rest; never logged.

### Tender
- **Purpose:** one listed tender, de-duplicated per source.
- **Fields (confirmed):** `id`, `source_id`, `external_id`/`url`, `title`, `published_date`, `deadline` (UTC + original timezone string), `raw_metadata`, `status` (`new`/`updated`/`processed`/`verdict_failed`/`awaiting_budget`), `first_seen_at`, `correlation_id`.
- **Relationships:** N–1 `Source`; 1–N `Document`; 1–N `Verdict`; 1–N `NotificationLog`.
- **Constraints:** unique `(source_id, external_id)` so re-crawls map to the existing row `[PROPOSED]`; `correlation_id` unique, immutable.
- **Indexing:** by `source_id + external_id`, by `status`, by `deadline`, by `correlation_id`. `[PROPOSED]`
- **Audit:** `first_seen_at`, status transitions, correlation timeline.
- **Security:** `raw_metadata` may include URLs; no secrets.

### Document
- **Purpose:** one attachment under a tender; tracks download + extraction state.
- **Fields (confirmed):** `id`, `tender_id`, `filename`, `source_url`, `storage_path`, `mime_type`, `language` (best guess), `checksum`, `extracted_text_ref`, `download_status`, `extraction_status`.
- **Relationships:** N–1 `Tender`.
- **Constraints:** unique `(tender_id, checksum)` so duplicates aren't refetched/re-sent `[PROPOSED]`.
- **Indexing:** by `tender_id`, by `checksum`, by `download_status`, by `extraction_status`. `[PROPOSED]`
- **Audit:** which succeeded/failed at download and extraction, plus reason codes.
- **Security:** `storage_path` must be adequate to serve secure link downloads; content accessed only through the archive.

### KnowledgeBaseVersion
- **Purpose:** immutable snapshot of the KB document.
- **Fields (confirmed):** `id`, `content_ref`, `content_hash`, `token_count`, `created_at`, `created_by`, `note`.
- **Relationships:** referenced by `Verdict.knowledge_base_version_id`.
- **Constraints:** content immutable once written; hash unique.
- **Indexing:** by `created_at`; by hash.
- **Audit:** who created it (`created_by`), when; diff between versions in admin.
- **Security:** content may be sensitive company/financial info; access-controlled; never sent to unapproved LLM profiles. Phase-2 note: `KnowledgeBaseDoc` (doc_type, tags[], summary, storage_path) returns for retrieval.

### Verdict
- **Purpose:** the Stage B structured assessment.
- **Fields (confirmed):** `id`, `tender_id`, `recommendation` (enum: `APPLY` / `DO NOT APPLY` / `APPLY WITH CONDITIONS`), `confidence`, `background_summary`, `requirements_summary`, `gap_analysis` (with evidence citations), `urgency_flag`, `generated_at`, `llm_profile_id`, `model`, `knowledge_base_version_id`, `prompt_version`, `incomplete_inputs` (bool).
- **Relationships:** N–1 `Tender`; N–1 `LLMProfile`; N–1 `KnowledgeBaseVersion`.
- **Constraints:** `recommendation` enum as above; `llm_profile_id`, `model`, `knowledge_base_version_id`, `prompt_version` recorded per v1.1; `incomplete_inputs` set where any document failed.
- **Indexing:** by `tender_id`, by `generated_at`, by `recommendation`. `[PROPOSED]`
- **Audit:** full traceability per §6.1 (provider, model, KB version, prompt version, what model returned).
- **Security:** no secrets; contains company-relevant analysis (access-controlled like KB).

### LLMProfile
- **Purpose:** saved configuration for talking to one model.
- **Fields (confirmed):** `id`, `name`, `base_url`, `api_key_encrypted`, `model`, `context_window_tokens`, `max_output_tokens`, `temperature`, `timeout_seconds`, `extra_headers`, `supports_json`, `supports_vision`, `cost_per_1k_input`, `cost_per_1k_output`, `approved_for_company_docs` (default **false**), `active`.
- **Relationships:** referenced by `LLMRoleAssignment`, `LLMCall`, `Verdict`.
- **Constraints:** flags default sensibly (`approved_for_company_docs=false`, `active`).
- **Indexing:** by `name`, by `active`.
- **Security:** `api_key_encrypted` — write-only, never returned/logged; master key outside the database.

### LLMRoleAssignment
- **Purpose:** binds a role to a profile (+ optional fallback).
- **Roles (confirmed):** `triage`, `verdict`, `embeddings` (deferred), `vision_ocr` (optional).
- **Fields (confirmed):** role, `profile_id`, `fallback_profile_id` (may be null).
- **Constraints:** fallback must obey the same `approved_for_company_docs` policy as primary (v1.1 §5.9.3).
- **Audit:** changes via `ConfigChangeLog`.

### LLMCall
- **Purpose:** per-call usage and outcome record.
- **Fields (confirmed):** `id`, `correlation_id`, `role`, `profile_id`, `tokens_in`, `tokens_out`, `latency_ms`, `est_cost`, `status`, `error_code`, `created_at`.
- **Audit/Security:** metadata only — never the prompt/content; feeds budget guard.

### MailProvider
- **Purpose:** mail provider configuration plus breaker state.
- **Fields (confirmed):** `id`, `name`, `type`, `credentials_encrypted`, `from_address`, `from_name`, `reply_to`, `priority`, `active`, `capabilities` (`max_attachments`, `max_attachment_mb`, `max_message_mb`, `daily_limit`, `rate_limit_per_min`, `needs_verified_domain`), `breaker_state`, `breaker_until`.
- **Security:** `credentials_encrypted`; write-only; never in logs or responses.

### NotificationLog
- **Purpose:** message-level record; source of truth for what was sent.
- **Fields (confirmed):** `id`, `tender_id`, `verdict_id`, `recipients[]` (snapshot at send time), `sent_at`, `attachments[]` and `links[]` (what was attached vs linked), `status` (`sent`/`failed`/`pending_retry`), `provider_used`, `dedupe_key`, `possible_duplicate` (bool), `error`.
- **Constraints:** dedupe key = tender + verdict + recipient-set hash; stored and sent as an email header. Recipients stored as a snapshot so later edits never rewrite history.
- **Audit:** primary notification audit source (providers' own logs, e.g. Sendlib 5-day retention, are never relied on).

### NotificationAttempt
- **Purpose:** one attempt through one provider.
- **Fields (confirmed):** `id`, `notification_id`, `provider_id`, `status`, `error_code`, `duration_ms`, `attempted_at`.
- **Relationship:** N–1 `NotificationLog`.

### Recipient
- **Purpose:** one person/address on the tender or dev-alert list.
- **Fields (confirmed):** `id`, `email`, `name`, `role`, `list_type` (`tender` | `dev_alert`), `delivery` (`to`/`cc`/`bcc`), `source_scope[]`, `receives_filter` (all verdicts / APPLY+APPLY WITH CONDITIONS / urgent only), `alert_types[]`, `min_severity` (info/warning/critical), `active`, `created_at`, `updated_at`.
- **Constraints:** validated email on entry; dev list: refuse to deactivate/delete the **last active dev recipient**; first dev recipient seeded from an environment variable.
- **Audit:** add/edit/remove logged; `NotificationLog` snapshots actual recipients at send time.

### AlertEvent
- **Purpose:** open/recovered alert lifecycle (throttling support).
- **Fields (confirmed):** `id`, `type`, `severity`, `source_id` (optional), `correlation_id`, `state` (`open`/`recovered`), `first_raised_at`, `last_reminded_at`, `resolved_at`.
- **Constraint:** one alert on unhealthy, one on recovery, reminders at configurable interval (no per-crawl spam).

### RunHistory
- **Purpose:** per-source crawl run summary, independent of tender records.
- **Fields (confirmed):** `id`, `source_id`, `started_at`, `ended_at`, `listings_found`, `new_count`, `error_count`, `failed_correlation_ids[]`.
- **Fields (extended, migration 0002 — prompt 05 §7):** `update_count`, `unchanged_count`, `correlation_id` (run correlation ID).
- **Lifecycle:** derived, not stored — `ended_at IS NULL` → RUNNING (or crashed mid-run); `ended_at` set + `error_count = 0` → COMPLETED; `ended_at` set + `error_count > 0` → ERRORED (docs/04 §4.8).
- **Audit:** answers "when did WAHO's watcher last succeed?" at a glance.

### Settings
- **Purpose:** system-wide configuration.
- **Fields (confirmed):** `test_mode`, `urgency_window_days`, `triage_threshold`, `triage_rules`, `monthly_ai_budget`, `alert_thresholds`, `retention_months`, `link_expiry_days`.
- **Confirmations:** Test Mode **ON by default**; link expiry default 14 days [PROPOSED]; token-budget guard share default 40% [PROPOSED]. Values that remain business decisions: `triage_threshold`, `monthly_ai_budget`, `retention_months` default value, `urgency_window_days` example is 5 business days.

### ConfigChangeLog
- **Purpose:** every configuration change.
- **Fields (confirmed):** `id`, `actor`, `entity`, `entity_id`, `changed_fields` (never secret values), `created_at`.
- **Security:** by design never stores secret values.

### AdminUser
- **Purpose:** admin-app account.
- **Fields (confirmed):** `id`, `email`, `role` (`admin`/`viewer`), `active`.
- **Confirmed constraints:** viewers never see KB or secrets; login method + who gets accounts is OPEN (`docs/13-open-decisions.md`).
- **Security:** hash credentials the standard way `[PROPOSED]`; never log secrets.

---

## 3.3 Relationships summary

```text
Source 1─N Tender 1─N Document
Tender 1─N Verdict N─1 LLMProfile, N─1 KnowledgeBaseVersion
Tender 1─N NotificationLog 1─N NotificationAttempt N─1 MailProvider
Source 1─N RunHistory
Source 1─N AlertEvent (optional)
LLMRoleAssignment M─1 LLMProfile (+ fallback M─1)
LLMCall M─1 LLMProfile (via role assignment)
ConfigChangeLog M─1 (actor: AdminUser or env-seeded)
```

## 3.4 Audit requirements (applies to all)

- Correlation ID on every tender/run row and log line.
- Status (not just result) per pipeline stage.
- Retention minimum (configurable, e.g. 12 months).
- NotificationLog stores recipient snapshot + attached-vs-linked at send time.

## 3.5 Security considerations (applies to all)

- Secrets: `Source.auth`, `LLMProfile.api_key`, `MailProvider.credentials` only — encrypted at rest, master key outside DB, write-only, never logged or returned by APIs.
- KB content and verdicts: access-controlled; never sent to unapproved LLM profiles.
- Document archive: only served via signed expiring links or authenticated routes.

## 3.6 v1.0 historical differences

- v1.0 had `KnowledgeBaseDoc` (per file: doc_type, tags[], summary, storage_path, uploaded_at/by) as a v1 component and no versions. v1.1 replaced it with `KnowledgeBaseVersion`; per-file `KnowledgeBaseDoc` returns only in phase 2.
- v1.0 `Tender.status` had `new/updated/processed`; v1.1 adds `verdict_failed`, `awaiting_budget`, and `correlation_id`.
- v1.0 `Verdict` had a single `model_version`; v1.1 records `llm_profile_id`, `model`, `knowledge_base_version_id`, `prompt_version`, `incomplete_inputs`.
- v1.0 `NotificationLog` had no provider/attempt/dedupe fields; v1.1 adds `NotificationAttempt`, `dedupe_key`, `possible_duplicate`, `provider_used`, `pending_retry`.
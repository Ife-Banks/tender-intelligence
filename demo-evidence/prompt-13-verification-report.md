# Prompt 13 — Independent Behavioral Verification

Status: **NEEDS_FIXES**. The implemented Stage A behavior and its handoff seam now pass focused checks, but KB-derived defaults remain impossible to load from the current opaque KB reference, and the full repository suite has one unrelated Prompt 12.1 failure.

## A. Implementation inspected

- `src/tender_intelligence/triage/service.py`: configuration validation, keyword/sector/region/value rules, model prompt/response, threshold, retry/fallback, per-call accounting, persistence.
- `src/tender_intelligence/db/models/triage.py`, `src/tender_intelligence/db/models/config.py`, `src/tender_intelligence/db/models/llm.py`.
- `migrations/versions/0011_triage_results.py`.
- `src/tender_intelligence/audit/timeline.py`.
- `src/tender_intelligence/orchestrator/coordinator.py`, `src/tender_intelligence/orchestrator/status.py`.
- `tests/integration/test_triage_service.py`, `tests/integration/test_orchestrator_pipeline.py`, `tests/integration/test_migrations.py`, `tests/integration/test_timeline.py`.

No Stage B verdict logic, email, document-processing, crawl, or Admin API/UI behavior was added. The coordinator has an injectable pass-only downstream seam.

## B. Specification mapping

| Source | Finding |
|---|---|
| `docs/07 §7.1` | Configurable rules, optional assigned triage model, no-match pass, distinct discard/failure. KB defaults are required when available but cannot be resolved with the current KB model. |
| `docs/04 §4.2` | Coordinator runs triage after document processing at stage 6. |
| `docs/04 §4.13` | Budget guard is Stage B-owned. Stage A records LLM usage/cost for later accounting. |
| `docs/09 screen 8` | Settings remain in the shared Settings triage surface; Admin UI remains out of scope. |
| `docs/03 Settings` | Uses `Setting.triage_rules` and `Setting.triage_threshold`. |
| `docs/03 LLMRoleAssignment` | Only `triage` assignment selects model mode; no assignment is rule mode. Timeout retry and assigned fallback are supported. |
| `docs/03 LLMCall` | Each model attempt stores tender correlation, role, actual profile, token usage, latency, estimated cost, status and safe error code. Prompt text is not stored. |
| `docs/13 O5/O6/O7` | Sector, region and minimum value are open and remain unset unless explicit test configuration is supplied. |
| `docs/14 T3` | Failure is persisted as `triage_failed`; failures remain visible and do not become discard decisions. |

## C. Configuration/default results

The service uses the existing `Setting` singleton and `LLMRoleAssignment` model; no second configuration system was introduced.

- Include keywords: unset unless supplied in `triage_rules`; a match is evidence, and absence alone does not disqualify.
- Exclude keywords: unset unless configured; a match discards before other rule checks.
- Sectors, regions, minimum value: unset/open by default; explicit test values exercise these filters.
- Threshold: unset by default. Rule mode does not fabricate scores. In model mode the threshold compares the returned numeric score; missing score with an active threshold records `triage_failed`.
- KB-derived defaults: **unavailable**. `KnowledgeBaseVersion` stores `content_ref`, hash, token count and metadata, but this project has no canonical pursue/skip content schema or KB reader. No defaults are guessed.
- O5/O6/O7 remain open; no business targets are hard-coded.

## D. Behavioral test matrix

| Scenario | Expected | Actual | Pass/Fail | Evidence |
|---|---|---|---|---|
| Include keyword | Pass absent exclusion | Pass with reason | PASS | `test_rule_include_exclude_and_blank_config_pass_through` |
| Include keyword absent | No invented disqualifier | Pass by default | PASS | same |
| Exclude keyword | `triage_discarded` | Discarded | PASS | same |
| Include and exclude | Exclusion takes precedence | Discarded | PASS | same |
| Case variation | Case-insensitive | Case-insensitive matching | PASS (observed) | service inspection |
| Punctuation variation | No normalization promised | Substring matching; punctuation not normalized | PASS (observed) | service inspection |
| Sector unset/configured | Open when unset; filter when set | Open by default; mismatch discarded | PASS | `test_sector_region_and_value_only_filter_when_configured` |
| Region unset/configured | Open when unset; filter when set | Open by default; mismatch discarded | PASS | same |
| Value unset/configured | Open when unset; below minimum discarded | Open by default; numeric below minimum discarded | PASS | same |
| Value equality/above/missing/currency | Exact configured rule | Equality and above pass; missing is unavailable/pass; no currency conversion | PARTIAL | numeric match test; other boundaries inspected |
| Rule mode threshold | No fabricated score | Score remains null; no arbitrary threshold discard | PASS | `test_rule_pass_does_not_fabricate_a_relevance_score` |
| Model threshold below/equal/above | Compare score at boundary | 0.49 discards; 0.50 and 0.51 pass for threshold 0.50 | PASS | `test_model_threshold_boundaries` |
| Threshold with no model score | Cannot evaluate threshold | `triage_failed`; failed LLMCall persisted | PASS | `test_threshold_requires_model_score_and_records_failure` |
| Malformed rules | `triage_failed`, durable result | `triage_failed`, safe config code persisted | PASS | `test_malformed_rules_persist_triage_failed` |
| Triage exception | Failed, never discard | Provider exception remains failed | PASS | `test_model_failure_is_not_recorded_as_discard` |
| No triage profile | Rule mode, zero LLMCall | Rule mode, zero LLMCall | PASS | include/exclude test |
| Triage profile assigned | Assigned model and LLMCall | Assigned triage profile called and recorded | PASS | model assignment test |
| Timeout and fallback | Retry then assigned fallback | 2 retries, fallback call, all attempts recorded | PASS | `test_triage_timeout_retries_then_uses_assigned_fallback` |
| Model input privacy | Public notice fields only | Allowlist excludes arbitrary internal metadata; no KB lookup | PASS (tested scope) | model spy in model assignment test |
| Timeline/correlation | Persist result, correlation and run | Result/event include tender correlation and run ID | PASS | handoff/run-link test |
| Downstream handoff | Pass only reaches seam | 1 pass callback; 2 discards and 0 failures do not call it | PASS (seam only) | `test_triage_handoff_only_receives_pass_and_records_run_link` |
| KB-derived defaults | Use KB section if present | No KB parser/reader or content schema exists | FAIL | model/schema inspection |
| Cost accounting hook | Capture LLM use/cost | Every primary retry/fallback call writes `LLMCall`; no duplicate budget service | PASS (recording hook) | retry test and model schema |

## E. Pipeline proof

The persisted integration chain exercised is:

```text
Tender -> document processing -> Stage A -> persisted PASS/DISCARD -> pass-only handoff seam
```

With the WAHO offline fixture, configured exclusion yielded one pass, two discards, and zero evaluation failures. The injected downstream callback ran once and received the passed decision. Discarded and failed tenders do not call it. This verifies the seam without implementing Stage B.

## F. Database proof

Migrated integration databases persisted and reloaded:

- `triage_results`: tender ID, nullable run ID, tender correlation ID, status, optional score, mode, reasons, model profile/model, safe error code, timestamps.
- `llm_calls`: per-attempt role/profile, token usage, latency, estimated cost and status.
- Timeline triage event: status, tender correlation, run ID, score/mode/reasons/model and error code.

Migration assertion verifies the `run_id` column. The handoff test queries persisted rows and checks run linkage and event reconstruction. These are synthetic test records, not live business tenders.

## G. Security/logging findings

- Provider prompts and responses are not persisted in `LLMCall` or `TriageResult`.
- Provider exception messages are not copied to triage rows; safe machine error codes are stored.
- Model input uses public title and explicit metadata allowlist: `source_name`, `sector`, `region`, `contract_value`, `notice_text`. A model spy confirms arbitrary internal metadata is omitted.
- No KB content or provider credentials are read or sent by Stage A.
- Source adapters must ensure allowlisted values are public notice data.

## H. Defects fixed

- Removed fabricated rule relevance scores and arbitrary threshold decisions.
- Model threshold now applies only to legitimate model scores; missing required score persists `triage_failed`.
- Restricted model payload to public tender fields.
- Added strict triage config validation and persisted `triage_failed` for malformed rules.
- Added two timeout retries, then active assigned fallback; each attempt receives its own `LLMCall` row.
- Added triage result run linkage and surfaced it in timeline events.
- Added and tested injectable pass-only handoff seam; no verdict logic added.

## I. Remaining defects

1. **Prompt 13 design gap:** KB pursue/skip defaults cannot be implemented safely until the project specifies and exposes canonical KB content. Current `content_ref` is opaque. Do not invent content structure or business defaults.
2. Failure-path tests remain incomplete for repeated triage execution, concurrent evaluation of one tender, and A/B/C per-tender failure isolation.
3. Full regression still has one unrelated Prompt 12.1 failure: `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt`. Its string contains replacement characters and the deadline parser returns `None`. The Prompt 13 task boundary does not authorize changing that unrelated parser/fixture.

## Test results

- Focused triage/orchestrator/migration/timeline suite: **41 passed**.
- Full repository suite: **590 run, 589 passed, 1 failed**; the only failure is the unrelated French WAHO deadline fixture noted above.
- Ruff on triage service/model, timeline, changed tests and migration test: pass. (The full coordinator file has pre-existing lint findings outside this Prompt 13 edit.)
- Mypy on triage service/model: pass.
- Migration, persisted-result, retry/fallback, run-link, timeline and pass-only handoff integration tests: pass.
- Live model provider call: not attempted; model behavior used deterministic test clients.

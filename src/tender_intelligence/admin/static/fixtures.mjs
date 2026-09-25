// Offline-only demonstration data for Prompt 16A. This module never performs HTTP requests.
const now = "2026-09-25T06:30:00Z";
const tender = {
  id: 204, title: "Solar mini-grid maintenance services", source_id: 1, source_name: "WAHO fixture",
  external_id: "WAHO-DEMO-204", status: "verdict_available", correlation_id: "demo-corr-204",
  first_seen_at: "2026-09-23T09:20:00Z", updated_at: now,
  deadline: { state: "resolved", utc: "2026-10-03T16:00:00Z", timezone: "Africa/Abidjan" },
  verdict: { recommendation: "APPLY WITH CONDITIONS", urgency: true, incomplete_inputs: false },
  url: "https://example.invalid/not-a-real-notice",
};

export const DEMO_SCENARIOS = [
  ["standard", "Standard fixture"],
  ["empty", "Empty data"],
  ["error", "Server error"],
  ["unavailable", "Service unavailable"],
  ["llm-failure", "LLM test failure"],
  ["llm-unavailable", "LLM test unavailable"],
  ["mail-failure", "Mail test failure"],
];

export const FIXTURE_DATA = {
  "/health/dashboard": {
    health_status: "warning", as_of: now, verdict_counts: { "7d": 12, "30d": 41 },
    notification_failures: { "7d": 1, "30d": 3 }, stuck_notification_count: 2,
    open_alert_count: 1,
    sources: [
      { name: "WAHO fixture", active: true, last_successful_run: "2026-09-25T05:45:00Z", failure_category: null, new_tenders_7d: 6, new_tenders_30d: 22, verdicts_7d: 5, verdicts_30d: 19, notification_failures_7d: 1, notification_failures_30d: 2 },
      { name: "UNGM fixture", active: false, last_successful_run: "2026-09-22T08:00:00Z", failure_category: "source_paused", new_tenders_7d: 0, new_tenders_30d: 4, verdicts_7d: 0, verdicts_30d: 3, notification_failures_7d: 0, notification_failures_30d: 0 },
    ],
    provider_chain: [
      { name: "Sendlib fixture", active: true, breaker_state: "open" },
      { name: "SMTP fallback fixture", active: true, breaker_state: "closed" },
    ],
  },
  "/sources": { items: [
    { id: 1, name: "WAHO fixture", source_type: "waho", base_url: "https://example.invalid/waho", listing_url: "https://example.invalid/waho/list", active: true, crawl_frequency_minutes: 60, expected_languages: ["en", "fr"], recipient_scope: [], parser_config: { mode: "fixture" }, auth_configured: false, last_run_at: now, last_failure_category: null },
    { id: 2, name: "UNGM fixture", source_type: "ungm", base_url: "https://example.invalid/ungm", listing_url: "https://example.invalid/ungm/list", active: false, crawl_frequency_minutes: 240, expected_languages: ["en"], recipient_scope: [], parser_config: { mode: "fixture" }, auth_configured: true, last_run_at: "2026-09-22T08:00:00Z", last_failure_category: "source_paused" },
  ], pagination: { total: 2, offset: 0, limit: 20 } },
  "/sources/supported-types": { items: ["waho", "ungm", "tenderdetail", "all_business_africa"] },
  "/tenders": { items: [
    tender,
    { id: 205, title: "Regional cold-chain equipment supply", source_id: 1, source_name: "WAHO fixture", external_id: "WAHO-DEMO-205", status: "triage_discarded", correlation_id: "demo-corr-205", first_seen_at: "2026-09-24T10:00:00Z", updated_at: now, deadline: { state: "unresolved" }, verdict: null, urgency: false, incomplete_inputs: true },
    { id: 206, title: "District health data platform", source_id: 2, source_name: "UNGM fixture", external_id: "UNGM-DEMO-206", status: "awaiting_budget", correlation_id: "demo-corr-206", first_seen_at: "2026-09-25T04:00:00Z", updated_at: now, deadline: { state: "conflicting" }, verdict: null, urgency: false, incomplete_inputs: false },
  ], pagination: { total: 3, offset: 0, limit: 20 } },
  "/tenders/204": tender,
  "/tenders/204/timeline": { correlation_id: "demo-corr-204", run_ids: ["demo-run-204"], events: [
    { tender_id: 204, stage: "discovery", status: "succeeded", timestamp: "2026-09-23T09:20:00Z", run_id: "demo-run-204", correlation_id: "demo-corr-204" },
    { tender_id: 204, stage: "documents", status: "succeeded", timestamp: "2026-09-23T09:22:00Z", run_id: "demo-run-204", correlation_id: "demo-corr-204" },
    { tender_id: 204, stage: "triage", status: "triage_passed", timestamp: "2026-09-23T09:24:00Z", run_id: "demo-run-204", correlation_id: "demo-corr-204" },
    { tender_id: 204, stage: "verdict", status: "verdict_available", timestamp: "2026-09-23T09:25:00Z", run_id: "demo-run-204", correlation_id: "demo-corr-204" },
  ] },
  "/tenders/204/verdicts": { items: [{ id: 77, recommendation: "APPLY WITH CONDITIONS", confidence: 0.78, urgency: true, incomplete_inputs: false, generated_at: "2026-09-23T09:25:00Z" }] },
  "/knowledge-base/versions": { current: { id: 8, created_at: "2026-09-24T11:00:00Z" }, token_budget: { latest_token_count: 6840, warning_share: 0.4 }, items: [
    { id: 8, content_hash: "sha256:demo-a81f", token_count: 6840, created_at: "2026-09-24T11:00:00Z", created_by: "demo.admin", note: "Fixture capability summary" },
    { id: 7, content_hash: "sha256:demo-b729", token_count: 6320, created_at: "2026-09-10T13:30:00Z", created_by: "demo.admin", note: "Previous fixture version" },
  ] },
  "/knowledge-base/versions/8": { id: 8, content_hash: "sha256:demo-a81f", token_count: 6840, created_at: "2026-09-24T11:00:00Z", content: "DEMO CONTENT ONLY\n\nSection 1.2 — Illustrative regional delivery experience.\nSection 2.1 — Illustrative solar maintenance capability.\nNo credentials or real company records are present." },
  "/knowledge-base/versions/7": { id: 7, content_hash: "sha256:demo-b729", token_count: 6320, created_at: "2026-09-10T13:30:00Z", content: "DEMO CONTENT ONLY\n\nPrevious illustrative capability version. No real company records are present." },
  "/llm/profiles": { items: [
    { id: 1, name: "Approved evaluation profile", base_url: "https://llm.example.invalid", model: "demo-model-v2", context_window_tokens: 32000, active: true, approved_for_company_docs: true, api_key_configured: true, supports_json: true, supports_vision: false },
    { id: 2, name: "Restricted test profile", base_url: "https://test.example.invalid", model: "demo-model-lite", context_window_tokens: 8000, active: false, approved_for_company_docs: false, api_key_configured: false, supports_json: true, supports_vision: false },
  ], pagination: { total: 2, offset: 0, limit: 100 } },
  "/llm/roles": { items: [
    { role: "triage", profile_id: 2, profile: { id: 2, name: "Restricted test profile" } },
    { role: "verdict", profile_id: 1, profile: { id: 1, name: "Approved evaluation profile" }, fallback_profile_id: 2, fallback_profile: { id: 2, name: "Restricted test profile" } },
  ] },
  "/llm/usage": { calls: 42, known_cost: 18.75, cost_unknown_calls: 2, monthly_budget: null, budget_open: true },
  "/recipients": { items: [
    { id: 1, email: "tender-demo@example.test", name: "Tender Demo", role: "Operations", list_type: "tender", delivery: "to", receives_filter: "all", active: true },
    { id: 2, email: "dev-demo@example.test", name: "Development Demo", role: "QA", list_type: "dev_alert", delivery: "to", min_severity: "warning", alert_types: ["worker_failure"], active: true },
    { id: 3, email: "viewer-demo@example.test", name: "Read Only Demo", role: "Viewer", list_type: "tender", delivery: "cc", receives_filter: "urgent", active: true },
  ] },
  "/mail/providers": { items: [
    { id: 1, name: "Sendlib fixture", provider_type: "sendlib", priority: 1, active: true, capabilities: { html: true, attachments: true }, breaker_state: "open", credentials_configured: true, from_address: "not-a-real-sender@example.test", reply_to: "" },
    { id: 2, name: "SMTP fallback fixture", provider_type: "smtp", priority: 2, active: true, capabilities: { html: true, attachments: false }, breaker_state: "closed", credentials_configured: false, from_address: "not-a-real-sender@example.test", reply_to: "" },
  ] },
  "/triage": { include_keywords: ["solar", "health"], exclude_keywords: ["weapons"], sectors: null, regions: null, minimum_contract_value: null, relevance_threshold: null, urgency_window_days: 10 },
  "/settings": { test_mode: true, updated_at: now, version: 4, monthly_ai_budget: null, retention_months: 12, link_expiry_days: 14, alert_thresholds: { kb_token_warning_share: 0.4 } },
  "/audit": { items: [
    { id: 90, created_at: "2026-09-24T11:00:00Z", actor: "demo.admin", entity: "KnowledgeBaseVersion", entity_id: 8, action: "created", category: "configuration", result: "success", correlation_id: "demo-config-90", changed_fields: ["content_hash", "note"] },
    { id: 89, created_at: "2026-09-20T10:00:00Z", actor: "demo.admin", entity: "Setting", entity_id: 1, action: "updated", category: "configuration", result: "success", correlation_id: "demo-config-89", changed_fields: ["retention_months"] },
  ], pagination: { total: 2, offset: 0, limit: 20 } },
};

export function cloneFixture(value) {
  return structuredClone(value);
}

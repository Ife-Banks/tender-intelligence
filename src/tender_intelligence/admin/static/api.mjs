/**
 * Admin API client — Prompt 16B.
 *
 * Exports two implementations behind the same interface:
 *
 *   LiveAdminApi   — real HTTP to /api/v1/* with auth headers, error classification,
 *                    timeout/abort, and correlation IDs.  Used when the browser is
 *                    served by the FastAPI admin process.
 *
 *   FixtureAdminApi — in-memory fixture client kept for offline preview and unit tests.
 *                     (Previously named AdminApi; tests continue to use the export alias.)
 *
 * createApi() auto-detects which to return based on a probe of /api/v1/sources/supported-types.
 *
 * Security constraints (docs/09 §9.5, docs/10):
 *   - Full secret values are never stored, logged, or placed in URLs.
 *   - Credentials/API keys are submitted once, write-only.
 *   - Auth tokens are kept only in module-level state (not localStorage/sessionStorage).
 *   - Raw server error text is never surfaced; only classified codes + safe messages.
 *   - Correlation/request IDs are surfaced for ops tracing, never as raw stack traces.
 */

import { cloneFixture, DEMO_SCENARIOS, FIXTURE_DATA } from "./fixtures.mjs";

// ── Safe user-facing messages ─────────────────────────────────────────────────

const SAFE_MESSAGES = {
  // Live API error codes
  authentication_required: "Sign in to access the administration console.",
  authorization_denied: "Your account does not have permission for this operation.",
  not_found: "The requested record was not found.",
  source_name_conflict: "A source with that name already exists.",
  llm_profile_name_conflict: "An LLM profile with that name already exists.",
  mail_provider_name_conflict: "A mail provider with that name already exists.",
  recipient_email_conflict: "That recipient address is already configured.",
  last_dev_recipient: "Keep at least one active development/alert recipient configured.",
  profile_in_use: "This LLM profile is assigned to a role and cannot be deleted.",
  inactive_profile: "The selected profile is inactive and cannot be assigned.",
  role_path_mismatch: "The role in the URL must match the role in the request body.",
  source_dry_run_unavailable: "Source dry-run is unavailable (coordinator not wired at startup).",
  source_test_failed: "The source test failed. Check source configuration and network access.",
  source_dry_run_contract_violation: "Source test returned unexpected persisted state.",
  llm_test_unavailable: "LLM connection test is unavailable (no LLM client factory configured).",
  provider_test_failed: "The LLM connection test failed. Check provider configuration.",
  test_mode_required: "Test Mode must be ON to send test email.",
  active_dev_recipient_required: "An active development/alert recipient is required.",
  test_email_service_unavailable: "Test email service is unavailable.",
  test_email_failed: "Test email could not be sent. Check mail provider configuration.",
  mail_provider_test_unavailable: "Mail provider test is unavailable.",
  mail_provider_test_failed: "Mail provider test failed.",
  empty_update: "No changes were submitted.",
  test_mode_reason_requires_test_mode_change: "Provide test_mode together with test_mode_reason.",
  test_mode_off_requires_reason: "A reason is required when turning Test Mode OFF.",
  validation_error: "Check the highlighted configuration values.",
  database_unavailable: "The database is unavailable. Check server status.",
  secret_encryption_unavailable: "Secret encryption is unavailable.",
  knowledge_base_version_exists: "This document version already exists.",
  knowledge_base_storage_failed: "Knowledge base storage is unavailable.",
  knowledge_base_extraction_failed: "The file could not be processed.",
  knowledge_base_extraction_empty: "The file produced no extractable text.",
  invalid_base64: "The file encoding is invalid.",
  invalid_filename: "The filename is not safe.",
  unsupported_file_type: "Unsupported file type. Use .docx, .pdf, .md, or .txt.",
  file_too_large: "The file exceeds the maximum allowed size.",
  malformed_file: "The file appears to be malformed.",
  unsupported_source_type: "That source type is not supported. See the supported-types list.",
  internal_error: "An unexpected server error occurred. Please try again or contact support.",
  network_error: "Could not reach the server. Check your connection and try again.",
  request_timeout: "The request timed out. The server may be busy.",
  // Fixture-only codes
  demo_server_error: "The fixture server returned a simulated error.",
  demo_unavailable: "The selected fixture service is unavailable.",
  demo_llm_failure: "The simulated model connection failed.",
  demo_llm_unavailable: "The simulated model test is unavailable.",
  demo_mail_failure: "The simulated mail test failed. No email was sent.",
  invalid_email: "Enter a valid email address.",
};

/**
 * Structured API error with enough context for display and retry logic.
 * Never carries raw response bodies, stack traces, or secret values.
 */
export class ApiError extends Error {
  /**
   * @param {object} opts
   * @param {number}   opts.status      HTTP status (or 0 for network failure)
   * @param {string}   opts.code        Machine-readable error code
   * @param {Array}    [opts.fields]    Field-level validation errors [{field, message}]
   * @param {string}   [opts.requestId] X-Request-ID / correlation_id from the server
   */
  constructor({ status = 500, code = "internal_error", fields = [], requestId = null } = {}) {
    const message = SAFE_MESSAGES[code] ?? "The operation could not be completed.";
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = Array.isArray(fields) ? fields : [];
    this.requestId = requestId;
    this.retryable = status === 503 || status === 0;
  }
}

// ── LiveAdminApi ──────────────────────────────────────────────────────────────

const API_BASE = "/api/v1";
const REQUEST_TIMEOUT_MS = 30_000;

// Auth state: kept in module scope, never in browser storage.
// In dev/test the API accepts X-Test-Actor / X-Test-Role headers.
// In production an actual session cookie / Authorization header is provided by
// the actor_resolver installed on app.state.  The UI sends whatever headers
// the server currently accepts; the server is authoritative.
let _testActor = null;
let _testRole = null;

/**
 * Configure development test-auth headers.  Only effective when the server was
 * started with TI_ADMIN_ENABLE_TEST_AUTH=1 in a non-production environment.
 */
export function configureTestAuth(actor, role) {
  _testActor = actor;
  _testRole = role;
}

function _authHeaders() {
  const h = {};
  if (_testActor) {
    h["X-Test-Actor"] = _testActor;
    h["X-Test-Role"] = _testRole || "viewer";
  }
  return h;
}

async function _request(method, path, body, signal) {
  const url = `${API_BASE}${path}`;
  const init = {
    method,
    headers: { ..._authHeaders(), "Accept": "application/json" },
    signal,
    credentials: "same-origin",
  };
  if (body !== undefined && body !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    if (err.name === "AbortError") throw new ApiError({ status: 0, code: "request_timeout" });
    throw new ApiError({ status: 0, code: "network_error" });
  }

  // 204 No Content
  if (response.status === 204) return null;

  let data;
  try {
    data = await response.json();
  } catch {
    if (!response.ok) throw new ApiError({ status: response.status, code: "internal_error" });
    return null;
  }

  if (!response.ok) {
    // Extract structured error — never expose raw detail strings that may contain secrets.
    const err = data?.error ?? data?.detail ?? {};
    const code = typeof err === "object" ? (err.code ?? "internal_error") : "internal_error";
    const fields = Array.isArray(err.fields) ? err.fields.map((f) => ({
      field: (f.location ?? []).join("."),
      message: String(f.message ?? "Invalid value"),
    })) : [];
    const requestId = response.headers.get("X-Correlation-ID") ?? response.headers.get("X-Request-ID") ?? err.correlation_id ?? null;
    throw new ApiError({ status: response.status, code, fields, requestId });
  }
  return data;
}

function _withTimeout(fn) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  return fn(controller.signal).finally(() => clearTimeout(timer));
}

export class LiveAdminApi {
  /**
   * Connection state: "live" | "offline" | "auth_required" | "forbidden"
   */
  constructor() {
    this.connectionState = "live";
    // Keep a reference to the current authenticated actor's role so the UI
    // can gate Admin-only screens without an extra round-trip.
    this.role = "viewer";
  }

  async _call(method, path, body) {
    try { return await _withTimeout((signal) => _request(method, path, body, signal)); }
    catch (error) {
      if (error instanceof ApiError && error.status === 401) this.connectionState = "auth_required";
      throw error;
    }
  }

  async get(path) { return this._call("GET", path); }
  async post(path, body = {}) { return this._call("POST", path, body); }
  async put(path, body = {}) { return this._call("PUT", path, body); }
  async patch(path, body = {}) { return this._call("PATCH", path, body); }
  async delete(path) { return this._call("DELETE", path); }

  async uploadKnowledgeBase(file, note = null) {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let start = 0; start < bytes.length; start += 0x8000) {
      binary += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
    }
    return this.post("/knowledge-base/versions", {
      filename: file.name,
      content_base64: btoa(binary),
      note,
    });
  }

  /** Probe the API to confirm connectivity and extract role from the response. */
  async probe() {
    try {
      await this.get("/sources/supported-types");
      this.connectionState = "live";
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) this.connectionState = "auth_required";
        else if (err.status === 403) this.connectionState = "forbidden";
        else if (err.status === 0 || err.status === 404) this.connectionState = "offline";
        else this.connectionState = "live"; // server up but different error
      } else {
        this.connectionState = "offline";
      }
    }
    return this.connectionState;
  }

  /** Detect current actor role by reading settings (viewer sees fewer fields). */
  async detectRole() {
    try {
      const data = await this.get("/settings");
      // Admin sees monthly_ai_budget; viewer gets null from the API.
      // A better signal: try a lightweight admin-only endpoint.
      // We use /audit (admin-only) as the role probe.
      await this.get("/audit?offset=0&limit=1");
      this.role = "admin";
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) this.role = "viewer";
      else this.role = "viewer"; // conservative
    }
    return this.role;
  }
}

// ── FixtureAdminApi (offline preview / unit tests) ────────────────────────────

const page = (items, offset = 0, limit = 20) => ({
  items: items.slice(offset, offset + limit),
  pagination: { total: items.length, offset, limit },
});
const matchId = (path, expression) => path.match(expression)?.[1];

export class FixtureAdminApi {
  constructor() {
    this.scenario = "standard";
    this.role = "admin";
    this.data = cloneFixture(FIXTURE_DATA);
    this.nextId = 300;
    // Compatibility: same connectionState field so the UI can check uniformly.
    this.connectionState = "fixture";
  }

  setScenario(scenario) {
    if (!DEMO_SCENARIOS.some(([value]) => value === scenario)) throw new TypeError("Unknown demo scenario.");
    this.scenario = scenario;
  }

  setRole(role) {
    if (!["admin", "viewer"].includes(role)) throw new TypeError("Unknown demo role.");
    this.role = role;
  }

  async get(path) { return this.request("GET", path); }
  async post(path, body = {}) { return this.request("POST", path, body); }
  async put(path, body = {}) { return this.request("PUT", path, body); }
  async patch(path, body = {}) { return this.request("PATCH", path, body); }
  async delete(path) { return this.request("DELETE", path); }

  async request(method, rawPath, body = {}) {
    const url = new URL(rawPath, "https://fixture.invalid");
    if (url.origin !== "https://fixture.invalid") throw new TypeError("Fixture paths must be relative.");
    if (this.scenario === "error") throw new ApiError({ status: 500, code: "demo_server_error" });
    if (this.scenario === "unavailable") throw new ApiError({ status: 503, code: "demo_unavailable" });

    const path = url.pathname;
    if (method === "GET") return this.read(path, url.searchParams);
    if (this.role !== "admin") throw new ApiError({ status: 403, code: "authorization_denied" });
    if (path === "/sources" && method === "POST") return this.create("/sources", body);
    if (path === "/knowledge-base/versions" && method === "POST") return this.uploadKbFixture(body);
    if (path === "/llm/profiles" && method === "POST") return this.saveProfile(null, body);
    if (path === "/recipients" && method === "POST") return this.create("/recipients", body);
    if (path === "/mail/providers" && method === "POST") return this.create("/mail/providers", body);
    if (path === "/mail/test" && method === "POST") return this.testMail();

    const sourceId = matchId(path, /^\/sources\/(\d+)$/);
    if (sourceId && method === "PUT") return this.update("/sources", sourceId, body);
    const sourceActive = matchId(path, /^\/sources\/(\d+)\/active$/);
    if (sourceActive && method === "PATCH") return this.update("/sources", sourceActive, body);
    const sourceTest = matchId(path, /^\/sources\/(\d+)\/test$/);
    if (sourceTest && method === "POST") return { status: "dry_run_complete", dry_run: true, persisted: false, email_sent: false, candidate_count: 3, tenders_considered: 2, correlation_id: `demo-source-${sourceTest}`, stages: { "04-discovery": "COMPLETED", "05-dedup": "COMPLETED" }, planned_actions: ["Would evaluate 3 synthetic candidates", "Would create no records", "Would send no email"] };

    const profileId = matchId(path, /^\/llm\/profiles\/(\d+)$/);
    if (profileId && method === "PUT") return this.saveProfile(profileId, body);
    if (profileId && method === "DELETE") {
      const idx = this.collection("/llm/profiles").findIndex((e) => e.id === Number(profileId));
      if (idx < 0) throw new ApiError({ status: 404, code: "not_found" });
      this.data["/llm/profiles"].items.splice(idx, 1);
      return null;
    }
    const profileTest = matchId(path, /^\/llm\/profiles\/(\d+)\/test$/);
    if (profileTest && method === "POST") return this.testLlm();
    if (/^\/llm\/roles\/[^/]+$/.test(path) && method === "PUT") {
      const entries = this.data["/llm/roles"].items;
      const existing = entries.find((entry) => entry.role === body.role);
      const profile = this.profile(body.profile_id);
      const fallback = body.fallback_profile_id ? this.profile(body.fallback_profile_id) : null;
      const record = { role: body.role, profile_id: Number(body.profile_id), profile: profile && { id: profile.id, name: profile.name }, fallback_profile_id: body.fallback_profile_id || null, fallback_profile: fallback && { id: fallback.id, name: fallback.name } };
      existing ? Object.assign(existing, record) : entries.push(record);
      return cloneFixture(record);
    }

    const recipientId = matchId(path, /^\/recipients\/(\d+)$/);
    if (recipientId && method === "PUT") return this.update("/recipients", recipientId, body);
    if (recipientId && method === "DELETE") return this.deleteRecipient(recipientId);
    const providerId = matchId(path, /^\/mail\/providers\/(\d+)$/);
    if (providerId && method === "PUT") return this.update("/mail/providers", providerId, body);
    const providerActive = matchId(path, /^\/mail\/providers\/(\d+)\/active$/);
    if (providerActive && method === "PATCH") return this.update("/mail/providers", providerActive, body);
    if (/^\/mail\/providers\/\d+\/test$/.test(path) && method === "POST") return this.testMail();
    if (path === "/triage" && method === "PUT") return this.replaceObject(path, body);
    if (path === "/settings" && method === "PUT") return this.updateSettings(body);
    throw new ApiError({ status: 404, code: "not_found" });
  }

  read(path, query) {
    if (path === "/tenders") {
      let items = this.collection(path);
      if (this.scenario === "empty") items = [];
      for (const key of ["source_id", "status"]) if (query.has(key)) items = items.filter((item) => String(item[key]) === query.get(key));
      if (query.has("recommendation")) items = items.filter((item) => item.verdict?.recommendation === query.get("recommendation"));
      if (query.has("search")) items = items.filter((item) => `${item.title} ${item.external_id}`.toLowerCase().includes(query.get("search").toLowerCase()));
      return page(items, Number(query.get("offset") || 0), Number(query.get("limit") || 20));
    }
    if (["/sources", "/llm/profiles", "/audit"].includes(path)) {
      const values = this.scenario === "empty" ? [] : this.collection(path);
      return page(values, Number(query.get("offset") || 0), Number(query.get("limit") || (path === "/llm/profiles" ? 100 : 20)));
    }
    if (path === "/recipients") {
      let values = this.collection(path);
      if (query.has("list_type")) values = values.filter((item) => item.list_type === query.get("list_type"));
      if (this.scenario === "empty") values = [];
      return { items: cloneFixture(values) };
    }
    const detail = matchId(path, /^\/tenders\/(\d+)$/);
    if (detail) {
      const found = this.data["/tenders"].items.find((item) => item.id === Number(detail));
      if (!found) throw new ApiError({ status: 404, code: "not_found" });
      return cloneFixture(found);
    }
    const timelineId = matchId(path, /^\/tenders\/(\d+)\/timeline$/);
    if (timelineId) return cloneFixture(this.data[`/tenders/${timelineId}/timeline`] || { correlation_id: "demo-corr-205", run_ids: [], events: [] });
    const verdictId = matchId(path, /^\/tenders\/(\d+)\/verdicts$/);
    if (verdictId) return cloneFixture(this.data[`/tenders/${verdictId}/verdicts`] || { items: [] });
    const kbVersion = matchId(path, /^\/knowledge-base\/versions\/(\d+)$/);
    if (kbVersion) return cloneFixture(this.data[`/knowledge-base/versions/${kbVersion}`]);
    if (path === "/knowledge-base/diff") return { diff: ["--- version 7", "+++ version 8", "+Illustrative solar maintenance capability."] };
    if (this.scenario === "empty") return this.emptyValue(path);
    if (Object.hasOwn(this.data, path)) return cloneFixture(this.data[path]);
    throw new ApiError({ status: 404, code: "not_found" });
  }

  collection(path) { return this.data[path]?.items || []; }

  emptyValue(path) {
    if (path === "/health/dashboard") return { health_status: "unknown", as_of: new Date().toISOString(), verdict_counts: { "7d": 0, "30d": 0 }, notification_failures: { "7d": 0, "30d": 0 }, stuck_notification_count: 0, open_alert_count: 0, sources: [], provider_chain: [] };
    if (path === "/mail/providers" || path === "/llm/roles") return { items: [] };
    if (path === "/llm/usage") return { calls: 0, known_cost: null, cost_unknown_calls: 0, monthly_budget: null, budget_open: true };
    if (path === "/knowledge-base/versions") return { current: null, token_budget: { latest_token_count: null, warning_share: null }, items: [] };
    if (path === "/triage") return { include_keywords: null, exclude_keywords: null, sectors: null, regions: null, minimum_contract_value: null, relevance_threshold: null, urgency_window_days: null };
    if (path === "/settings") return cloneFixture(this.data[path]);
    return cloneFixture(this.data[path] || {});
  }

  create(path, body) {
    if (path === "/recipients" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(body.email || "")) throw new ApiError({ status: 422, code: "invalid_email" });
    const record = { id: this.nextId++, active: true, ...body };
    if (path === "/sources" && Object.hasOwn(record, "auth")) { record.auth_configured = Boolean(record.auth); delete record.auth; }
    if (path === "/llm/profiles" && Object.hasOwn(record, "api_key")) { record.api_key_configured = Boolean(record.api_key); delete record.api_key; }
    if (path === "/mail/providers" && Object.hasOwn(record, "credentials")) { record.credentials_configured = Boolean(record.credentials); delete record.credentials; }
    this.collection(path).push(record);
    return cloneFixture(record);
  }

  update(path, id, changes) {
    const record = this.collection(path).find((entry) => entry.id === Number(id));
    if (!record) throw new ApiError({ status: 404, code: "not_found" });
    if (path === "/recipients" && record.active && changes.active === false && record.list_type === "dev_alert" && this.collection(path).filter((entry) => entry.list_type === "dev_alert" && entry.active).length <= 1) throw new ApiError({ status: 409, code: "last_dev_recipient" });
    if (path === "/sources" && Object.hasOwn(changes, "auth")) { record.auth_configured = Boolean(changes.auth); delete changes.auth; }
    if (path === "/mail/providers" && Object.hasOwn(changes, "credentials")) { record.credentials_configured = Boolean(changes.credentials); delete changes.credentials; }
    if (path === "/llm/profiles" && Object.hasOwn(changes, "api_key")) { record.api_key_configured = Boolean(changes.api_key) || record.api_key_configured; delete changes.api_key; }
    Object.assign(record, changes);
    return cloneFixture(record);
  }

  deleteRecipient(id) {
    const values = this.collection("/recipients");
    const record = values.find((entry) => entry.id === Number(id));
    if (!record) throw new ApiError({ status: 404, code: "not_found" });
    if (record.active && record.list_type === "dev_alert" && values.filter((entry) => entry.active && entry.list_type === "dev_alert").length <= 1) throw new ApiError({ status: 409, code: "last_dev_recipient" });
    this.data["/recipients"].items = values.filter((entry) => entry.id !== Number(id));
    return null;
  }

  saveProfile(id, body) {
    const record = id ? this.collection("/llm/profiles").find((entry) => entry.id === Number(id)) : { id: this.nextId++, active: true };
    if (!record) throw new ApiError({ status: 404, code: "not_found" });
    const secretSet = Boolean(body.api_key);
    const safe = { ...body };
    delete safe.api_key;
    Object.assign(record, safe);
    if (secretSet) record.api_key_configured = true;
    if (!id) this.collection("/llm/profiles").push(record);
    return cloneFixture(record);
  }

  profile(id) { return this.collection("/llm/profiles").find((entry) => entry.id === Number(id)); }

  replaceObject(path, body) { this.data[path] = { ...this.data[path], ...cloneFixture(body) }; return cloneFixture(this.data[path]); }

  updateSettings(body) {
    this.data["/settings"] = { ...this.data["/settings"], ...cloneFixture(body), version: (this.data["/settings"].version || 0) + 1, updated_at: new Date().toISOString() };
    return cloneFixture(this.data["/settings"]);
  }

  uploadKbFixture({ filename, note }) {
    const items = this.data["/knowledge-base/versions"].items;
    const id = this.nextId++;
    const now = new Date().toISOString();
    const record = { id, content_hash: `sha256:demo-${id}`, token_count: 0, created_at: now, created_by: "demo.admin", note: note || `Simulated upload: ${filename}` };
    items.unshift(record);
    this.data["/knowledge-base/versions"].current = { id, created_at: now };
    this.data[`/knowledge-base/versions/${id}`] = { ...record, content: `DEMO ONLY — simulated metadata for ${filename}. File contents were not read or uploaded.` };
    return cloneFixture(record);
  }

  testLlm() {
    if (this.scenario === "llm-failure") throw new ApiError({ status: 502, code: "demo_llm_failure" });
    if (this.scenario === "llm-unavailable") throw new ApiError({ status: 503, code: "demo_llm_unavailable" });
    return { status: "simulated_success", provider: "Fixture provider", model: "demo-model-v2", latency_ms: 84, supports_json: true, supports_vision: false };
  }

  testMail() {
    if (this.scenario === "mail-failure") throw new ApiError({ status: 502, code: "demo_mail_failure" });
    return { status: "simulated_success", provider: "Fixture mail provider", correlation_id: "demo-mail-001", notification_log_id: null, email_sent: false };
  }

  // Probe always succeeds for the fixture client.
  async probe() { return "fixture"; }
  async detectRole() { return this.role; }
}

// Backward-compat export alias so existing tests (`new AdminApi()`) keep working.
export { FixtureAdminApi as AdminApi };

// ── Factory ───────────────────────────────────────────────────────────────────

/**
 * Probe the server and return a LiveAdminApi if reachable, otherwise FixtureAdminApi.
 *
 * @param {object} [opts]
 * @param {string} [opts.testActor]  X-Test-Actor header value for dev auth
 * @param {string} [opts.testRole]   X-Test-Role header value for dev auth
 * @returns {Promise<LiveAdminApi|FixtureAdminApi>}
 */
export async function createApi({ testActor = null, testRole = null } = {}) {
  if (testActor) configureTestAuth(testActor, testRole || "viewer");
  const live = new LiveAdminApi();
  const state = await live.probe();
  if (state === "offline") {
    // Could not reach the server at all — fall back to fixtures.
    return new FixtureAdminApi();
  }
  // Server is reachable (may be auth_required/forbidden — UI handles that state).
  if (state === "live" || state === "auth_required") {
    if (state === "live") await live.detectRole();
    return live;
  }
  return live;
}

export { DEMO_SCENARIOS };

import test from "node:test";
import assert from "node:assert/strict";
import { ApiError, createApi, LiveAdminApi } from "../../src/tender_intelligence/admin/static/api.mjs";

const originalFetch = globalThis.fetch;

test("live requests use same-origin credentials and the versioned Admin API", async () => {
  let captured;
  globalThis.fetch = async (url, init) => {
    captured = { url, init };
    return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const api = new LiveAdminApi();
    await api.get("/sources");
    assert.equal(captured.url, "/api/v1/sources");
    assert.equal(captured.init.credentials, "same-origin");
    assert.equal(captured.init.headers.Accept, "application/json");
  } finally { globalThis.fetch = originalFetch; }
});

test("POST, PATCH, and DELETE use centralized JSON/204 handling", async () => {
  const captured = [];
  globalThis.fetch = async (url, init) => {
    captured.push({ url, init });
    return init.method === "DELETE"
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "content-type": "application/json" } });
  };
  try {
    const api = new LiveAdminApi();
    await api.post("/sources", { name: "fixture" });
    await api.patch("/sources/7/active", { active: false });
    await api.delete("/llm/profiles/5");
    assert.deepEqual(captured.map(({ init }) => init.method), ["POST", "PATCH", "DELETE"]);
    assert.deepEqual(JSON.parse(captured[0].init.body), { name: "fixture" });
    assert.deepEqual(JSON.parse(captured[1].init.body), { active: false });
    assert.equal(captured[2].url, "/api/v1/llm/profiles/5");
  } finally { globalThis.fetch = originalFetch; }
});

test("authentication failure stays live and never falls back to fixture data", async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({ detail: { code: "authentication_required" } }), { status: 401, headers: { "content-type": "application/json" } });
  };
  try {
    const api = await createApi();
    assert.ok(api instanceof LiveAdminApi);
    assert.equal(api.connectionState, "auth_required");
    assert.ok(calls >= 1);
  } finally { globalThis.fetch = originalFetch; }
});

test("missing API route selects labelled fixture preview, but forbidden remains a live API state", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: "not_found" } }), { status: 404, headers: { "content-type": "application/json" } });
  try { assert.equal((await createApi()).connectionState, "fixture"); }
  finally { globalThis.fetch = originalFetch; }
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: "authorization_denied" } }), { status: 403, headers: { "content-type": "application/json" } });
  try {
    const api = await createApi();
    assert.ok(api instanceof LiveAdminApi);
    assert.equal(api.connectionState, "forbidden");
    assert.equal(api.role, "viewer");
  } finally { globalThis.fetch = originalFetch; }
});

test("403, 404, 409, 429 and 5xx codes are retained without exposing raw detail", async () => {
  const raw = "DO_NOT_SHOW_RAW_SERVER_TEXT";
  for (const [status, code] of [[403, "authorization_denied"], [404, "not_found"], [409, "last_dev_recipient"], [429, "rate_limited"], [503, "database_unavailable"]]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code, raw } }), { status, headers: { "content-type": "application/json" } });
    const api = new LiveAdminApi();
    await assert.rejects(api.get("/fixture"), (error) => error instanceof ApiError && error.status === status && error.code === code && !error.message.includes(raw));
  }
  globalThis.fetch = originalFetch;
});

test("server validation errors are classified without echoing rejected secret values", async () => {
  const sentinel = "UI_SECRET_SENTINEL_1d4f";
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: "validation_error", fields: [{ location: ["body", "api_key"], message: "invalid value" }] }, debug: sentinel }), { status: 422, headers: { "content-type": "application/json" } });
  try {
    const api = new LiveAdminApi();
    await assert.rejects(api.post("/llm/profiles", { api_key: sentinel }), (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, "validation_error");
      assert.equal(JSON.stringify(error).includes(sentinel), false);
      assert.equal(error.message.includes(sentinel), false);
      return true;
    });
  } finally { globalThis.fetch = originalFetch; }
});

test("KB upload sends actual file bytes in the Prompt 15 base64 request schema", async () => {
  let captured;
  globalThis.fetch = async (_url, init) => {
    captured = init;
    return new Response(JSON.stringify({ id: 3 }), { status: 201, headers: { "content-type": "application/json" } });
  };
  try {
    const api = new LiveAdminApi();
    await api.uploadKnowledgeBase(new File(["safe fixture text"], "fixture.txt"), "UI integration fixture");
    const request = JSON.parse(captured.body);
    assert.equal(request.filename, "fixture.txt");
    assert.equal(request.content_base64, btoa("safe fixture text"));
    assert.equal(request.note, "UI integration fixture");
  } finally { globalThis.fetch = originalFetch; }
});

test("204 responses return null and server bodies are never included in errors", async () => {
  const sentinel = "RAW_SERVER_DETAIL_SENTINEL";
  let status = 204;
  globalThis.fetch = async () => status === 204
    ? new Response(null, { status: 204 })
    : new Response(JSON.stringify({ detail: sentinel }), { status: 500, headers: { "content-type": "application/json" } });
  try {
    const api = new LiveAdminApi();
    assert.equal(await api.delete("/recipients/4"), null);
    status = 500;
    await assert.rejects(api.delete("/recipients/4"), (error) => error.message.includes(sentinel) === false);
  } finally { globalThis.fetch = originalFetch; }
});

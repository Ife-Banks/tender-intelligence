import test from "node:test";
import assert from "node:assert/strict";
import { AdminApi, ApiError } from "../../src/tender_intelligence/admin/static/api.mjs";

test("fixtures load without a network client and never call fetch", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("network access must not occur"); };
  try {
    const api = new AdminApi();
    assert.equal((await api.get("/settings")).test_mode, true);
    assert.equal((await api.get("/tenders?offset=0&limit=20")).items.length, 3);
  } finally { globalThis.fetch = originalFetch; }
});

test("empty scenario returns empty lists without mutating the base fixtures", async () => {
  const api = new AdminApi();
  api.setScenario("empty");
  assert.deepEqual((await api.get("/sources")).items, []);
  assert.deepEqual((await api.get("/llm/profiles")).items, []);
  assert.deepEqual((await api.get("/tenders")).items, []);
  api.setScenario("standard");
  assert.equal((await api.get("/sources")).items.length, 2);
  assert.equal((await api.get("/llm/profiles")).items.length, 2);
});

test("server error and unavailable scenarios return safe, classified errors", async () => {
  const api = new AdminApi();
  api.setScenario("error");
  await assert.rejects(api.get("/settings"), (error) => error instanceof ApiError && error.status === 500 && !error.message.includes("secret"));
  api.setScenario("unavailable");
  await assert.rejects(api.get("/settings"), (error) => error instanceof ApiError && error.status === 503 && error.retryable);
});

test("provider tests are simulations and do not send email", async () => {
  const api = new AdminApi();
  const result = await api.post("/mail/test", {});
  assert.equal(result.status, "simulated_success");
  assert.equal(result.email_sent, false);
  assert.equal(result.notification_log_id, null);
  api.setScenario("mail-failure");
  await assert.rejects(api.post("/mail/test", {}), /No email was sent/);
});

test("LLM connection test success, failure, and unavailable outcomes are deterministic fixtures", async () => {
  const api = new AdminApi();
  assert.equal((await api.post("/llm/profiles/1/test", {})).status, "simulated_success");
  api.setScenario("llm-failure");
  await assert.rejects(api.post("/llm/profiles/1/test", {}), (error) => error.status === 502);
  api.setScenario("llm-unavailable");
  await assert.rejects(api.post("/llm/profiles/1/test", {}), (error) => error.status === 503);
});

test("source test is a dry-run fixture with no persistence or mail side effect", async () => {
  const api = new AdminApi();
  const result = await api.post("/sources/1/test", {});
  assert.equal(result.dry_run, true);
  assert.equal(result.persisted, false);
  assert.equal(result.email_sent, false);
  assert.deepEqual(result.planned_actions, ["Would evaluate 3 synthetic candidates", "Would create no records", "Would send no email"]);
});

test("recipient validation and last development recipient protection are enforced locally", async () => {
  const api = new AdminApi();
  await assert.rejects(api.post("/recipients", { email: "invalid", list_type: "dev_alert" }), (error) => error.code === "invalid_email");
  await assert.rejects(api.delete("/recipients/2"), (error) => error.code === "last_dev_recipient");
});

test("Viewer preview cannot mutate fixture configuration", async () => {
  const api = new AdminApi();
  api.setRole("viewer");
  await assert.rejects(api.put("/settings", { test_mode: false }), (error) => error instanceof ApiError && error.status === 403);
  await assert.rejects(api.delete("/recipients/1"), (error) => error instanceof ApiError && error.status === 403);
  assert.equal((await api.get("/settings")).test_mode, true);
});

test("secret fields are reduced to configured flags and never returned", async () => {
  const api = new AdminApi();
  const sentinel = "DO_NOT_ECHO_UI_TEST_SECRET_73b1";
  const created = await api.post("/llm/profiles", { name: "Temporary", model: "demo", api_key: sentinel });
  assert.equal(created.api_key, undefined);
  assert.equal(created.api_key_configured, true);
  assert.equal(JSON.stringify(created).includes(sentinel), false);
  assert.equal(JSON.stringify(await api.get("/llm/profiles")).includes(sentinel), false);
});

test("KB upload simulation accepts filename metadata only", async () => {
  const api = new AdminApi();
  const result = await api.post("/knowledge-base/versions", { filename: "synthetic.pdf", note: "Preview" });
  assert.equal(result.note, "Preview");
  const detail = await api.get(`/knowledge-base/versions/${result.id}`);
  assert.match(detail.content, /File contents were not read or uploaded/);
  assert.equal(JSON.stringify(detail).includes("base64"), false);
});

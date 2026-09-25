import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../../src/tender_intelligence/admin/static/app.mjs", import.meta.url), "utf8");
const api = await readFile(new URL("../../src/tender_intelligence/admin/static/api.mjs", import.meta.url), "utf8");
const html = await readFile(new URL("../../src/tender_intelligence/admin/static/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../../src/tender_intelligence/admin/static/styles.css", import.meta.url), "utf8");

test("all ten Admin UI destinations have renderers and use the live API factory", () => {
  const screens = ["health", "sources", "tenders", "knowledge-base", "llm", "recipients", "mail", "triage", "settings", "audit"];
  const renderers = app.match(/const renderers = \{([\s\S]*?)\n    \};/)?.[1] || "";
  for (const screen of screens) {
    assert.match(app, new RegExp(`\\["${screen}"`), `${screen} is in navigation`);
    assert.match(renderers, new RegExp(`(?:"${screen}"|${screen}):\\s*render`), `${screen} has a page renderer`);
  }
  assert.match(app, /api = await createApi\(\)/);
  assert.match(app, /await renderers\[state\.page\]\(\)/);
  assert.match(app, /api\.uploadKnowledgeBase\(file/);
  assert.match(html, /id="main-content"/);
});

test("live shell does not present fixture controls or claim that all actions are simulations", () => {
  assert.match(html, /id="demo-banner"[^>]*hidden/);
  assert.match(html, /id="demo-role-control"[^>]*hidden/);
  assert.match(html, /id="scenario-control"[^>]*hidden/);
  assert.match(app, /Authentication required/);
  assert.match(app, /state\.live = api instanceof LiveAdminApi/);
  assert.match(app, /Run source dry run/);
  assert.match(app, /Send a \[TEST\] email/);
  assert.doesNotMatch(app, /api\.data\["\/recipients"\]/);
});

test("UI security guardrails avoid dynamic HTML, browser storage, and inline styles", () => {
  assert.doesNotMatch(app, /\b(?:innerHTML|outerHTML|insertAdjacentHTML)\b/);
  assert.doesNotMatch(app, /\b(?:localStorage|sessionStorage)\b/);
  assert.doesNotMatch(app, /\.style\s*\./);
  assert.doesNotMatch(app, /\bfetch\s*\(/);
  assert.doesNotMatch(html, /\sstyle\s*=/i);
  assert.doesNotMatch(css, /@import\s+url\(/i);
  assert.match(html, /<meta name="color-scheme" content="light"/);
  assert.match(css, /prefers-reduced-motion/);
});

test("Viewer restrictions remain server-authoritative and offline role is visibly a preview", () => {
  assert.match(api, /detectRole\(\)/);
  assert.match(app, /state\.role !== "admin"/);
  assert.match(app, /Preview role · \$\{role\}/);
  assert.match(app, /Turn Test Mode OFF\?/);
});

test("sensitive actions use native labelled dialogs and accessible status regions", () => {
  assert.match(html, /<dialog id="confirm-dialog"[^>]*aria-labelledby="confirm-title"/);
  assert.match(html, /<dialog id="editor-dialog"[^>]*aria-labelledby="editor-title"/);
  assert.match(html, /id="global-message"[^>]*aria-live="polite"/);
  assert.match(html, /class="skip-link" href="#main-content"/);
});

test("responsive and reduced-motion rules are defined", () => {
  assert.match(css, /@media\s*\(max-width:\s*760px\)/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/);
  assert.match(css, /:focus-visible/);
});

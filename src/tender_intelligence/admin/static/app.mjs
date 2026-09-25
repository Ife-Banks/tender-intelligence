import { ApiError, createApi, DEMO_SCENARIOS, LiveAdminApi, FixtureAdminApi } from "./api.mjs";

let api = new FixtureAdminApi(); // replaced by createApi() on DOMContentLoaded
const $ = (selector) => document.querySelector(selector);
const content = $("#screen-content");
const navElement = $("#primary-nav");
const messageElement = $("#global-message");
const confirmDialog = $("#confirm-dialog");

const NAV = [
  ["health", "Health dashboard", "activity"],
  ["sources", "Sources", "globe"],
  ["tenders", "Tenders", "file"],
  ["knowledge-base", "Knowledge base", "book", "admin"],
  ["llm", "LLM providers", "spark"],
  ["recipients", "Recipients", "users"],
  ["mail", "Mail providers", "mail"],
  ["triage", "Triage & urgency", "filter"],
  ["settings", "Settings", "settings"],
  ["audit", "Audit log", "list", "admin"],
];
const ICON_PATHS = {
  activity: [{ d: "M3 12h4l3-8 4 16 3-8h4" }],
  globe: [{ tag: "circle", cx: 12, cy: 12, r: 9 }, { d: "M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" }],
  file: [{ d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }, { d: "M14 2v6h6M8 13h8M8 17h8" }],
  book: [{ d: "M4 19.5A2.5 2.5 0 0 1 6.5 17H20" }, { d: "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" }],
  spark: [{ d: "m12 3 1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2L12 3z" }, { d: "m19 14 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3z" }],
  users: [{ d: "M16 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" }, { tag: "circle", cx: 10, cy: 7, r: 4 }, { d: "M20 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" }],
  mail: [{ tag: "rect", x: 3, y: 5, width: 18, height: 14, rx: 2 }, { d: "m3 7 9 6 9-6" }],
  filter: [{ d: "M4 5h16M7 12h10m-7 7h4" }],
  settings: [{ tag: "circle", cx: 12, cy: 12, r: 3 }, { d: "m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.6a8 8 0 0 1-1.7 1l-.3 1.8h-2.8l-.3-1.8a8 8 0 0 1-1.7-1l-1.7.6-1.4-2.4L7.3 15a8 8 0 0 1 0-2l-1.4-1.1 1.4-2.4 1.7.6a8 8 0 0 1 1.7-1l.3-1.8h2.8l.3 1.8a8 8 0 0 1 1.7 1l1.7-.6 1.4 2.4-1.4 1.1a8 8 0 0 1 0 2z" }],
  list: [{ d: "M8 6h13M8 12h13M8 18h13" }, { d: "M3 6h.01M3 12h.01M3 18h.01" }],
};
const TITLES = Object.fromEntries(NAV.map(([key, title]) => [key, title]));
const DESCRIPTIONS = {
  health: "Operational status and activity from the Admin API.",
  sources: "Review configured sources and run an explicitly confirmed dry run.",
  tenders: "Inspect persisted notices, verdict status, and pipeline timelines.",
  "knowledge-base": "Review Knowledge Base versions and change history.",
  llm: "Review LLM profiles, role assignments, approval, and usage.",
  recipients: "Manage tender and development/alert recipient lists.",
  mail: "Inspect mail provider configuration and run explicitly marked test operations.",
  triage: "Review shared Stage A rules and urgency configuration.",
  settings: "Manage shared system settings and Test Mode.",
  audit: "Review persisted configuration history with secrets omitted.",
};
const state = {
  role: "admin",
  page: routeFromHash(),
  offset: 0,
  pageSize: 20,
  filters: {},
  dirty: false,
  activeTenderId: null,
  live: false,   // true when api is a LiveAdminApi connected to the real server
};

function isLive() { return state.live; }

/**
 * Return a human-readable label that distinguishes live vs. offline mode.
 * Never exposes session tokens, URLs, or internal server details.
 */
function connectionLabel() {
  if (!state.live) return "Offline fixture mode";
  const cs = api.connectionState;
  if (cs === "auth_required") return "Authentication required";
  if (cs === "forbidden") return "Access denied";
  return "Connected · live data";
}

function routeFromHash() {
  const key = location.hash.replace(/^#\/?/, "").split("/")[0];
  return TITLES[key] ? key : "health";
}

function node(tag, attrs = {}, children = []) {
  const result = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") result.className = value;
    else if (key === "text") result.textContent = String(value);
    else if (key === "checked") result.checked = Boolean(value);
    else if (key === "disabled") result.disabled = Boolean(value);
    else if (key === "value") result.value = value;
    else if (key === "hidden") result.hidden = Boolean(value);
    else result.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of (Array.isArray(children) ? children : [children])) {
    if (child === null || child === undefined || child === false) continue;
    result.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return result;
}

function setText(selector, text) {
  $(selector).textContent = text;
}

function showMessage(kind, message) {
  messageElement.className = `global-message ${kind}`;
  messageElement.textContent = message;
  messageElement.hidden = !message;
}

function errorMessage(error) {
  if (error instanceof ApiError) {
    const extras = [];
    if (error.fields.length) extras.push(error.fields.map((field) => `${field.field || "Field"}: ${field.message}`).join("; "));
    if (error.requestId) extras.push(`Request ID: ${error.requestId}`);
    extras.push(`Error code: ${error.code}`);
    return [error.message, ...extras].join(" ");
  }
  return "The screen could not be loaded. Please retry or contact your system administrator.";
}

function errorPanel(error, retry) {
  const box = node("section", { class: "card card-pad error-state", role: "alert" });
  box.append(node("h2", { text: "This information is unavailable" }));
  box.append(node("p", { class: "muted", text: errorMessage(error) }));
  if (retry) box.append(button("Retry", retry, "button button-quiet button-small"));
  if (error instanceof ApiError && error.status === 503) {
    box.append(node("p", { class: "small-muted", text: `Service code: ${error.code}` }));
  }
  return box;
}

function authRequiredPanel() {
  return node("section", { class: "card card-pad error-state", role: "alert" }, [
    node("h2", { text: "Authentication required" }),
    node("p", { class: "muted", text: "Your session is missing or has expired. The Admin API rejected this request. Sign in through the configured identity provider, then refresh this page." }),
  ]);
}

function accessDeniedPanel() {
  return node("section", { class: "card card-pad error-state", role: "alert" }, [
    node("h2", { text: "Access denied" }),
    node("p", { class: "muted", text: "The Admin API denied access to this console. Ask an authorized administrator to review your account role." }),
  ]);
}

function loadingPanel(label = "Loading Admin API data…") {
  return node("div", { class: "card loading-state", role: "status", "aria-live": "polite" }, [
    node("div", { class: "loading-bar", "aria-hidden": "true" }),
    node("p", { text: label }),
  ]);
}

function emptyPanel(message) {
  return node("div", { class: "empty-state" }, message);
}

function button(label, action, className = "button button-quiet", options = {}) {
  const result = node("button", {
    type: "button",
    class: className,
    disabled: options.disabled,
    title: options.title,
  }, label);
  if (action) result.addEventListener("click", async (event) => {
    if (result.disabled) return;
    result.disabled = true;
    try { await action(event); }
    finally { if (result.isConnected) result.disabled = false; }
  });
  return result;
}

function linkButton(label, href, className = "button button-quiet button-small") {
  try {
    const url = new URL(href, location.origin);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    return node("a", { class: className, href: url.href, target: "_blank", rel: "noopener noreferrer" }, label);
  } catch { return null; }
}

function section(title, description = "") {
  const result = node("section", { class: "section" });
  result.append(node("div", { class: "section-heading" }, [
    node("div", {}, [node("h2", { text: title }), description && node("p", { class: "card-subtitle", text: description })]),
  ]));
  return result;
}

function card(title, children, description = "") {
  const result = node("section", { class: "card card-pad" });
  result.append(node("h2", { text: title }));
  if (description) result.append(node("p", { class: "card-subtitle", text: description }));
  const body = node("div", { class: "stack card-content" });
  for (const child of children) if (child) body.append(child);
  result.append(body);
  return result;
}

function metric(label, value, note = "") {
  return node("article", { class: "card card-pad" }, [
    node("div", { class: "metric-label", text: label }),
    node("div", { class: "metric-value", text: value ?? "—" }),
    node("div", { class: "metric-note", text: note }),
  ]);
}

function pill(value, kind = "") {
  const text = value === null || value === undefined || value === "" ? "Unavailable" : String(value);
  return node("span", { class: `pill ${kind}` }, [
    node("span", { class: "pill-dot", "aria-hidden": "true" }), text,
  ]);
}

function formatDate(value) {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}

function jsonText(value, fallback = "—") {
  if (value === null || value === undefined) return fallback;
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return fallback; }
}

function makeTable(headers, rows) {
  if (!rows.length) return emptyPanel("No records are available for this view.");
  const table = node("table", { class: "data-table" });
  const thead = node("thead");
  const headRow = node("tr");
  for (const title of headers) headRow.append(node("th", { scope: "col", text: title }));
  thead.append(headRow);
  const tbody = node("tbody");
  for (const row of rows) {
    const tr = node("tr");
    for (const cell of row) tr.append(node("td", {}, cell instanceof Node ? cell : String(cell ?? "—")));
    tbody.append(tr);
  }
  table.append(thead, tbody);
  return node("div", { class: "table-wrap" }, table);
}

function pagination(data, onPage) {
  const total = data?.pagination?.total ?? 0;
  const offset = data?.pagination?.offset ?? state.offset;
  const limit = data?.pagination?.limit ?? state.pageSize;
  const count = data?.items?.length ?? 0;
  const start = total ? offset + 1 : 0;
  const end = Math.min(offset + count, total);
  return node("div", { class: "pagination" }, [
    node("span", { text: `${start}–${end} of ${total}` }),
    node("div", { class: "table-actions" }, [
      button("Previous", () => onPage(Math.max(0, offset - limit)), "button button-quiet button-small", { disabled: offset <= 0 }),
      button("Next", () => onPage(offset + limit), "button button-quiet button-small", { disabled: offset + count >= total }),
    ]),
  ]);
}

function field(label, type, value, options = {}) {
  const wrapper = node("label", { class: `field ${options.wide ? "wide" : ""}` });
  wrapper.append(node("span", {}, [label, options.optional && node("span", { class: "optional", text: " optional" })]));
  let input;
  if (type === "textarea" || type === "json" || type === "lines") {
    const rendered = type === "json" && value !== null && value !== undefined
      ? jsonText(value, "")
      : type === "lines" && Array.isArray(value) ? value.join("\n") : value ?? "";
    input = node("textarea", { name: options.name, rows: options.rows || (type === "json" ? 5 : 4), placeholder: options.placeholder || "" }, rendered);
  } else if (type === "select") {
    input = node("select", { name: options.name });
    for (const choice of options.choices || []) {
      const option = node("option", { value: choice.value, selected: String(choice.value) === String(value ?? "") }, choice.label);
      input.append(option);
    }
  } else {
    input = node("input", {
      name: options.name,
      type,
      accept: options.accept,
      value: value ?? "",
      placeholder: options.placeholder || "",
      accept: options.accept,
      min: options.min,
      max: options.max,
      step: options.step,
      autocomplete: type === "password" ? "new-password" : "off",
      spellcheck: options.spellcheck === false ? "false" : null,
    });
    if (type === "checkbox") input.checked = Boolean(value);
  }
  if (options.required) input.required = true;
  if (options.disabled) input.disabled = true;
  if (options.help) wrapper.append(node("small", { class: "field-help", text: options.help }));
  wrapper.append(input);
  return wrapper;
}

function formFieldValue(form, key, config) {
  const input = form.elements.namedItem(key);
  if (!input) return undefined;
  if (config.type === "checkbox") return input.checked;
  if (config.type === "number") {
    if (input.value === "") return null;
    const number = Number(input.value);
    return Number.isFinite(number) ? number : null;
  }
  if (config.type === "json") {
    if (!input.value.trim()) return null;
    try { return JSON.parse(input.value); } catch { throw new Error(`${config.label} must be valid JSON.`); }
  }
  if (config.type === "lines") {
    return input.value.split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
  }
  const value = input.value.trim();
  return value === "" ? null : value;
}

function setDirtyFrom(form) {
  const original = form.dataset.original || "";
  const current = new FormData(form);
  const values = {};
  for (const [key, value] of current.entries()) values[key] = value;
  state.dirty = JSON.stringify(values) !== original;
  return state.dirty;
}

let confirmResolver = null;
function askConfirm(title, message, confirmLabel = "Confirm", dangerous = true) {
  setText("#confirm-title", title);
  setText("#confirm-message", message);
  const submit = $("#confirm-submit");
  submit.textContent = confirmLabel;
  submit.className = dangerous ? "button button-danger" : "button button-primary";
  confirmDialog.showModal();
  return new Promise((resolve) => { confirmResolver = resolve; });
}

$("#confirm-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const answer = event.submitter?.value === "confirm";
  confirmDialog.close();
  confirmResolver?.(answer);
  confirmResolver = null;
});
$("#confirm-form").querySelector('[value="cancel"]').addEventListener("click", () => {
  confirmDialog.close();
  confirmResolver?.(false);
  confirmResolver = null;
});
confirmDialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  confirmDialog.close();
  confirmResolver?.(false);
  confirmResolver = null;
});

function setDemoRole(role) {
  state.role = role;
  if (api instanceof FixtureAdminApi) api.setRole(role);
  $("#actor-role").textContent = `Preview role · ${role}`;
  setText("#connection-label", connectionLabel());
  renderNavigation();
  if ((state.page === "knowledge-base" || state.page === "audit") && role !== "admin") {
    state.page = "health";
    location.hash = "#/health";
  }
  renderCurrentPage();
}

function renderNavigation() {
  navElement.replaceChildren();
  const list = node("div", { class: "nav-list" });
  for (const [key, title, iconName, role] of NAV) {
    if (role === "admin" && state.role !== "admin") continue;
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    for (const shape of ICON_PATHS[iconName] || []) {
      const element = document.createElementNS("http://www.w3.org/2000/svg", shape.tag || "path");
      for (const [attribute, value] of Object.entries(shape)) {
        if (attribute !== "tag") element.setAttribute(attribute, String(value));
      }
      icon.append(element);
    }
    const anchor = node("a", {
      class: "nav-link",
      href: `#/${key}`,
      "aria-current": state.page === key ? "page" : null,
    }, [node("span", { class: "nav-icon", "aria-hidden": "true" }, icon), node("span", { text: title })]);
    list.append(anchor);
  }
  navElement.append(list);
}

function navigate(page) {
  if (!TITLES[page]) page = "health";
  if (state.dirty && page !== state.page) {
    return askConfirm("Discard unsaved changes?", "Your current demo edits have not been saved.", "Discard edits", true).then((ok) => {
      if (!ok) { location.hash = `#/${state.page}`; return; }
      state.dirty = false;
      state.page = page;
      state.activeTenderId = page === "tenders" ? state.activeTenderId : null;
      location.hash = `#/${page}`;
      renderNavigation();
      return renderCurrentPage().then(() => $("#main-content").focus({ preventScroll: true }));
    });
  }
  state.page = page;
  state.offset = 0;
  state.activeTenderId = page === "tenders" ? state.activeTenderId : null;
  renderNavigation();
  setText("#page-title", TITLES[page]);
  setText("#page-description", DESCRIPTIONS[page]);
  if (location.hash !== `#/${page}`) location.hash = `#/${page}`;
  return renderCurrentPage().then(() => $("#main-content").focus({ preventScroll: true }));
}

window.addEventListener("hashchange", () => {
  const page = routeFromHash();
  if (page !== state.page) navigate(page);
});
window.addEventListener("beforeunload", (event) => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ""; }
});
$("#refresh-button").addEventListener("click", async () => {
  if (state.dirty && !await askConfirm("Discard unsaved changes?", "Refreshing this screen discards local edits to fixture data.", "Discard and refresh", true)) return;
  state.dirty = false;
  await renderCurrentPage();
});
$("#demo-role").addEventListener("change", (event) => setDemoRole(event.target.value));
const scenarioSelect = $("#demo-scenario");
for (const [value, label] of DEMO_SCENARIOS) scenarioSelect.append(node("option", { value }, label));
scenarioSelect.addEventListener("change", async () => {
  if (state.dirty && !await askConfirm("Discard unsaved changes?", "Changing preview scenarios discards local edits to fixture data.", "Discard edits", true)) {
    scenarioSelect.value = api.scenario;
    return;
  }
  api.setScenario(scenarioSelect.value);
  state.dirty = false;
  showMessage("info", `Demo scenario changed to ${scenarioSelect.selectedOptions[0].textContent}. No real services are connected.`);
  renderCurrentPage();
});

async function renderCurrentPage() {
  state.dirty = false;
  setText("#page-title", TITLES[state.page] || "Health dashboard");
  setText("#page-description", DESCRIPTIONS[state.page] || "");
  syncTestModeIndicator();
  if (api instanceof LiveAdminApi && api.connectionState === "auth_required") {
    content.replaceChildren(authRequiredPanel());
    return;
  }
  if (api instanceof LiveAdminApi && api.connectionState === "forbidden") {
    content.replaceChildren(accessDeniedPanel());
    return;
  }
  if ((state.page === "knowledge-base" || state.page === "audit") && state.role !== "admin") {
    content.replaceChildren(node("section", { class: "card card-pad", role: "alert" }, [
      node("h2", { text: "Forbidden" }),
      node("p", { class: "muted", text: "Knowledge Base and audit data are restricted to Admin users." }),
    ]));
    return;
  }
  if (!NAV.some(([key, , , role]) => key === state.page && (role !== "admin" || state.role === "admin"))) {
    content.replaceChildren(node("section", { class: "card card-pad", role: "alert" }, "This screen is not available for your role."));
    return;
  }
  content.setAttribute("aria-busy", "true");
  content.replaceChildren(loadingPanel());
  try {
    const renderers = {
      health: renderHealth,
      sources: renderSources,
      tenders: renderTenders,
      "knowledge-base": renderKnowledgeBase,
      llm: renderLlm,
      recipients: renderRecipients,
      mail: renderMail,
      triage: renderTriage,
      settings: renderSettings,
      audit: renderAudit,
    };
    await renderers[state.page]();
  } catch (error) {
    if (api instanceof LiveAdminApi && api.connectionState === "auth_required") {
      state.role = "viewer";
      setText("#connection-label", connectionLabel());
      setText("#actor-role", "Role · signed out");
      renderNavigation();
      content.replaceChildren(authRequiredPanel());
    } else content.replaceChildren(errorPanel(error, () => renderCurrentPage()));
  } finally {
    content.setAttribute("aria-busy", "false");
  }
}

async function renderHealth() {
  const data = await api.get("/health/dashboard");
  const healthKind = data.health_status === "ok" ? "success" : data.health_status === "warning" ? "warning" : "danger";
  const contentNodes = [];
  if ((data.stuck_notification_count || 0) > 0) {
    contentNodes.push(node("div", { class: "sticky-banner", role: "alert" }, `ACTION REQUIRED · ${data.stuck_notification_count} notification queue item(s) are stuck.`));
  }
  if ((data.open_alert_count || 0) > 0) {
    contentNodes.push(node("div", { class: "notice notice-danger", role: "alert" }, `There are ${data.open_alert_count} open alert(s).`));
  }
  contentNodes.push(node("div", { class: "grid grid-4" }, [
    metric("System status", data.health_status || "Unavailable", `As of ${formatDate(data.as_of)}`),
    metric("Verdicts · 7 days", data.verdict_counts?.["7d"] ?? "Unavailable", "Persisted verdict records"),
    metric("Notification failures · 7 days", data.notification_failures?.["7d"] ?? "Unavailable", `30 days: ${data.notification_failures?.["30d"] ?? "Unavailable"}`),
    metric("Stuck notification queue", data.stuck_notification_count ?? "Unavailable", `Open alerts: ${data.open_alert_count ?? "Unavailable"}`),
  ]));
  const sources = data.sources || [];
  contentNodes.push(section("Source health", "Per-source run status and activity from persisted run history."));
  const sourceRows = sources.map((source) => [
    node("strong", { text: source.name }),
    pill(source.active ? "Active" : "Paused", source.active ? "success" : "warning"),
    formatDate(source.last_successful_run),
    source.failure_category || "No recorded failure",
    `${source.new_tenders_7d ?? "—"} / ${source.new_tenders_30d ?? "—"}`,
    `${source.verdicts_7d ?? "—"} / ${source.verdicts_30d ?? "—"}`,
    `${source.notification_failures_7d ?? "—"} / ${source.notification_failures_30d ?? "—"}`,
  ]);
  contentNodes.push(makeTable(["Source", "State", "Last successful run", "Last failure category", "New tenders 7d / 30d", "Verdicts 7d / 30d", "Mail failures 7d / 30d"], sourceRows));
  contentNodes.push(section("Mail provider chain", "Configured state only; this dashboard does not contact providers."));
  contentNodes.push(makeTable(["Provider", "State", "Circuit breaker"], (data.provider_chain || []).map((provider) => [
    provider.name, pill(provider.active ? "Active" : "Inactive", provider.active ? "success" : "warning"), pill(provider.breaker_state, provider.breaker_state === "closed" ? "success" : "warning"),
  ])));
  content.replaceChildren(...contentNodes);
  const statusCard = content.querySelector(".metric-value");
  if (statusCard && healthKind === "danger") statusCard.closest(".card")?.classList.add("metric-danger");
}

function syncTestModeIndicator() {
  // For live API we don't have in-memory data, so only sync for fixtures.
  // The health/settings screens always reload from the API which will update this.
  if (api instanceof FixtureAdminApi) {
    const active = Boolean(api.data["/settings"]?.test_mode);
    const badge = $("#test-mode-status");
    badge.textContent = active ? "TEST MODE · ON" : "TEST MODE · OFF";
    badge.className = `status-chip ${active ? "status-chip-warning" : "status-chip-danger"}`;
  }
  // For live API the indicator is updated after the settings screen loads.
}

function syncTestModeFromData(testMode) {
  const badge = $("#test-mode-status");
  badge.textContent = testMode ? "TEST MODE · ON" : "TEST MODE · OFF";
  badge.className = `status-chip ${testMode ? "status-chip-warning" : "status-chip-danger"}`;
}

function editorDialog({ title, description = "", fields, initial = {}, onSave, saveLabel = "Save", approvalConfirm = false }) {
  const dialog = $("#editor-dialog");
  const form = $("#editor-form");
  form.replaceChildren();
  const error = node("div", { class: "form-error", role: "alert", hidden: true });
  const grid = node("div", { class: "form-grid" });
  for (const config of fields) {
    grid.append(field(config.label, config.type || "text", initial[config.name] ?? config.defaultValue ?? "", {
      name: config.name,
      required: config.required,
      optional: !config.required,
      wide: config.wide,
      choices: config.choices,
      rows: config.rows,
      help: config.help,
      placeholder: config.placeholder,
      min: config.min,
      max: config.max,
      step: config.step,
      spellcheck: config.spellcheck,
    }));
  }
  const cancel = button("Cancel", () => closeEditor(false), "button button-quiet");
  const save = node("button", { type: "submit", class: "button button-primary", text: saveLabel });
  form.append(node("div", { class: "dialog-heading" }, [node("div", {}, [node("p", { class: "eyebrow", text: "CONFIGURATION" }), node("h2", { id: "editor-title", text: title }), node("p", { id: "editor-description", class: "muted", text: description })]), button("×", () => closeEditor(false), "icon-button", { title: "Close" })]), grid, error, node("div", { class: "dialog-actions" }, [cancel, save]));
  form.dataset.original = JSON.stringify(initial);
  form.dataset.initial = JSON.stringify(initial);
  form.addEventListener("input", () => { state.dirty = true; }, { once: false });
  dialog.oncancel = (event) => {
    event.preventDefault();
    closeEditor(false);
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    error.hidden = true;
    save.disabled = true;
    try {
      const payload = {};
      for (const config of fields) {
        const value = formFieldValue(form, config.name, config);
        if (value !== undefined) payload[config.name] = value;
      }
      if (approvalConfirm && payload.approved_for_company_docs !== Boolean(initial.approved_for_company_docs)) {
        const confirmed = await askConfirm("Confirm company-document approval", "This profile may receive sensitive OPEX company knowledge in verdict requests. Only approve a provider after the organization's data-handling review.", "Confirm approval change", true);
        if (!confirmed) return;
      }
      await onSave(payload);
      state.dirty = false;
      form.reset();
      form.querySelectorAll('input[type="password"]').forEach((input) => { input.value = ""; });
      dialog.close();
    } catch (failure) {
      error.textContent = failure instanceof Error ? errorMessage(failure) : "The change could not be saved.";
      error.hidden = false;
    } finally {
      save.disabled = false;
    }
  };
  dialog.showModal();
  const first = form.querySelector("input,textarea,select");
  first?.focus();
  return dialog;
}

async function closeEditor(force) {
  const dialog = $("#editor-dialog");
  if (!force && state.dirty) {
    const discard = await askConfirm("Discard unsaved changes?", "The values in this form have not been saved to a real system.", "Discard changes", true);
    if (!discard) return;
  }
  state.dirty = false;
  $("#editor-form").reset();
  dialog.close();
}

function sourceFields(sourceTypes) {
  return [
    { name: "name", label: "Source name", required: true },
    { name: "source_type", label: "Source type", type: "select", required: true, choices: sourceTypes.map((type) => ({ value: type, label: type })) },
    { name: "base_url", label: "Base URL", required: true, help: "Must match the selected adapter's supported source configuration." },
    { name: "listing_url", label: "Listing URL" },
    { name: "crawl_frequency_minutes", label: "Crawl frequency (minutes)", type: "number", min: 1, step: 1 },
    { name: "expected_languages", label: "Expected languages (JSON array)", type: "json", help: "Example: [\"en\", \"fr\"]", wide: true },
    { name: "recipient_scope", label: "Recipient scope (JSON array of IDs)", type: "json", wide: true },
    { name: "parser_config", label: "Adapter parser configuration (JSON)", type: "json", wide: true, help: "The adapter owns this schema. Unsupported settings are not interpreted by the UI." },
    { name: "auth", label: "Source auth configuration (JSON)", type: "json", wide: true, help: "Write-only. Leave empty when not changing credentials." },
    { name: "active", label: "Source enabled", type: "checkbox" },
  ];
}

async function renderSources() {
  const [data, typesData] = await Promise.all([api.get(`/sources?offset=${state.offset}&limit=${state.pageSize}`), api.get("/sources/supported-types")]);
  const top = node("div", { class: "toolbar" });
  if (state.role === "admin") top.append(button("＋ Add source", () => openSourceEditor(typesData.items || []), "button button-primary"));
  top.append(node("span", { class: "small-muted", text: `${data.pagination?.total ?? 0} configured source(s)` }));
  const rows = (data.items || []).map((source) => [
    node("div", {}, [node("strong", { text: source.name }), node("div", { class: "small-muted", text: `${source.source_type} · ${source.base_url}` })]),
    pill(source.active ? "Enabled" : "Disabled", source.active ? "success" : "warning"),
    source.crawl_frequency_minutes ? `${source.crawl_frequency_minutes} min` : "Not configured",
    formatDate(source.last_run_at),
    source.last_error ? "Failure recorded" : "None recorded",
    source.auth_configured ? "Configured · value hidden" : "Not configured",
    node("div", { class: "table-actions" }, [
      button("Edit", () => openSourceEditor(typesData.items || [], source), "button button-quiet button-small", { disabled: state.role !== "admin" }),
      state.role === "admin" && button(source.active ? "Disable" : "Enable", () => toggleSource(source), "button button-quiet button-small"),
      state.role === "admin" && button("Test dry run", () => testSource(source), "button button-primary button-small"),
    ]),
  ]);
  content.replaceChildren(top, makeTable(["Source", "State", "Schedule", "Last run", "Last failure", "Credentials", "Actions"], rows));
  content.append(pagination(data, (offset) => { state.offset = offset; renderCurrentPage(); }));
  if (state.role === "viewer") content.append(node("p", { class: "small-muted", text: "Source tests and configuration changes are available to Admin users only." }));
}

function openSourceEditor(types, source = null) {
  const initial = source ? { ...source, auth: null } : { active: true, expected_languages: [], recipient_scope: [], parser_config: null, auth: null };
  editorDialog({
    title: source ? `Edit ${source.name}` : "Add source",
    description: isLive()
      ? "Changes are written to the database and audit-logged. The worker picks them up on its next scheduled run."
      : "In offline preview, edits change synthetic fixture data only. Nothing is saved to a worker or shared configuration.",
    fields: sourceFields(types),
    initial,
    onSave: async (payload) => {
      if (source && payload.auth === null) delete payload.auth;
      if (source && payload.listing_url === null) payload.listing_url = null;
      if (source) await api.put(`/sources/${source.id}`, payload);
      else await api.post("/sources", payload);
      showMessage("success", isLive() ? "Source configuration saved to the database." : "Demo source configuration saved in browser memory only.");
      await renderCurrentPage();
    },
  });
}

async function toggleSource(source) {
  const active = !source.active;
  if (!await askConfirm(`${active ? "Enable" : "Disable"} source`, `${source.name} will be ${active ? "enabled" : "disabled"} in shared configuration.`, active ? "Enable source" : "Disable source", !active)) return;
  try { await api.patch(`/sources/${source.id}/active`, { active }); showMessage("success", isLive() ? "Source state saved." : "Offline fixture source state updated."); await renderCurrentPage(); }
  catch (error) { showMessage("error", errorMessage(error)); }
}

async function testSource(source) {
  if (isLive() && !await askConfirm("Run source dry run", `The configured adapter for ${source.name} may contact its source website to fetch the listing. It will not persist tender/run records or send email.`, "Run dry run", false)) return;
  const resultCard = node("section", { class: "card card-pad stack", role: "status", "aria-live": "polite" }, [
    node("span", { class: "pill info", text: "DRY RUN / TEST — no production writes and no email" }),
    node("h2", { text: `Testing ${source.name}` }),
    loadingPanel(isLive() ? "Running configured source adapter in dry-run mode…" : "Running the synthetic source dry-run…"),
  ]);
  content.replaceChildren(resultCard);
  try {
    const result = await api.post(`/sources/${source.id}/test`, {});
    const details = node("div", { class: "detail-grid" });
    for (const [label, value] of Object.entries({
      Status: result.status,
      Source: result.source_name,
      "Candidates found": result.candidate_count ?? result.candidates_found,
      "Tenders considered": result.tenders_considered,
      "Correlation ID": result.correlation_id,
      "Database writes": result.persisted ? "Unexpectedly persisted" : "None · dry run",
      "Email sent": result.email_sent ? "Unexpectedly sent" : "No",
      "Error category": result.error_code,
    })) details.append(node("div", { class: "detail-item" }, [node("span", { text: label }), node("strong", { text: value ?? "Unavailable" })]));
    resultCard.replaceChildren(node("span", { class: "pill info", text: "DRY RUN / TEST — no production writes and no email" }), node("h2", { text: `Source test · ${result.status}` }), details);
    resultCard.append(node("h3", { text: "Stage status" }), node("pre", { class: "code-block" }, jsonText(result.stages)));
    resultCard.append(node("h3", { text: "Planned actions" }), node("pre", { class: "code-block" }, jsonText(result.planned_actions, "No planned actions returned")));
    resultCard.append(node("p", { class: "small-muted", text: isLive() ? "The configured source adapter ran; no tender/run records or email were persisted." : "Synthetic candidates only; no external service was contacted." }));
    resultCard.append(button("Back to sources", () => navigate("sources"), "button button-quiet"));
  } catch (error) {
    resultCard.replaceChildren(node("span", { class: "pill danger", text: "Dry run failed" }), node("h2", { text: source.name }), node("p", { class: "muted", role: "alert", text: errorMessage(error) }), button("Retry", () => testSource(source), "button button-quiet"), button("Back to sources", () => navigate("sources"), "button button-quiet"));
  }
}

async function renderTenders() {
  const params = new URLSearchParams({ offset: String(state.offset), limit: String(state.pageSize) });
  for (const key of ["source_id", "status", "recommendation"]) if (state.filters[key]) params.set(key, state.filters[key]);
  const data = await api.get(`/tenders?${params}`);
  const filters = node("form", { class: "toolbar" });
  filters.append(field("Source ID", "number", state.filters.source_id, { name: "source_id", min: 1, step: 1 }));
  filters.append(field("Search title / ID", "search", state.filters.search, { name: "search", placeholder: "Search loaded notices" }));
  filters.append(field("Status", "text", state.filters.status, { name: "status", placeholder: "Any status" }));
  filters.append(field("Recommendation", "select", state.filters.recommendation, { name: "recommendation", choices: [{ value: "", label: "Any verdict" }, ...["APPLY", "DO NOT APPLY", "APPLY WITH CONDITIONS"].map((v) => ({ value: v, label: v }))] }));
  filters.append(node("button", { class: "button button-primary", type: "submit", text: "Apply filters" }));
  filters.addEventListener("submit", (event) => {
    event.preventDefault();
    state.filters = Object.fromEntries(new FormData(filters).entries());
    state.offset = 0;
    renderCurrentPage();
  });
  const rows = (data.items || []).filter((tender) => !state.filters.search || `${tender.title} ${tender.external_id || ""}`.toLowerCase().includes(state.filters.search.toLowerCase())).map((tender) => [
    node("div", {}, [button(tender.title, () => openTender(tender.id), "button button-quiet button-small"), node("div", { class: "small-muted", text: `${tender.source_name || `Source ${tender.source_id}`} · ${tender.external_id || "No external ID"}` })]),
    pill(tender.status || "Unknown"),
    tender.deadline?.state === "resolved" ? `${formatDate(tender.deadline.utc)} · ${tender.deadline.timezone || "timezone unavailable"}` : (tender.deadline?.state || "Unresolved"),
    tender.urgency ?? tender.verdict?.urgency ? pill("Urgent", "warning") : pill("Not urgent"),
    tender.verdict ? tender.verdict.recommendation : "No verdict returned",
    tender.incomplete_inputs === undefined ? "Not provided" : tender.incomplete_inputs ? pill("Incomplete inputs", "warning") : pill("Complete inputs", "success"),
    tender.correlation_id || "Unavailable",
  ]);
  content.replaceChildren(filters, makeTable(["Tender", "Status", "Deadline", "Urgency", "Verdict", "Input completeness", "Correlation ID"], rows));
  content.append(pagination(data, (offset) => { state.offset = offset; renderCurrentPage(); }));
}

async function openTender(tenderId) {
  state.activeTenderId = tenderId;
  content.replaceChildren(loadingPanel("Loading synthetic tender and timeline…"));
  try {
    const [tender, timeline] = await Promise.all([api.get(`/tenders/${tenderId}`), api.get(`/tenders/${tenderId}/timeline`)]);
    if (state.activeTenderId !== tenderId) return;
    const details = node("div", { class: "detail-grid" });
    for (const [label, value] of Object.entries({
      "Tender ID": tender.id,
      "External ID": tender.external_id,
      Source: tender.source_name,
      Status: tender.status,
      "First seen": formatDate(tender.first_seen_at),
      "Updated": formatDate(tender.updated_at),
      "Deadline state": tender.deadline?.state,
      "Deadline UTC": tender.deadline?.utc,
      "Source timezone": tender.deadline?.timezone,
      "Correlation ID": tender.correlation_id,
      "Urgency": tender.urgency ?? tender.verdict?.urgency ? "Urgent" : "Not urgent",
      "Input completeness": tender.incomplete_inputs === undefined ? "Not provided" : tender.incomplete_inputs ? "Incomplete inputs" : "Complete inputs",
      "Verdict": tender.verdict?.recommendation || "Not available",
    })) details.append(node("div", { class: "detail-item" }, [node("span", { text: label }), node("strong", { text: value ?? "Unavailable" })]));
    const events = node("div", { class: "timeline" });
    for (const event of timeline.events || []) {
      events.append(node("article", { class: "timeline-event" }, [
        node("div", { class: "timeline-line", "aria-hidden": "true" }, node("span", { class: "timeline-node" })),
        node("div", { class: "timeline-body" }, [node("strong", { text: `${event.stage} · ${event.status}` }), node("div", { class: "timeline-meta", text: `${formatDate(event.timestamp)} · tender ${event.tender_id ?? "—"} · run ${event.run_id ?? "—"} · correlation ${event.correlation_id || "—"}` }), event.failure_code && node("div", { class: "timeline-meta", text: `Failure: ${event.failure_code}` })]),
      ]));
    }
    const back = button("← Back to tenders", () => navigate("tenders"), "button button-quiet");
    const detail = node("section", { class: "stack" }, [back, card(tender.title, [details, tender.url ? linkButton("Open source notice", tender.url) : null], `Tender ${tender.id} · ${tender.source_name || "Unknown source"}`), card("Pipeline timeline", [events.childNodes.length ? events : emptyPanel("No timeline events were returned.")], `Correlation ${timeline.correlation_id || "unavailable"} · ${timeline.run_ids?.length || 0} run(s)`)]);
    if (state.role === "admin") {
      const verdictBox = card("Verdict history", [loadingPanel("Loading Admin-only verdict details…")]);
      detail.append(verdictBox);
      api.get(`/tenders/${tenderId}/verdicts`).then((data) => {
        if (state.activeTenderId !== tenderId) return;
        const rows = (data.items || []).map((v) => [v.id, v.recommendation, v.confidence ?? "Unavailable", v.urgency ? "Urgent" : "Not urgent", v.incomplete_inputs ? "Incomplete inputs" : "Complete inputs", formatDate(v.generated_at)]);
        verdictBox.lastChild.replaceWith(makeTable(["Verdict ID", "Recommendation", "Confidence", "Urgency", "Inputs", "Generated"], rows));
      }).catch((error) => verdictBox.lastChild.replaceWith(errorPanel(error)));
    }
    content.replaceChildren(detail);
  } catch (error) {
    if (state.activeTenderId === tenderId) content.replaceChildren(errorPanel(error, () => openTender(tenderId)));
  }
}

async function renderKnowledgeBase() {
  const data = await api.get("/knowledge-base/versions");
  const top = node("div", { class: "grid grid-3" });
  const current = data.current;
  top.append(metric("Current version", current ? `#${current.id}` : "No version", current ? `Created ${formatDate(current.created_at)}` : "Upload an approved KB document to create version 1."));
  top.append(metric("Tokens reported", data.token_budget?.latest_token_count ?? "Unavailable", "Reported by the Admin API."));
  top.append(metric("Warning threshold", data.token_budget?.warning_share == null ? "Unset" : `${Math.round(data.token_budget.warning_share * 100)}%`, "Configured in shared settings."));
  const upload = card("Upload a new version", [
    node("p", { class: "muted", text: "Versions are immutable. The selected file is sent to the authenticated Admin API for validation and extraction." }),
    field("Knowledge Base file", "file", "", { name: "file", accept: ".docx,.pdf,.md,.txt", help: "Supported formats: DOCX, PDF, Markdown, and text." }),
    field("Version note", "textarea", "", { name: "note", wide: true, rows: 2 }),
    node("div", { class: "form-actions" }, button("Upload version", null, "button button-primary")),
  ]);
  const fileInput = upload.querySelector('input[type="file"]');
  const noteInput = upload.querySelector('textarea');
  const uploadButton = upload.querySelector("button");
  if (uploadButton) uploadButton.addEventListener("click", async () => {
    const file = fileInput?.files?.[0];
    if (!file) { showMessage("error", "Choose a filename to preview."); return; }
    uploadButton.disabled = true;
    try {
      if (api instanceof LiveAdminApi) await api.uploadKnowledgeBase(file, noteInput?.value || null);
      else await api.post("/knowledge-base/versions", { filename: file.name, note: noteInput?.value || null });
      if (fileInput) fileInput.value = "";
      if (noteInput) noteInput.value = "";
      showMessage("success", isLive() ? "Knowledge Base version uploaded and persisted." : "A synthetic Knowledge Base version was added to the offline preview.");
      await renderCurrentPage();
    } catch (error) { showMessage("error", errorMessage(error)); }
    finally { uploadButton.disabled = false; }
  });
  const history = (data.items || []).map((version) => [
    `#${version.id}`,
    version.content_hash,
    version.token_count ?? "Unavailable",
    formatDate(version.created_at),
    version.created_by || "Unavailable",
    version.note || "—",
    button("View", () => viewKbVersion(version.id), "button button-quiet button-small"),
  ]);
  const historyCard = section("Version history", "Each update is a separate immutable version.");
  historyCard.append(makeTable(["Version", "Content hash", "Tokens", "Created", "Created by", "Note", "Action"], history));
  const compare = section("Compare versions", "Compare the stored contents of two immutable versions.");
  const compareForm = node("form", { class: "toolbar" });
  const choices = [{ value: "", label: "Select version" }, ...(data.items || []).map((version) => ({ value: version.id, label: `Version ${version.id}` }))];
  compareForm.append(field("From", "select", "", { name: "from_id", choices }), field("To", "select", "", { name: "to_id", choices }), node("button", { type: "submit", class: "button button-quiet", text: "Compare" }));
  compareForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const from = compareForm.elements.from_id.value;
    const to = compareForm.elements.to_id.value;
    if (!from || !to) { showMessage("error", "Select both versions to compare."); return; }
    try {
      const diff = await api.get(`/knowledge-base/diff?from_id=${encodeURIComponent(from)}&to_id=${encodeURIComponent(to)}`);
      const pre = node("pre", { class: "code-block" }, diff.diff?.join("\n") || "No differences returned.");
      compare.replaceChildren(compareForm, pre);
    } catch (error) { compare.replaceChildren(compareForm, errorPanel(error)); }
  });
  compare.append(compareForm);
  content.replaceChildren(top, upload, historyCard, compare);
}

async function viewKbVersion(versionId) {
  try {
    const data = await api.get(`/knowledge-base/versions/${versionId}`);
    const viewer = node("section", { class: "card card-pad stack" }, [node("h2", { text: `Knowledge Base version #${data.id}` }), node("p", { class: "small-muted", text: `${data.content_hash} · ${formatDate(data.created_at)} · ${data.token_count ?? "unavailable"} tokens` }), node("pre", { class: "code-block" }, data.content)]);
    content.append(viewer);
    viewer.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { content.append(errorPanel(error)); }
}

async function renderLlm() {
  const [profiles, roles, usage] = await Promise.all([api.get("/llm/profiles?offset=0&limit=100"), api.get("/llm/roles"), api.get("/llm/usage")]);
  const admin = state.role === "admin";
  const actions = node("div", { class: "toolbar" });
  if (admin) actions.append(button("＋ Add profile", () => editLlmProfile(), "button button-primary"));
  const usageCard = node("div", { class: "grid grid-4" }, [metric("Calls this month", usage.calls ?? "Unavailable"), metric("Known cost", usage.known_cost == null ? "Unavailable" : usage.known_cost, `${usage.cost_unknown_calls ?? 0} call(s) with unknown cost`), metric("Monthly budget", usage.monthly_budget ?? "Unset", usage.budget_open ? "No budget configured" : "Configured in shared settings"), metric("Budget state", usage.budget_open ? "Open" : "Configured", "From persisted usage and settings")]);
  const profileFields = ["name", "model", "context_window_tokens", "active", "approved_for_company_docs"];
  const rows = (profiles.items || []).map((profile) => [
    node("div", {}, [node("strong", { text: profile.name }), node("div", { class: "small-muted", text: profile.model })]),
    profile.base_url || "Provider URL hidden",
    pill(profile.active ? "Active" : "Inactive", profile.active ? "success" : "warning"),
    profile.approved_for_company_docs === undefined ? "Restricted details" : pill(profile.approved_for_company_docs ? "Company docs approved" : "Company docs blocked", profile.approved_for_company_docs ? "warning" : "success"),
    profile.api_key_configured === undefined ? "Hidden" : (profile.api_key_configured ? "Set · write-only" : "Not set"),
    admin ? node("div", { class: "table-actions" }, [
      button("Edit", () => editLlmProfile(profile), "button button-quiet button-small"),
      button("Test connection", () => testLlm(profile), "button button-primary button-small", { disabled: !admin }),
      button("Delete", () => deleteLlmProfile(profile), "button button-danger button-small"),
    ]) : "Read only",
  ]);
  const profileSection = section("LLM profiles", "Provider approval is not a business decision the UI can make for you.");
  profileSection.append(actions, makeTable(["Profile", "Endpoint", "State", "Company documents", "API key", "Actions"], rows));
  const roleRows = (roles.items || []).map((item) => {
    const selected = item.profile || item;
    return [item.role, selected?.name || `Profile ${item.profile_id ?? "unset"}`, item.fallback_profile?.name || (item.fallback_profile_id ? `Profile ${item.fallback_profile_id}` : "No fallback"), admin ? button("Assign", () => editRole(item, profiles.items || []), "button button-quiet button-small") : "Read only"];
  });
  const roleSection = section("Role assignments", "Assignments are shared with the pipeline configuration.");
  roleSection.append(makeTable(["Role", "Primary profile", "Fallback profile", "Action"], roleRows));
  if (admin) roleSection.append(button("＋ Assign role", () => editRole(null, profiles.items || []), "button button-quiet"));
  content.replaceChildren(usageCard, profileSection, roleSection);
}

function llmProfileFields(source = null) {
  return [
    { name: "name", label: "Profile name", required: true },
    { name: "base_url", label: "Provider base URL", required: true, help: "Use the base URL expected by the configured LLM adapter." },
    { name: "model", label: "Model identifier", required: true },
    { name: "api_key", label: source?.api_key_configured ? "Replace API key" : "API key", type: "password", help: "Write-only. Leave blank to retain the configured key." },
    { name: "context_window_tokens", label: "Context window (tokens)", type: "number", min: 1, step: 1, required: true },
    { name: "max_output_tokens", label: "Maximum output tokens", type: "number", min: 1, step: 1 },
    { name: "temperature", label: "Temperature", type: "number", min: 0, max: 2, step: 0.1 },
    { name: "timeout_seconds", label: "Timeout (seconds)", type: "number", min: 1, max: 600, step: 1, required: true },
    { name: "cost_per_1k_input", label: "Input cost per 1k", type: "number", min: 0, step: 0.0001 },
    { name: "cost_per_1k_output", label: "Output cost per 1k", type: "number", min: 0, step: 0.0001 },
    { name: "supports_json", label: "JSON output supported", type: "checkbox" },
    { name: "supports_vision", label: "Vision supported", type: "checkbox" },
    { name: "approved_for_company_docs", label: "Approved for company documents", type: "checkbox", help: "Requires explicit confirmation before changing." },
    { name: "active", label: "Profile active", type: "checkbox" },
  ];
}

function editLlmProfile(profile = null) {
  const defaults = { context_window_tokens: 8000, timeout_seconds: 60, supports_json: true, supports_vision: false, approved_for_company_docs: false, active: true };
  const initial = { ...defaults, ...(profile || {}), api_key: "" };
  editorDialog({
    title: profile ? `Edit ${profile.name}` : "Add LLM profile",
    description: "Credentials stay in memory only during submission and are write-only after saving.",
    fields: llmProfileFields(profile), initial, approvalConfirm: true,
    onSave: async (payload) => {
      if (profile && !payload.api_key) delete payload.api_key;
      if (profile) await api.put(`/llm/profiles/${profile.id}`, payload);
      else await api.post("/llm/profiles", payload);
      showMessage("success", isLive() ? "LLM profile saved." : "Offline fixture profile updated in memory.");
      await renderCurrentPage();
    },
  });
}

async function deleteLlmProfile(profile) {
  if (!await askConfirm("Delete LLM profile", `Delete ${profile.name}? The API will reject deletion while the profile is assigned to a role.`, "Delete profile", true)) return;
  try {
    await api.delete(`/llm/profiles/${profile.id}`);
    showMessage("success", isLive() ? "LLM profile deleted." : "Offline fixture profile deleted.");
    await renderCurrentPage();
  } catch (error) { showMessage("error", errorMessage(error)); }
}

function editRole(role, profiles) {
  const options = profiles.map((profile) => ({ value: profile.id, label: `${profile.name} · ${profile.model}` }));
  editorDialog({
    title: role ? `Assign ${role.role} role` : "Assign an LLM role",
    fields: [
      { name: "role", label: "Role", type: "select", required: true, choices: ["triage", "verdict", "embeddings", "vision_ocr"].map((value) => ({ value, label: value })) },
      { name: "profile_id", label: "Primary profile", type: "select", required: true, choices: options },
      { name: "fallback_profile_id", label: "Fallback profile", type: "select", choices: [{ value: "", label: "None" }, ...options] },
    ],
    initial: { role: role?.role || "verdict", profile_id: role?.profile_id || role?.profile?.id || profiles[0]?.id || "", fallback_profile_id: role?.fallback_profile_id || role?.fallback_profile?.id || "" },
    onSave: async (payload) => {
      payload.profile_id = Number(payload.profile_id);
      payload.fallback_profile_id = payload.fallback_profile_id ? Number(payload.fallback_profile_id) : null;
      await api.put(`/llm/roles/${encodeURIComponent(payload.role)}`, payload);
      showMessage("success", isLive() ? "LLM role assignment saved." : "Offline fixture role assignment updated in memory.");
      await renderCurrentPage();
    },
  });
}

async function testLlm(profile) {
  if (!await askConfirm("Test provider connection", `Send a minimal connection-check request to the configured provider for ${profile.name}?`, "Test connection", false)) return;
  try {
    const result = await api.post(`/llm/profiles/${profile.id}/test`, {});
    await askConfirm("Provider connection test result", `Status: ${result.status}\nProvider: ${result.provider}\nModel: ${result.model}\nLatency: ${result.latency_ms} ms\nJSON: ${result.supports_json}\nVision: ${result.supports_vision}${isLive() ? "\nA real provider connection was tested." : "\nOffline fixture only; no provider was contacted."}`, "Close", false);
  } catch (error) { showMessage("error", errorMessage(error)); }
}

async function renderRecipients() {
  const data = await api.get("/recipients");
  const actions = state.role === "admin" ? button("＋ Add recipient", () => editRecipient(), "button button-primary") : null;
  const sections = [];
  for (const [listType, title, note] of [["tender", "Tender recipients", "Recipients configured for tender notices."], ["dev_alert", "Development / alert recipients", "At least one active development/alert recipient is required."]]) {
    const group = (data.items || []).filter((item) => item.list_type === listType);
    const rows = group.map((recipient) => [
      state.role === "admin" ? node("div", {}, [node("strong", { text: recipient.email }), node("div", { class: "small-muted", text: recipient.name || "No display name" })]) : "Address hidden for Viewer",
      recipient.delivery || "—",
      pill(recipient.active ? "Active" : "Inactive", recipient.active ? "success" : "warning"),
      recipient.receives_filter || "—",
      state.role === "admin" ? node("div", { class: "table-actions" }, [
        button("Edit", () => editRecipient(recipient), "button button-quiet button-small"),
        button(recipient.active ? "Disable" : "Enable", () => saveRecipientActive(recipient), "button button-quiet button-small"),
        button("Delete", () => deleteRecipient(recipient), "button button-danger button-small"),
      ]) : "Read only",
    ]);
    const part = section(title, note);
    part.append(makeTable(["Recipient", "Delivery", "State", "Filter", "Actions"], rows));
    sections.push(part);
  }
  content.replaceChildren(...(actions ? [actions, ...sections] : sections));
}

const recipientFields = [
  { name: "email", label: "Email address", required: true },
  { name: "name", label: "Display name" },
  { name: "role", label: "Recipient role" },
  { name: "list_type", label: "List", type: "select", required: true, choices: [{ value: "tender", label: "Tender recipients" }, { value: "dev_alert", label: "Development / alert" }] },
  { name: "delivery", label: "Delivery", type: "select", choices: ["to", "cc", "bcc"].map((v) => ({ value: v, label: v.toUpperCase() })) },
  { name: "receives_filter", label: "Tender filter", type: "select", choices: ["all", "apply", "urgent"].map((v) => ({ value: v, label: v })) },
  { name: "min_severity", label: "Minimum alert severity", type: "select", choices: ["info", "warning", "critical"].map((v) => ({ value: v, label: v })) },
  { name: "source_scope", label: "Source scope (JSON ID array)", type: "json" },
  { name: "alert_types", label: "Alert types (JSON array)", type: "json" },
  { name: "active", label: "Recipient active", type: "checkbox" },
];

function editRecipient(recipient = null) {
  const initial = recipient || { list_type: "tender", delivery: "to", receives_filter: "all", min_severity: "critical", source_scope: null, alert_types: null, active: true };
  editorDialog({
    title: recipient ? `Edit recipient #${recipient.id}` : "Add recipient",
    description: recipient?.list_type === "dev_alert" ? "At least one active development/alert recipient must remain configured." : "Recipient details are restricted to Admin users.",
    fields: recipientFields, initial,
    onSave: async (payload) => {
      if (recipient) await api.put(`/recipients/${recipient.id}`, payload);
      else await api.post("/recipients", payload);
      showMessage("success", isLive() ? "Recipient saved." : "Offline fixture recipient updated in memory.");
      await renderCurrentPage();
    },
  });
}

async function saveRecipientActive(recipient) {
  const active = !recipient.active;
  // RecipientGuard in the Admin API owns the last-active development recipient invariant.
  const warning = isLive() ? "This change is saved to shared recipient configuration and audit-logged." : "This updates the offline fixture only.";
  if (!await askConfirm(`${active ? "Enable" : "Disable"} recipient`, `${recipient.email}\n\n${warning}`, active ? "Enable recipient" : "Disable recipient", !active)) return;
  try {
    const payload = Object.fromEntries(["email", "name", "role", "list_type", "delivery", "source_scope", "receives_filter", "alert_types", "min_severity"].map((key) => [key, recipient[key] ?? null]));
    payload.active = active;
    await api.put(`/recipients/${recipient.id}`, payload);
    showMessage("success", isLive() ? "Recipient state saved." : "Offline fixture recipient state updated.");
    await renderCurrentPage();
  } catch (error) { showMessage("error", errorMessage(error)); }
}

async function deleteRecipient(recipient) {
  if (!await askConfirm("Delete recipient", `Remove ${recipient.email || `recipient #${recipient.id}`}? The API prevents deleting the final active development/alert recipient.`, "Remove recipient", true)) return;
  try { await api.delete(`/recipients/${recipient.id}`); showMessage("success", isLive() ? "Recipient deleted." : "Offline fixture recipient deleted."); await renderCurrentPage(); }
  catch (error) { showMessage("error", errorMessage(error)); }
}

async function renderMail() {
  const [providers, recipients, settings] = await Promise.all([api.get("/mail/providers"), api.get("/recipients?list_type=dev_alert"), api.get("/settings")]);
  const rows = (providers.items || []).map((provider) => [
    `${provider.priority ?? "—"}. ${provider.name}`,
    provider.provider_type || "Unavailable",
    pill(provider.active ? "Active" : "Inactive", provider.active ? "success" : "warning"),
    jsonText(provider.capabilities, "Not exposed"),
    pill(provider.breaker_state || "Unavailable", provider.breaker_state === "closed" ? "success" : "warning"),
    provider.credentials_configured === undefined ? "Hidden" : provider.credentials_configured ? "Set · write-only" : "Not set",
    state.role === "admin" ? node("div", { class: "table-actions" }, [
      button("Edit", () => editMailProvider(provider), "button button-quiet button-small"),
      button(provider.active ? "Disable" : "Enable", () => toggleMailProvider(provider), "button button-quiet button-small"),
      button("Test provider", () => testMailProvider(provider), "button button-primary button-small"),
    ]) : "Read only",
  ]);
  const list = section("Provider chain", "Provider configuration is shared with NotificationService. This view does not probe providers.");
  if (state.role === "admin") list.append(button("＋ Add provider", () => editMailProvider(), "button button-primary"));
  list.append(makeTable(["Order · name", "Type", "State", "Capabilities", "Breaker", "Credentials", "Actions"], rows));
  const test = section("Send a test email", "This sends a real [TEST] message to the selected active development/alert recipient when connected to the Admin API.");
  const devs = (recipients.items || []).filter((recipient) => recipient.active);
  const form = node("form", { class: "card card-pad stack" }, [
    node("span", { class: "pill warning", text: "LIVE ACTION · sends only to an active development/alert recipient · [TEST] marker required" }),
    node("label", { class: "field" }, [node("span", { text: "Development/alert recipient" }), selectNode("recipient_id", devs.map((r) => ({ value: r.id, label: r.email || `Recipient ${r.id}` })), devs[0]?.id), node("small", { class: "field-help", text: "Verify this address before sending. Server-side Test Mode and recipient checks remain authoritative." })]),
    field("Subject", "text", "Admin UI delivery test", { name: "subject", required: true }),
    field("Message", "textarea", "[TEST] Tender Intelligence mail provider verification.", { name: "text", required: true, wide: true }),
    node("div", { class: "form-actions" }, node("button", { type: "submit", class: "button button-primary", disabled: state.role !== "admin" || !settings.test_mode || !devs.length, text: "Send [TEST] email" })),
  ]);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const recipientId = Number(form.elements.recipient_id.value);
    if (!settings.test_mode) { showMessage("error", "Test Mode is OFF; the API will refuse this operation."); return; }
    if (!await askConfirm("Send a [TEST] email", `Send a real test message to ${form.elements.recipient_id.selectedOptions[0]?.textContent || "the selected development recipient"}? The server will route it through the configured provider.`, "Send test email", false)) return;
    const submit = form.querySelector("button[type=submit]"); submit.disabled = true;
    try {
      const result = await api.post("/mail/test", { recipient_id: recipientId, subject: form.elements.subject.value, text: form.elements.text.value });
      showMessage("success", `Test email status: ${result.status}. Provider: ${result.provider || "unavailable"}. Correlation ID: ${result.correlation_id || "unavailable"}.`);
    } catch (error) { showMessage("error", errorMessage(error)); }
    finally { submit.disabled = false; }
  });
  if (state.role === "admin") test.append(form);
  else test.append(node("div", { class: "notice notice-info", text: "Test email is available to Admin users only." }));
  if (!settings.test_mode) test.append(node("div", { class: "notice notice-danger", role: "alert", text: "Test Mode is OFF. The Admin API will refuse test-email sends." }));
  if (!devs.length) test.append(node("div", { class: "notice notice-warning", text: "No active development/alert recipient exists." }));
  content.replaceChildren(list, test);
}

function selectNode(name, options, current) {
  const select = node("select", { name });
  for (const choice of options) select.append(node("option", { value: choice.value, selected: String(choice.value) === String(current ?? "") }, choice.label));
  return select;
}

const mailProviderFields = [
  { name: "name", label: "Provider name", required: true },
  { name: "provider_type", label: "Provider type", required: true, help: "Must match a provider supported by the existing server registry." },
  { name: "from_address", label: "Sender address", required: true },
  { name: "from_name", label: "Sender name" },
  { name: "reply_to", label: "Reply-to address" },
  { name: "priority", label: "Chain priority", type: "number", min: 1, step: 1, required: true },
  { name: "capabilities", label: "Provider capabilities (JSON)", type: "json", wide: true },
  { name: "credentials", label: "Replace credentials (JSON)", type: "password", wide: true, help: "Credentials are write-only. Leave blank to keep current credentials." },
  { name: "active", label: "Provider active", type: "checkbox" },
];

function editMailProvider(provider = null) {
  const initial = { priority: 1, active: true, ...(provider || {}), credentials: "" };
  editorDialog({
    title: provider ? `Edit ${provider.name}` : "Add mail provider",
    description: "Credentials are write-only and encrypted by the Admin API. Leave the field blank to retain existing credentials.",
    fields: mailProviderFields,
    initial,
    onSave: async (payload) => {
      if (typeof payload.credentials === "string") {
        if (payload.credentials.trim()) {
          try { payload.credentials = JSON.parse(payload.credentials); }
          catch { throw new Error("Credentials must be a JSON object."); }
        } else delete payload.credentials;
      }
      if (provider) await api.put(`/mail/providers/${provider.id}`, payload);
      else await api.post("/mail/providers", payload);
      showMessage("success", isLive() ? "Mail provider configuration saved." : "Offline fixture provider updated in memory.");
      await renderCurrentPage();
    },
  });
}

async function toggleMailProvider(provider) {
  const active = !provider.active;
  if (!await askConfirm(`${active ? "Enable" : "Disable"} provider`, `${provider.name} will be ${active ? "enabled" : "disabled"} in shared configuration.`, active ? "Enable provider" : "Disable provider", !active)) return;
  try { await api.patch(`/mail/providers/${provider.id}/active`, { active }); showMessage("success", "Provider state updated."); await renderCurrentPage(); }
  catch (error) { showMessage("error", errorMessage(error)); }
}

async function testMailProvider(provider) {
  if (!await askConfirm("Send provider test email", `Send a real [TEST] email through ${provider.name} to the active development/alert recipient?`, "Send test email", false)) return;
  try {
    const result = await api.post(`/mail/providers/${provider.id}/test`, {});
    showMessage("success", `Provider test status: ${result.status}. Provider: ${result.provider || "unavailable"}. Correlation ID: ${result.correlation_id || "unavailable"}.`);
  } catch (error) { showMessage("error", errorMessage(error)); }
}

async function renderTriage() {
  const data = await api.get("/triage");
  const fields = [
      { name: "include_keywords", label: "Include keywords", type: "lines", value: data.include_keywords, help: "One term per line. Null means unset; an empty configured list remains distinguishable." },
    { name: "exclude_keywords", label: "Exclude keywords", type: "lines", value: data.exclude_keywords, help: "One term per line." },
    { name: "sectors", label: "Target sectors", type: "lines", value: data.sectors, help: "Unset means no sector filter." },
    { name: "regions", label: "Target regions", type: "lines", value: data.regions, help: "Unset means no region filter." },
    { name: "minimum_contract_value", label: "Minimum contract value", type: "number", value: data.minimum_contract_value, help: "Currency conversion is not inferred. Leave blank to keep the current fixture value." },
    { name: "relevance_threshold", label: "Relevance threshold", type: "number", value: data.relevance_threshold, min: 0, max: 1, step: 0.01, help: "No default is invented; this preview leaves the fixture unset unless you enter a value." },
    { name: "urgency_window_days", label: "Urgency window (days)", type: "number", value: data.urgency_window_days, min: 0, step: 1, help: "0 is a configured value; blank/unset remains distinct." },
  ];
  const form = node("form", { class: "card card-pad stack" });
  const grid = node("div", { class: "form-grid" });
  const originals = {};
  for (const config of fields) {
    originals[config.name] = config.value;
    const control = field(config.label, config.type === "lines" ? "textarea" : "number", config.type === "lines" ? (config.value ?? []).join("\n") : config.value, {
      name: config.name, min: config.min, max: config.max, step: config.step,
      help: `${config.value === null || config.value === undefined ? "Current state: unset. " : Array.isArray(config.value) && !config.value.length ? "Current state: configured empty list. " : "Current state: configured. "}${config.help}`,
    });
    grid.append(control);
  }
  form.append(grid, node("div", { class: "form-actions" }, state.role === "admin" ? node("button", { class: "button button-primary", type: "submit", text: "Save triage configuration" }) : node("span", { class: "small-muted", text: "Viewer access is read-only." })));
  form.addEventListener("input", () => { state.dirty = true; });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {};
    for (const config of fields) {
      const raw = form.elements.namedItem(config.name).value;
      if (config.type === "lines") {
        const list = raw.split(/\r?\n/).map((part) => part.trim()).filter(Boolean);
        if (config.value === null && list.length === 0) continue;
        payload[config.name] = list;
      } else if (raw !== "") payload[config.name] = Number(raw);
      else if (config.value !== null && config.value !== undefined) {
      showMessage("info", `${config.label} was left unchanged. The current API does not expose a clear-to-unset operation for this field.`);
      }
    }
    if (!Object.keys(payload).length) { showMessage("info", "No configuration values changed."); state.dirty = false; return; }
    try { await api.put("/triage", payload); state.dirty = false; showMessage("success", isLive() ? "Triage configuration saved and audit-logged." : "Offline fixture triage configuration updated in memory."); await renderCurrentPage(); }
    catch (error) { showMessage("error", errorMessage(error)); }
  });
  content.replaceChildren(node("div", { class: "notice notice-info", text: isLive() ? "These are shared Stage A settings. Saving changes updates the configuration used by the worker; this screen does not run triage." : "Offline fixture settings only. No worker is connected." }), form);
}

async function renderSettings() {
  const data = await api.get("/settings");
  syncTestModeFromData(data.test_mode);
  const modeLabel = isLive() ? "LIVE" : "fixture";
  const form = node("form", { class: "card card-pad stack" });
  const stateSummary = node("div", { class: "notice notice-info" }, `Test Mode is ${data.test_mode ? "ON" : "OFF"}. Updated ${formatDate(data.updated_at)} · configuration version ${data.version ?? "unavailable"}.`);
  form.append(stateSummary);
  const grid = node("div", { class: "form-grid" });
  grid.append(field("Test Mode", "checkbox", data.test_mode, { name: "test_mode", disabled: state.role !== "admin", help: "When ON, tender email is routed only to development recipients and marked [TEST]. Turning this OFF requires explicit confirmation and a reason." }));
  grid.append(field("Reason for Test Mode change", "text", "", { name: "test_mode_reason", disabled: state.role !== "admin", help: "Required when turning Test Mode OFF." }));
  grid.append(field("Monthly AI budget", "number", data.monthly_ai_budget, { name: "monthly_ai_budget", min: 0, step: 0.01, disabled: state.role !== "admin", help: data.monthly_ai_budget == null ? "Unset; no budget configured." : "Configured budget." }));
  grid.append(field("Retention (months)", "number", data.retention_months, { name: "retention_months", min: 1, step: 1, disabled: state.role !== "admin" }));
  grid.append(field("Link expiry (days)", "number", data.link_expiry_days, { name: "link_expiry_days", min: 1, step: 1, disabled: state.role !== "admin" }));
  grid.append(field("KB token warning share (0–1)", "number", data.alert_thresholds?.kb_token_warning_share, { name: "kb_token_warning_share", min: 0, max: 1, step: 0.01, disabled: state.role !== "admin", help: "Fractional share at which a KB token-budget warning is raised." }));
  form.append(grid, node("div", { class: "form-actions" }, state.role === "admin" ? node("button", { class: "button button-primary", type: "submit", text: "Save settings" }) : node("span", { class: "small-muted", text: "Viewer access is read-only." })));
  form.addEventListener("input", () => { state.dirty = true; });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const turnOff = data.test_mode && !form.elements.test_mode.checked;
    if (turnOff) {
      if (!form.elements.test_mode_reason.value.trim()) { showMessage("error", "Provide a reason before turning Test Mode OFF."); return; }
      const confirmed = await askConfirm(
        "Turn Test Mode OFF?",
        isLive()
          ? "This will take effect immediately. All tender emails will route to real recipients and will NOT carry the [TEST] prefix. This change is audit-logged."
          : "This changes only the in-memory demo fixture. Refreshing restores Test Mode ON.",
        "Turn Test Mode OFF", true
      );
      if (!confirmed) {
        form.elements.test_mode.checked = data.test_mode;
        return;
      }
    }
    const payload = {};
    if (form.elements.test_mode.checked !== data.test_mode) {
      payload.test_mode = form.elements.test_mode.checked;
      if (!form.elements.test_mode.checked) payload.test_mode_reason = form.elements.test_mode_reason.value.trim();
    }
    const numberFields = ["monthly_ai_budget", "retention_months", "link_expiry_days"];
    for (const key of numberFields) {
      const value = form.elements[key].value;
      if (value !== "") payload[key] = Number(value);
    }
    const share = form.elements.kb_token_warning_share.value;
    if (share !== "" && Number(share) !== data.alert_thresholds?.kb_token_warning_share) {
      payload.alert_thresholds = { kb_token_warning_share: Number(share) };
    }
    if (!Object.keys(payload).length) { showMessage("info", "No settings changed."); state.dirty = false; return; }
    const saveBtn = form.querySelector("button[type=submit]");
    if (saveBtn) saveBtn.disabled = true;
    try {
      await api.put("/settings", payload);
      state.dirty = false;
      showMessage("success", isLive() ? "Settings saved to the database. The worker will pick up changes on its next run." : "Demo settings updated in memory only.");
      await renderCurrentPage();
    } catch (error) {
      form.elements.test_mode.checked = data.test_mode;
      showMessage("error", errorMessage(error));
    } finally {
      if (saveBtn) saveBtn.disabled = false;
    }
  });
  const footer = isLive()
    ? node("p", { class: "small-muted", text: "Changes are written to the database and audit-logged. The worker picks them up on its next run." })
    : node("p", { class: "small-muted", text: "Offline preview: fixture changes stay in memory. Refresh resets them." });
  content.replaceChildren(form, footer);
}

async function renderAudit() {
  const data = await api.get(`/audit?offset=${state.offset}&limit=${state.pageSize}`);
  const filter = node("label", { class: "field" }, [node("span", { text: "Filter this page by entity" }), node("input", { type: "search", placeholder: "For example: Setting", autocomplete: "off" })]);
  const tableHolder = node("div");
  const renderRows = (term = "") => {
    const items = (data.items || []).filter((row) => `${row.actor} ${row.entity} ${row.entity_id} ${jsonText(row.changed_fields)}`.toLowerCase().includes(term.toLowerCase()));
    tableHolder.replaceChildren(makeTable(["Timestamp", "Actor", "Entity", "Target", "Changed fields"], items.map((row) => [formatDate(row.created_at), row.actor, row.entity, row.entity_id ?? "—", node("pre", { class: "code-block audit-code" }, jsonText(row.changed_fields))])));
  };
  filter.querySelector("input").addEventListener("input", (event) => renderRows(event.target.value));
  renderRows();
  content.replaceChildren(node("div", { class: "notice notice-info", text: "Persisted configuration audit history. Secret values are omitted by the Admin API." }), filter, tableHolder, pagination(data, (offset) => { state.offset = offset; renderCurrentPage(); }));
}

async function boot() {
  try {
    api = await createApi();
    state.live = api instanceof LiveAdminApi;
    state.role = state.live ? (api.connectionState === "auth_required" ? "viewer" : api.role) : api.role;
    $("#connection-label").textContent = connectionLabel();
    $("#actor-role").textContent = state.live ? `Role · ${state.role}` : `Preview role · ${state.role}`;
    $("#demo-chip").hidden = state.live;
    $("#demo-banner").hidden = state.live;
    $("#demo-role-control").hidden = state.live;
    $("#scenario-control").hidden = state.live;
    $("#footer-state").textContent = state.live
      ? "Configuration and records are read from the Admin API."
      : "Offline fixtures only · edits remain in memory and are discarded on refresh.";
    if (!state.live) {
      for (const [value, label] of DEMO_SCENARIOS) scenarioSelect.append(node("option", { value }, label));
      $("#demo-role").value = state.role;
      syncTestModeIndicator();
    } else if (api.connectionState !== "auth_required") {
      try {
        const settings = await api.get("/settings");
        syncTestModeFromData(settings.test_mode);
      } catch {
        $("#test-mode-status").textContent = "TEST MODE · unavailable";
        $("#test-mode-status").className = "status-chip";
      }
    }
    renderNavigation();
    await renderCurrentPage();
  } catch {
    state.live = false;
    api = new FixtureAdminApi();
    state.role = api.role;
    $("#connection-label").textContent = connectionLabel();
    $("#demo-chip").hidden = false;
    $("#demo-banner").hidden = false;
    $("#demo-role-control").hidden = false;
    $("#scenario-control").hidden = false;
    $("#footer-state").textContent = "Offline fixtures only · edits remain in memory and are discarded on refresh.";
    for (const [value, label] of DEMO_SCENARIOS) scenarioSelect.append(node("option", { value }, label));
    renderNavigation();
    await renderCurrentPage();
  }
}

boot();

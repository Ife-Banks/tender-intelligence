# Prompt 06 — AI Triage (Stage A)

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/07-ai-verdict-spec.md` (§7.1 Stage A — Triage)
- `docs/04-pipeline-spec.md` (§4.2 stage 6, §4.13)
- `docs/09-admin-app-spec.md` (screen 8 — Triage & urgency, so the config surface is known)
- `docs/03-data-model.md` (§Settings, §LLMRoleAssignment)
- `docs/13-open-decisions.md` (O5, O6, O7 — target sectors/regions/minimum value)

## Task

Implement **Stage A — Triage**: a cheap relevance filter that discards obviously irrelevant notices before any full AI call.

1. **Rule engine:** admin-configurable rules — include/exclude keywords, target sectors, target regions, optional minimum contract value, relevance threshold. Values absent from configuration must remain unset (defaults open; `docs/13` O5/O6/O7). Store rules in the Settings/triage-rule surface defined by `docs/03`.
2. **Two execution modes:**
   - Rule/keyword-based pass (may be the default initially — the source explicitly allows starting with no model).
   - Optional cheap-model pass through the `triage` role when a profile is assigned (via `LLMRoleAssignment`).
3. **Output:** a triage result per tender (pass / discard + optional score), recorded as a stage status in the tender timeline. Distinguish `triage_discarded` (system decision) from `triage_failed` (could not evaluate) — the latter must not be silently treated as irrelevant (`docs/14` T3).
4. **Cost control integration:** triage runs before Stage B; the per-call `LLMCall` record is written when a model is used; budget guard hooks (`docs/07` §7.11) are left wired for the verdict prompt.
5. Respect `docs/07` §7.1 rules: defaults derived from the KB pursue/skip section when present; otherwise unset.

## Out of scope

- Stage B verdict engine (next prompt).
- Deciding the target sectors/regions/minimum-value values (O5/O6/O7 — leave configurable, defaults blank).
- Document understanding, email, admin UI.

## Tests

- Keyword include/exclude filtering works.
- Sector/region/value rules match only when configured; when unconfigured, nothing is filtered by those axes.
- Discarded vs failed are distinct statuses; a triage-stage exception produces `triage_failed` and does not drop the tender as "irrelevant".
- Mandatory pass-through: a notice with no rule match is handled per the configured threshold (default: keep = proceed to Stage B when no rules disqualify).
- Model-mode path uses the assigned `triage` profile and writes an `LLMCall` record; no model assigned ⇒ rule mode with no LLM call.
- Conclusion status recorded with correlation ID on the tender timeline.

## Rules

- Do not invent target-profile values; keep config-blank as the honest default.
- Preserve Test Mode and never send KB content during triage (triage runs on public tender text only).
- Tests accompany the implementation.

## Report

Files changed; mapping to `docs/07` §7.1; default rule behaviour with blank config; tests run + results; notes.
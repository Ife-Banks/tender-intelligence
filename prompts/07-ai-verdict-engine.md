# Prompt 07 — AI Verdict Engine (Stage B)

> Paste `prompts/00-master-context.md` first. **This is the most rules-heavy prompt — read `docs/07` fully.**

## Read

- `PROJECT_RULES.md`
- `docs/07-ai-verdict-spec.md` (full — required assessment, evidence rules, validation, retries, failure behaviour, data policy, budget, fallback)
- `docs/04-pipeline-spec.md` (§4.2 stage 7–8, §4.6, §4.7, §4.12, §4.13)
- `docs/03-data-model.md` (§Verdict, §KnowledgeBaseVersion, §LLMCall, §LLMProfile, §LLMRoleAssignment)
- `docs/10-security-spec.md` (data policy §10.1)
- `docs/13-open-decisions.md`

## Task

Implement **Stage B — Verdict generation** and the **verdict formatter** that emits the §8.1 email structure.

1. **Context assembly:** given a tender's document bundle and the current `KnowledgeBaseVersion`, build the input for the assigned `verdict` role. Read the KB whole (v1 — **no RAG/vector retrieval**, never build it).
2. **The message:** require JSON conforming to a defined schema covering: background; requirements (incl. exact deadline date+time+timezone); requirement-by-requirement gap analysis with **KB section citations**; verdict enum (`APPLY` / `DO NOT APPLY` / `APPLY WITH CONDITIONS`); confidence; urgency flag; and the pipeline-provided `incomplete_inputs` value.
3. **Prompt-level evidence rules** (encode in the prompt, enforce behaviourally):
   - Every "meets requirement" claim cites its KB section; uncited claims are downgraded to "unverified".
   - Gaps are written "no evidence on file for X", never an invented negative capability claim.
   - Urgency is set when deadline is within the configured window regardless of verdict.
4. **Validation & retry:** strict JSON schema validation. On invalid output: retry **once**; if still invalid ⇒ mark `verdict_failed`, raise an alert, and ensure the raw notice is still emailed with an "automatic assessment unavailable" banner (the email path is a separate prompt — provide the rejected-verdict data contract).
5. **Provider/version recording** (per `docs/03` §Verdict): `llm_profile_id`, `model`, `knowledge_base_version_id`, `prompt_version`, and whether map/reduce summarisation occurred.
6. **Retry/fallback:** default 2 retries on timeout/rate-limit/5xx then fallback profile; **fallback must obey `approved_for_company_docs`** — refuse KB content to any unapproved profile (primary or fallback). Write `LLMCall` records (tokens, latency, cost).
7. **`incomplete_inputs`:** propagate and record per `docs/07` §7.7.
8. **Oversized bundles:** if bundle + KB exceed the profile's `context_window_tokens`, summarise per document (map), then verdict on summaries (reduce); record that it happened.
9. **Token-budget guard:** KB token warning at the configured share (default 40% `[PROPOSED]`); budget gate (`awaiting_budget`) hooks per `docs/04` §4.13.

## Out of scope

- Building RAG/embeddings/vector infra.
- Email sending, attachment planning (separate prompt) — only define the formatter output contract.
- Deciding the monthly AI budget value, KB template content, or production provider approval.
- Any admin UI.

## Tests

- Schema: valid JSON accepted; each invalid case (missing field, bad enum, non-JSON) retried once then `verdict_failed`.
- Evidence rules: a claim with no citation is flagged/unverified; never-asserted-capability phrase `no evidence on file for X` is enforced on fixture prompt outputs (mock LLM).
- Data policy: KB content refused for `approved_for_company_docs=false` call, including as fallback.
- Retry/fallback behaviour with mock LLM (timeout, 429, 5xx, fallback refusal).
- `LLMCall` written per call; verdict records profile+model+KB version+prompt version.
- `incomplete_inputs` propagation.
- Oversized bundle: map/reduce path executes and is recorded.
- Mock LLM harness — never real credentials in tests; Test Mode respected.

## Rules

- Neutral on provider specifics; LLM call goes through `LLMClient` abstraction.
- Never log KB content or full prompts; only metadata (`docs/10` §10.1).
- Do not decide business values (budget, KB content, provider approval) — leave config-gated with flags.

## Report

Files changed; mapping to `docs/07` sections; schema version + prompt version identifiers chosen; tests run + results; open decisions still pending.
# Prompt 12 — Testing & QA (Hostile)

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/11-testing-strategy.md` (full — especially §11.2 failure scenarios F1–F20)
- `docs/14-acceptance-criteria.md`
- `docs/04-pipeline-spec.md` (retry/failure/idempotency)
- `docs/10-security-spec.md`

## Task

Run a **hostile QA / failure-testing pass**. Your job is to break the system and prove it fails safely, loudly, and per spec. Use `docs/11` §11.2 (F1–F20) as the mandate:

1. For each failure scenario, simulate it (fakes/stubs/injected faults — no live third parties) and verify the documented response:
   - Source down → structured log + throttled dev alert; no crash; no false confidence.
   - Parser mismatch → `parser_mismatch` alert.
   - One-of-ten attachment 404 → others proceed; `incomplete_inputs=true`.
   - Corrupt ZIP member, OCR failure, half-succeeded extraction → per-file statuses.
   - LLM timeout → retries → fallback; LLM invalid output twice → `verdict_failed` + alert + raw notice still emailed with banner.
   - Budget 100% → `awaiting_budget` queue; triage continues.
   - Mail provider transient vs permanent; chain down → `pending_retry` + red banner; ambiguous timeout → `possible_duplicate`.
   - Crawl crash → seen-tenders preserved; next run proceeds.
   - Admin down → worker runs on last saved config.
   - Last active dev recipient removal → refused.
   - Unapproved fallback receiving KB → refused.
   - Oversized bundle → map/reduce + recorded.
   - Test Mode leakage to business recipients → none.
2. **Idempotency stress:** crawl the same fixtures repeatedly; assert zero duplicate notifications, stable seen-tenders, sane RunHistory.
3. **Concurrency/reentrancy check:** overlapping runs of the same source do not double-send or double-process (as designed).
4. **Retry exhaustion sanity:** all retries consumed leaves a clear terminal state and an alert.
5. **Acceptance cross-check:** map results to `docs/14` criteria IDs (G/S/W/D/T/V/K/N/M/A/SE/AU/FH/DP). Report which criteria are proven, which are untested, and why.

## Out of scope

- New functionality.
- Live-network tests against real sources/providers (open-decision constrained). Note what still needs a live, authorised integration run.

## Deliverables

- A filled trace of each F1–F20 scenario (status: PASS / FAIL / PARTIAL + evidence).
- New or updated tests for any scenario not already covered.
- A gap list: criteria from `docs/14` that remain unproven, and whether the blocker is code, data, or an open decision.
- Summary of defects found and fixed (with doc references).

## Rules

- Test code must not weaken production checks (no stubs that bypass validation).
- Preserve correlation IDs in all failure traces.

## Report

Scenario-by-scenario results; tests added/changed; defects found/fixed; unproven acceptance criteria; risks that remain (with open-decision links).
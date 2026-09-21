# Prompt 14 — Code Review Against Specifications

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md` (all)
- Every doc in `/docs` (00–14) that applies to the code under review.
- The relevant prompts from `/prompts` that produced the code.
- `README.md`

## Task

Perform a **full implementation review** of the entire repository against every project specification. You are the final quality gate before release.

Review systematically:

1. **Requirements consistency (`docs/01`, `docs/14`):** walk each MUST requirement and each acceptance criterion; mark satisfied / partially satisfied / not implemented / handled-by-open-decision. Flag any requirement where v1.0 behaviour accidentally overrode v1.1.
2. **Architecture (`docs/02`, `docs/04`):** worker/admin separation; config re-read per run; admin dry-runs only; provider abstractions (LLM/Mail/source/document) intact; pipeline stage order and status recording; correlation IDs; idempotency; no RAG built into v1.
3. **Data model (`docs/03`):** entities/fields/constraints/indexes match; enums correct; migrations non-destructive; `approved_for_company_docs` default false; Test Mode default ON.
4. **Adapters & processing (`docs/05`–`docs/07`):** WAHO isolation; document resilience; triage/verdict separation; evidence rules ("no evidence on file", citations); JSON validation + single retry + `verdict_failed`; KB version/provider/model recording; data-policy gate incl. fallback.
5. **Notifications (`docs/08`):** provider chain, planner, Test Mode routing, snapshots, secure links, attached-vs-linked logging, update/alert templates.
6. **Admin (`docs/09`, `prompts/09`, `prompts/10`):** ten screens; API write/read split; secret write-only; viewer restrictions.
7. **Security (`docs/10`):** every §10.3 invariant; secrets-in-logs grep clean; audit log values safe; signed links.
8. **Tests (`docs/11`):** coverage of failure scenarios F1–F20; no test was deleted/weakened; QA trace from `prompts/12` is consistent.
9. **Deployment (`docs/12`):** phase order respected; go-live blockers correctly surfaced; nothing deployed without decisions.
10. **Open decisions (`docs/13`):** confirm no code silently hard-coded a business decision; confirm every open decision used a config default + flag instead.

Produce per-area verdicts plus:

- A **release-readiness summary** (Go / No-Go / Conditional) against `docs/14`, with an explicit list of unmet criteria.
- A **go-live blockers** list grouped by open decision (`docs/13`) vs code defect.
- Any contradictions between `docs/` documents you found (suggest an edit to the docs, do not silently rewrite).
- A short changelog of review fixes applied.

## Out of scope

- Writing new features.
- Rewriting docs beyond correcting a recorded contradiction.

## Rules

- Apply fixes only when they are clearly required by a documented requirement; otherwise record as a finding.
- Never weaken security/tests/validation while "fixing" a review finding.

## Report

Per-area verdicts; checklist of acceptance criteria with status; code defects fixed; doc contradictions found; go-live blockers; overall recommendation.
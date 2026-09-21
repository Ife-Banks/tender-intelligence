# 07 — AI Verdict Specification

> Source of truth: v1.1 §5.5, §5.6, §5.9, §5.12. This is a critical document: it defines the two-stage AI pipeline, the required assessment content, evidence rules, structured-output controls, and the approval/data-policy rules. RAG is **not** part of v1.

---

## 7.1 Stage A — Triage

### Purpose
Cheaply filter out obviously irrelevant notices **before** spending a full AI call on them. Sources like All Business Africa or TenderDetail publish thousands of unrelated notices (school furniture, army boots, road maintenance); most should never reach the expensive verdict step. Triage therefore exists for cost control and signal quality.

### Inputs
- The listing-level or bundle-level data of the tender (title, source, sector/region hints, optionally the extracted notice text).
- Admin-configurable triage rules.
- Defaults drawn from the knowledge base's "sectors, regions and contract types to pursue or skip" section.

### Outputs
- A decision: **pass to Stage B** or **discard as irrelevant**; optionally a relevance score against the threshold.
- Recorded in the tender's timeline as the triage stage status/result.

### Why it exists
- Per-tender AI calls cost money (v1.1 NFR "Cost control," §5.6, §5.9.6).
- Reduces unnecessary expensive AI processing; the budget guard ($5.9.6) is the backstop.

### Rules
- Triage rules are **admin-configurable** (v1.1 §5.8): include/exclude keywords, target sectors and regions, optional minimum contract value, relevance threshold.
- May start as simple rule/keyword matching; the source explicitly permits starting without a model (v1.1 §5.6, §5.9.3 role `triage`).
- Rule-set defaults come from the knowledge base's pursue/skip section.
- **The actual target profile (sectors/regions/contract types/minimum value) is an open business decision** — `docs/13-open-decisions.md`. Do not invent it.
- Triage discards silently by design for clearly-irrelevant notices, but a **triage failure** (cannot evaluate / stage error) must be handled as a warning/alert, not silently dropped as "irrelevant." *[Mechanism proposed: distinguish `triage_discarded` from `triage_failed`.]*

---

## 7.2 Stage B — Verdict

### Inputs
- The tender's reusable **document bundle** (plain text + metadata per document; §06).
- The current **knowledge-base version** (read whole, per decision D5).
- Assigned LLM profiles per role (triage/verdict), with retry + fallback.

### Outputs — the required assessment
The structured verdict contains exactly the sections OPEX already uses (v1.1 §5.6, §8.1):

1. **Background** — plain-language summary: procuring body, grant/project ID if present, scope of assignment.
2. **Requirements** — extracted eligibility criteria, required experience, required documents/evidence, and the **exact deadline (date, time, timezone)**. The deadline, not "a date."
3. **Why we can/cannot apply** — requirement-by-requirement evidence:
   - For each requirement: does the knowledge base show evidence of meeting it, partially meeting it, or not meeting it at all?
   - **Name the specific gap** (e.g. "no GS1-certified Lead Consultant on file"), not just "not a fit."
   - Cite the knowledge-base section each "meets requirement" claim relies on. A claim with no citation is **downgraded to "unverified."**
4. **Verdict / recommendation** field — exactly one of:
   ```text
   APPLY
   DO NOT APPLY
   APPLY WITH CONDITIONS
   ```
   (e.g. "only if we can secure a GS1-certified partner within N days"), **plus a confidence score.**
5. **Deadline urgency flag** — urgent if the deadline is within a configurable threshold (example given: 5 business days), **regardless of the verdict**, so a borderline-relevant but time-critical notice doesn't get buried.

### Why the company can/cannot apply
The gap analysis must be grounded in **evidence from the versioned knowledge base**, presented with citations. It must not claim the company lacks a capability merely because the record doesn't mention it.

## 7.3 Evidence rules

- **Evidence-cited matches.** Every "meets requirement" claim cites the knowledge-base section it relies on. Uncited claims → "unverified."
- **"No evidence on file" wording.** The KB is a record of what OPEX has documented, not a proof of what OPEX lacks. Gap statements read:

  > No evidence on file for X.

  …instead of inventing a negative capability claim ("OPEX does not have X"). A human can then check before dismissing a tender.
- This phrasing is **mandatory behaviour for the AI**, not a suggestion.

## 7.4 Structured output & validation

- The model is asked for **JSON matching a schema** corresponding to §7.2 (background, requirements, gap analysis with citations, verdict enum, confidence, urgency flag, incomplete-inputs marker as provided by the pipeline).
- The engine **validates** the JSON (schema + enum values + required fields).
- **One retry** after invalid output.
- If it still fails: tender marked `verdict_failed`, an **alert is raised**, and the **raw notice is still emailed** with a clear "automatic assessment unavailable" banner — nothing is lost, the failure is loud.

## 7.5 Failed-verdict behaviour

| Outcomes | Behaviour |
|---|---|
| Invalid output (after 1 retry) | `verdict_failed`, alert, raw notice emailed with banner |
| Timeout/rate-limit/5xx | Retry (default 2) → fallback profile → if still failing, alert + treat as failed verdict (same loud path) |
| Budget at 100% | Stage B paused; tender queued `awaiting_budget`; triage continues |
| Missing attachments | `incomplete_inputs = true`; verdict still produced on available content with the flag set |

## 7.6 Provider/model & version recording

Per verdict (v1.1 §5.6, §7), record:
- **LLM provider profile id** actually used (primary or fallback).
- **Model** identifier actually used.
- **Knowledge-base version** id used.
- **Prompt version.**
- Whether summarisation (map/reduce) was used for an oversized bundle.

This makes "why did the system say that in October?" answerable.

## 7.7 `incomplete_inputs`

- Set `true` when any attachment failed to download or extract (v1.1 §5.6, §6.1).
- The email footer says so explicitly.

## 7.8 Oversized document handling

- If the tender bundle plus the knowledge base exceeds the profile's `context_window_tokens`:
  1. Summarise per document first (map step).
  2. Run the verdict on the summaries (reduce step).
  3. **Record that this happened** in the verdict/audit.
- Token budget guard warns in admin when KB exceeds a configurable share (default 40% [PROPOSED]).

## 7.9 Test-provider restrictions

- While any profile with `approved_for_company_docs = false` is assigned to the verdict role, the knowledge base sent to it must be the **placeholder/non-sensitive version** (v1.1 §5.12, §5.9.4). Never real OPEX company data.

## 7.10 Approved-for-company-documents policy

- Verdict calls send OPEX's capability record — certificates, project values, staff details — to whichever provider is assigned. That is a **trust decision**, not just a technical one.
- `approved_for_company_docs` defaults to **false**.
- The system **refuses** to send knowledge-base content to a profile whose flag is `false`, **including as a failover target**.
- Setting the flag `true` is an **explicit admin action, audit-logged**.
- OPEX's paid model is expected to be the first profile flagged true, **after OPEX confirms it is acceptable under its own data-handling requirements** (open decision → `docs/13-open-decisions.md`).

## 7.11 Budget controls

- Log tokens in/out, latency, estimated cost per call (`LLMCall`).
- Configurable monthly AI budget: **alert at 80%**, **pause Stage B at 100%** (triage continues; tenders queue `awaiting_budget`).
- Budget value = open business decision.

## 7.12 Fallback providers

- Each role (`triage`, `verdict`, `embeddings` [deferred], `vision_ocr` [optional]) may name a fallback profile.
- Retry (default 2) → failover on timeout/rate-limit/5xx.
- **Fallback is subject to the same data policy as the primary:** the system will not fail over an unapproved (KB-containing) call to an unapproved profile.
- Record which profile actually served each call.

## 7.13 Role assignment & model choice

- Which model each stage uses is decided by **role assignment in `LLMRoleAssignment`**, not hardcoded.
- `triage` may be a cheap/small model or rule-based with no model.
- `verdict` needs enough capability/context for long, multi-language, table-heavy documents.
- `embeddings` and `vision_ocr` are not v1 roles (`embeddings` deferred; `vision_ocr` optional pending OCR-test results).

## 7.14 RAG / vector search status

**RAG/vector retrieval is NOT part of v1.**

- v1.1 decision D5 + §3 + §5.5: one structured, versioned knowledge-base document read directly by the verdict engine; no embeddings, no vector store, no retrieval step.
- Phase-2/5 upgrade path exists (interface `get_knowledge_context(tender_bundle)` stays stable); implementing it requires an **embedding model** and is only to be done when explicitly approved.

## 7.15 Proposed mechanism notes

- *[Proposed]*: OpenAI-compatible chat shape behind an `LLMClient` interface; per-role profiles; JSON-schema validation library; per-call usage logging. These are implementation choices under the confirmed behavioural rules above.

## 7.16 Historical note (v1.0)

v1.0 described Stage B as "full document-understanding + RAG + verdict," with per-file KB documents and a single `model_version`. v1.1 removed RAG from v1, replaced per-file docs with the versioned document, added the evidence-citation and "no evidence on file" rules, added `approved_for_company_docs`, the budget guard, fallback data policy, prompt versioning, and `incomplete_inputs`. v1.1 governs.
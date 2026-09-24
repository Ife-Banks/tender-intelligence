# Prompt 14 — AI Verdict Engine (Stage B)

> Paste `prompts/00-master-context.md` first. **This is the most rules-heavy prompt — read `docs/07` fully before implementing anything.**
>
> **Do not start Prompt 15 or modify Admin UI. Prompt 14 must be independently behaviorally verified before the next implementation prompt.**

## Read

* `PROJECT_RULES.md`
* `docs/07-ai-verdict-spec.md` — **full document required**

  * required assessment
  * evidence rules
  * validation
  * retries
  * failure behaviour
  * data policy
  * budget
  * fallback
* `docs/04-pipeline-spec.md`

  * §4.2 stage 7–8
  * §4.6
  * §4.7
  * §4.12
  * §4.13
* `docs/03-data-model.md`

  * Verdict
  * KnowledgeBaseVersion
  * LLMCall
  * LLMProfile
  * LLMRoleAssignment
* `docs/10-security-spec.md`

  * §10.1 data policy
* `docs/13-open-decisions.md`
* `prompts/12.1` / the implemented deadline-resolution contract, if present
* `prompts/13-ai-triage.md`
* the actual current implementation/state from Prompts 04–13

---

# Objective

Implement **Stage B — Verdict Generation** and the **verdict-content formatter**.

Stage B runs only for tenders that pass Stage A triage.

The implementation must produce an evidence-grounded, structured assessment that can be consumed by the existing notification/email layer.

Do not implement:

* email sending;
* attachment planning;
* Admin UI;
* RAG/vector infrastructure;
* application submission;
* new source adapters.

---

# 1. STAGE B ENTRY CONDITION

Stage B must only execute when Stage A has produced a valid pass/continue result.

Verify the existing triage contract rather than duplicating triage logic.

Expected:

```text
triage PASS
    ↓
Stage B
```

A discarded tender must not reach Stage B.

A failed triage must not be silently treated as a pass.

---

# 2. CONTEXT ASSEMBLY

Given:

* the tender;
* its persisted `TenderDocumentBundle`;
* the authoritative resolved deadline;
* the current `KnowledgeBaseVersion`;
* the assigned `verdict` LLM role/profile;

build the Stage B context.

### Knowledge Base

For v1:

* use the complete applicable KB;
* do not build embeddings;
* do not build vector search;
* do not implement RAG;
* do not silently omit arbitrary KB sections.

If the complete KB cannot fit the approved model context window, follow the documented oversized-context policy in `docs/07`.

Do not silently truncate the KB or replace it with an undocumented retrieval strategy.

---

# 3. AUTHORITATIVE DEADLINE

The deadline used by Stage B must come from the project's resolved deadline contract.

If Prompt 12.1 has produced:

```text
RESOLVED
```

use its canonical UTC value plus original source timezone.

If the deadline is:

```text
UNRESOLVED
```

or:

```text
CONFLICTING
```

preserve that state.

Do not independently guess, reinterpret, or overwrite the deadline from raw document text.

The verdict output must never fabricate an exact deadline.

---

# 4. STRUCTURED MODEL OUTPUT

Require strict JSON conforming to a versioned schema.

The schema must cover at minimum:

* background;
* requirements;
* exact deadline date;
* exact deadline time;
* deadline timezone;
* requirement-by-requirement assessment;
* tender evidence references where applicable;
* KB section citations where company capability/applicability claims require KB support;
* gaps;
* verdict:

  * `APPLY`
  * `DO NOT APPLY`
  * `APPLY WITH CONDITIONS`
* confidence;
* urgency flag;
* `incomplete_inputs`;
* assessment limitations where applicable.

Choose and record explicit:

```text
schema_version
prompt_version
```

Do not leave these implicit.

---

# 5. EVIDENCE RULES

Implement and enforce the evidence rules from `docs/07`.

## Tender evidence

Claims about what the tender requires or states must be grounded in the tender document bundle.

Where the output schema supports evidence references, cite the relevant document/page/section.

## KB evidence

Claims about OPEX capability, qualifications, experience, resources, certifications, or applicability must cite the relevant KB section.

A company-capability claim requiring KB support but lacking a valid KB citation must be marked:

```text
unverified
```

Do not invent citations.

Do not create fake section numbers.

---

# 6. GAP LANGUAGE

When the KB contains no evidence supporting a capability, do not convert absence of evidence into a negative factual claim.

Preferred form:

```text
no evidence on file for X
```

Do NOT produce unsupported claims such as:

```text
OPEX does not have X capability.
OPEX has never done X.
OPEX lacks X experience.
```

unless the KB itself contains explicit evidence supporting that negative conclusion.

Distinguish:

```text
no evidence on file
```

from:

```text
evidence indicates the requirement is not met
```

These are not interchangeable.

---

# 7. REQUIREMENT-BY-REQUIREMENT ASSESSMENT

Every material tender requirement must be traceable through:

```text
Tender requirement
    ↓
Tender evidence
    ↓
Relevant company evidence, where applicable
    ↓
KB citation
    ↓
Assessment status
    ↓
Gap / limitation
```

Do not generate a final verdict without a corresponding requirement-level assessment.

The verdict must be derivable from the assessment and evidence rather than selected first and rationalized afterward.

---

# 8. INCOMPLETE INPUTS

Propagate the pipeline-provided:

```text
incomplete_inputs
```

value exactly.

When:

```text
incomplete_inputs = true
```

the assessment must explicitly identify the relevant limitation.

Missing documents must NOT automatically be interpreted as evidence that the company fails a requirement.

For example:

```text
missing technical attachment
≠
technical requirement failed
```

The result should instead identify the assessment as incomplete or unverified where appropriate.

---

# 9. URGENCY

Urgency is independent of the applicability verdict.

Set urgency according to the configured urgency window and the authoritative resolved deadline.

For example, if the configured window says a deadline within N days is urgent:

```text
APPLY + deadline within N days
→ urgent
```

and:

```text
DO NOT APPLY + deadline within N days
→ urgent
```

provided the deadline is sufficiently resolved to make that determination.

Do not infer urgency from the verdict itself.

---

# 10. VALIDATION

Perform both:

### Structural validation

* strict JSON;
* schema validation;
* enum validation;
* required fields;
* correct data types.

### Semantic validation

Verify at minimum:

* every required requirement assessment exists;
* verdict enum is valid;
* confidence is valid;
* evidence references are structurally valid;
* KB citations reference actual KB sections;
* unsupported capability claims are rejected/downgraded;
* `incomplete_inputs` matches pipeline state;
* urgency is consistent with the configured window/deadline state;
* exact deadline is not fabricated;
* no required section is silently omitted.

A syntactically valid but semantically invalid response is still invalid.

---

# 11. VALIDATION RETRY

For invalid model output:

```text
first response
    ↓
validation
    ↓
invalid
    ↓
ONE validation retry
```

If the retry is still invalid:

```text
verdict_failed
```

and invoke the documented alert hook.

Do not turn validation retry into an unlimited retry loop.

Do not count validation retries as additional transport retries.

The rejected-verdict data must remain available to the downstream email layer so it can produce the documented:

```text
automatic assessment unavailable
```

raw-notice notification behavior.

Prompt 14 must NOT send the email itself.

---

# 12. PROVIDER / PROFILE / VERSION RECORDING

For every successful verdict record:

* `llm_profile_id`
* provider
* model
* `knowledge_base_version_id`
* `prompt_version`
* `schema_version`
* whether map/reduce summarisation occurred

Record the information required by the actual `Verdict` and `LLMCall` data models.

Do not invent a second verdict record model if the existing schema already provides the required entities.

---

# 13. TRANSPORT RETRY AND FALLBACK

Keep transport/provider failures separate from validation failures.

For:

* timeout;
* rate limit / 429;
* 5xx;

use the documented retry policy.

Default behavior specified by this prompt:

```text
up to 2 transport retries
    ↓
fallback profile
```

unless `docs/07` specifies a stricter or different policy.

For permanent/provider-rejection errors, follow the documented failure/fallback semantics.

---

# 14. DATA-POLICY GATE

Before constructing or sending any KB-containing request:

1. inspect the selected LLM profile;
2. verify `approved_for_company_docs`;
3. refuse the call if approval is false.

This rule applies to:

* primary profile;
* fallback profile;
* every retry.

An unapproved profile must NEVER receive company KB content.

The system must reject the request **before the payload containing KB content is sent**.

If the primary profile is not approved:

```text
primary rejected by data policy
    ↓
approved fallback if available
    ↓
otherwise documented verdict failure / awaiting approved provider
```

Do not send KB content to an unapproved provider and then reject afterward.

---

# 15. LLMCall RECORDING

Write an `LLMCall` record for every actual model invocation.

Record the fields required by `docs/03`, including where supported:

* tender;
* role;
* provider;
* model;
* profile;
* status;
* token usage;
* latency;
* cost;
* correlation/run context;
* error classification;
* timestamps.

Do not write an `LLMCall` for a model invocation that never occurred.

Do not claim zero-cost/zero-token usage when the provider did not return usage data; follow the project's documented unknown-value semantics.

---

# 16. OVERSIZED BUNDLES

If:

```text
document bundle + KB
```

cannot fit within the selected profile's context window, follow the documented map/reduce strategy.

At minimum:

```text
documents
    ↓
per-document map summaries
    ↓
reduce context
    ↓
verdict
```

Record:

```text
map_reduce_used = true
```

and the required provenance/metadata.

Do not silently drop documents.

Do not replace the complete KB with undocumented semantic retrieval.

If the oversized case cannot be safely processed under the documented policy, produce the appropriate failure state rather than fabricating an assessment.

---

# 17. TOKEN / BUDGET GUARD

Integrate with the existing cost-control mechanism from:

* `docs/07 §7.11`
* `docs/04 §4.13`

Use the configured KB-token warning threshold.

The prompt's proposed default:

```text
40%
```

is not a business decision and must remain configuration-driven where the project has not finalized it.

If the budget gate says:

```text
awaiting_budget
```

preserve that state.

Do not invent the monthly AI budget.

Do not bypass the budget guard.

---

# 18. VERDICT FORMATTER

Implement only the verdict-content formatter required for downstream email consumption.

It should produce the content structure required by the notification specification, including:

* Background
* Requirements
* exact deadline date/time/timezone
* requirement assessment
* Why We Can / Why We Cannot / relevant applicability explanation
* verdict
* confidence
* urgency
* incomplete-input warning where applicable
* assessment-unavailable state where verdict generation failed

The formatter must NOT:

* send email;
* select recipients;
* choose providers;
* plan attachments;
* create secure document links;
* modify Test Mode;
* invoke the mail provider.

The downstream notification layer owns those responsibilities.

---

# 19. FAILED-VERDICT DATA CONTRACT

When verdict generation ultimately fails, expose a structured result that downstream Prompt 11 can consume.

The contract must distinguish:

```text
VERDICT_AVAILABLE
VERDICT_FAILED
AWAITING_BUDGET
AWAITING_APPROVED_PROVIDER
```

or the exact equivalent already defined by the project's data model.

For `VERDICT_FAILED`, provide enough information for the notification layer to generate the documented:

```text
automatic assessment unavailable
```

banner while still allowing the raw tender notice to be sent according to the existing notification contract.

Do not implement the email send.

---

# 20. KNOWLEDGE-BASE DATA POLICY

Never log:

* KB contents;
* complete model prompts;
* complete model responses where prohibited;
* provider credentials;
* authorization headers.

Logs may contain metadata such as:

* tender ID;
* correlation ID;
* run ID;
* profile ID;
* provider/model;
* KB version;
* prompt/schema version;
* status;
* latency;
* token counts;
* cost;
* error class.

---

# 21. TESTS

Tests must use mocks/test providers.

Never use production credentials.

At minimum implement and run:

### Schema

* valid JSON accepted;
* missing field;
* invalid enum;
* wrong data type;
* non-JSON;
* semantically invalid output.

Every invalid response:

```text
retry once
→ if still invalid
→ verdict_failed
```

### Evidence

Test:

1. requirement supported by KB evidence;
2. KB citation present;
3. missing KB citation;
4. unsupported company-capability claim;
5. no KB evidence;
6. correct `no evidence on file for X` behavior;
7. fabricated citation rejected.

### Tender evidence

Test that tender facts are grounded in the tender bundle rather than incorrectly attributed to the KB.

### Data policy

Test:

```text
approved_for_company_docs = true
```

and:

```text
approved_for_company_docs = false
```

including fallback.

The unapproved profile must receive **zero KB content**.

### Retry

Test:

* timeout;
* 429;
* 5xx;
* permanent provider error;
* fallback success;
* fallback unavailable;
* fallback rejected by data policy.

### LLMCall

Verify one persisted `LLMCall` per actual model invocation.

### Verdict metadata

Verify:

* profile;
* model;
* KB version;
* prompt version;
* schema version;
* map/reduce flag.

### Incomplete inputs

Verify that:

```text
incomplete_inputs = true
```

propagates into the verdict and prevents missing evidence from becoming an invented negative capability claim.

### Deadline

Test:

* resolved deadline;
* unresolved deadline;
* conflicting deadline.

Verify no fabricated exact deadline.

### Urgency

Test:

* inside configured window;
* outside configured window;
* applicability verdict independent of urgency.

### Oversized bundle

Verify map/reduce executes when required and is recorded.

### Budget

Verify the budget guard seam and `awaiting_budget` behavior.

### Formatter

Verify the formatter produces the downstream contract without sending email.

---

# 22. INTEGRATION TEST

Use a deterministic end-to-end fixture:

```text
persisted tender
    ↓
document bundle
    ↓
Stage A PASS
    ↓
Stage B context assembly
    ↓
mock LLM
    ↓
validation
    ↓
Verdict
    ↓
verdict formatter
    ↓
notification data contract
```

Verify actual persisted state.

Do not invoke a real email provider.

Do not invoke a production LLM.

---

# 23. REGRESSION

Run the relevant tests from Prompts 04–13.

At minimum verify:

* source discovery;
* deduplication;
* persistence;
* document discovery;
* document acquisition;
* document processing;
* pipeline orchestration;
* notification seam;
* audit/timeline;
* Stage A triage.

Prompt 14 must not break earlier stages.

---

# 24. OUT OF SCOPE

Do NOT:

* build RAG;
* build embeddings;
* build vector search;
* implement Admin UI;
* implement email sending;
* implement attachment planning;
* implement secure links;
* change source adapters;
* decide target sectors/regions/minimum values;
* decide monthly AI budget;
* approve a production provider;
* invent KB content;
* implement application submission.

---

# 25. REPORT

Create:

```text
demo-evidence/prompt-14-implementation-report.md
```

Include:

* files changed;
* classes/functions changed;
* mapping to `docs/07` sections;
* schema version;
* prompt version;
* verdict data contract;
* failed-verdict data contract;
* provider/fallback behavior;
* data-policy enforcement;
* deadline integration;
* map/reduce behavior;
* budget guard integration;
* tests run and results;
* open decisions still pending;
* any assumptions explicitly made.

Also create:

```text
demo-evidence/prompt-14-implementation.json
```

containing machine-readable implementation status.

---

# 26. FINAL IMPLEMENTATION GATE

At the end, report:

```text
PROMPT 14: IMPLEMENTED — READY FOR INDEPENDENT BEHAVIORAL VERIFICATION
```

Do NOT claim `VERIFIED`.

Verification must be performed separately against the actual implementation.

Do not start Prompt 15.

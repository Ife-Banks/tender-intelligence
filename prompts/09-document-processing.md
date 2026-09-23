# Prompt 09 — Document Processing / Understanding

> Paste `prompts/00-master-context.md` first.

## Objective

Implement **document understanding** for documents successfully acquired by Prompt 08.

The purpose of this stage is to transform stored document bytes into a **deterministic, reusable, persisted representation of document content**, then construct one reusable **document bundle per tender**.

Prompt 09 must not perform AI tender verdicting.

The core question is:

> **What does each acquired document contain?**

The output must be suitable for later Stage A triage and Stage B AI verdict processing without requiring the documents to be downloaded or OCR'd again.

---

# 1. Read First

Read:

* `PROJECT_RULES.md`
* `docs/06-document-processing-spec.md` — full document, especially §6.2–§6.7
* `docs/04-pipeline-spec.md` — §4.2 stage 5, §4.5, §4.6, §4.12
* `docs/03-tender-data-model.md` — Document extraction fields/statuses/language
* `docs/02-technical-architecture.md` — dependency rules/library policy
* `prompts/06-tender-persistence.md`
* `prompts/07-document-discovery.md`
* `prompts/08-document-download.md`

Also inspect the actual Prompt 08 implementation and its final behavioral/integration verification.

Do not assume Prompt 08's report is correct without checking the code/contracts.

---

# 2. Architectural Boundary

Prompt 09 consumes the output of Prompt 08.

The intended flow is:

```text
Prompt 07
TenderAttachment
      ↓
Prompt 08
Acquired Document + stored bytes
      ↓
Prompt 09
Document Understanding
      ↓
Document Extraction Representation
      ↓
Tender Document Bundle
      ↓
Prompt 10 / later stages
Pipeline orchestration / triage / verdict
```

Prompt ownership:

```text
Prompt 07 = discover documents

Prompt 08 = acquire and safely store documents

Prompt 09 = understand/extract document content

Prompt 10 = orchestrate stages

Prompt 13 = AI triage

Prompt 14 = AI verdict
```

Do not move responsibilities between these stages.

---

# 3. Input Contract

Prompt 09 must consume persisted `Document` records and the configured storage abstraction established by Prompt 06/08.

For each document, the processing layer must be able to determine:

* document identity
* tender identity
* storage reference/path
* original filename
* MIME/type metadata
* acquisition status
* checksum
* parent/archive provenance where applicable
* correlation ID

Do not fetch documents directly from the original WAHO URL.

The source URL is provenance, not the document-processing input.

If the stored bytes are unavailable, processing must produce an explicit processing failure rather than silently downloading them again.

---

# 4. Required Formats

Implement and test the formats required by `docs/06` §6.2.

## 4.1 Native PDF

For a PDF containing machine-readable text:

* extract text directly
* preserve page boundaries
* preserve page ordering
* associate extracted text with its source page
* record extraction method
* produce deterministic output

Do not unnecessarily OCR a PDF that has usable native text.

---

## 4.2 Scanned / Image PDF

For a scanned or image-only PDF:

* detect that native extraction is insufficient
* render/process pages as required
* perform OCR
* preserve page boundaries
* preserve page ordering
* record that OCR was used
* retain sufficient provenance for later page-level evidence mapping

The OCR result must be represented as extracted document content, not merely logged output.

---

# 5. Vision-Based Processing

The specification allows a vision-capable provider/profile to be evaluated for scanned documents.

Treat this as an **explicit implementation option**, not an automatic requirement that every document be sent to an LLM.

If a configured provider/profile exposes:

```text
supports_vision = true
```

test the vision path using appropriate real or representative scanned WAHO documents.

Compare it against the local OCR path using measurable criteria appropriate to the documents, such as:

* extraction completeness
* preservation of important fields
* table readability
* multilingual handling
* page correspondence
* processing reliability
* latency/cost where measurable

Do not declare a winner based on intuition.

Record the evidence.

The selected default must be explicitly documented in the implementation/report.

If no approved vision provider/profile is available, do not invent one.

The implementation must remain functional using the approved non-vision extraction path.

Never silently send company-sensitive documents to an unapproved provider.

Never log raw document contents while testing provider behavior.

---

# 6. DOCX

Support DOCX extraction.

Extract:

* paragraphs
* meaningful textual content
* relevant document structure where practical
* tables

Preserve enough structure that downstream processing can distinguish ordinary text from table content.

Do not require Word/Office to be installed.

---

# 7. ZIP

Prompt 08 is responsible for ZIP acquisition and flattening.

Prompt 09 must therefore treat ZIP inner documents as ordinary `Document` inputs.

Do not reimplement ZIP discovery or ZIP extraction unless the existing architecture explicitly requires a processing-only archive operation.

If an archive Document itself requires processing, follow the documented data-model semantics rather than inventing a second ZIP pipeline.

---

# 8. Page Boundaries

Page boundaries are a first-class requirement.

The extraction representation must make it possible to answer:

> "Which page did this text/table come from?"

For PDFs, preserve:

```text
document
  ├── page 1
  │     ├── text
  │     └── tables
  ├── page 2
  │     ├── text
  │     └── tables
  └── ...
```

Do not flatten everything into one opaque string and discard page information.

This is required for later evidence citation and verdict reasoning.

For formats without native page semantics, use the appropriate structural representation rather than inventing fake PDF page numbers.

---

# 9. Tables

Tables must be represented structurally.

At minimum, preserve concepts such as:

```text
table
  ├── page/source location
  ├── headers
  ├── rows
  └── relevant metadata
```

Test tables representing:

* deadlines
* eligibility requirements
* evaluation criteria/matrices

Do not reduce tables exclusively to a whitespace-flattened text string.

The bundle may contain a textual rendering of a table as well as its structured representation, but the structured representation must remain available.

---

# 10. Multilingual Documents

Do not assume that a tender has one language.

The system must support:

```text
EN
FR
PT
```

including cases where a single tender contains documents in different languages or a single document contains multiple languages.

Record language at:

```text
document level
```

and:

```text
bundle level
```

where required by the specification.

Language detection is metadata and must not silently alter or translate the source content.

Do not translate source material as part of Prompt 09 unless the authoritative specification explicitly requires translation.

---

# 11. Extraction Representation

Create a stable internal representation for extracted content.

It should be capable of representing at least:

```text
DocumentExtraction
├── document identity
├── extraction status
├── extraction method
├── language metadata
├── page/section boundaries
├── extracted text
├── structured tables
├── provenance
├── processing metadata
└── correlation ID
```

Use the exact fields/statuses required by `docs/03-tender-data-model.md` and `docs/06-document-processing-spec.md`.

Do not invent competing status enums where the specification already defines them.

---

# 12. Bundle Construction

After individual documents have been processed, construct one reusable **document bundle per tender**.

The bundle must provide:

```text
Tender
  ↓
ordered documents
  ↓
document extraction content
  ↓
pages/sections
  ↓
structured tables
  ↓
language metadata
  ↓
document-level statuses
  ↓
bundle-level completeness metadata
```

The bundle should preserve document boundaries.

Do not concatenate every document into an anonymous text blob.

A downstream consumer must be able to identify:

* which document supplied a fact
* which page supplied it
* which extraction method produced it
* whether that document was successfully processed

---

# 13. Bundle Persistence and Reuse

Persist the reusable extraction/bundle representation according to the existing data model.

Use:

```text
Document.extracted_text_ref
```

where that is the specified persistence reference.

Do not store large extracted content directly in arbitrary database fields unless the existing data model explicitly requires it.

The persisted representation must be retrievable by later stages.

---

# 14. Idempotency

Extraction must be deterministic and idempotent.

For the same:

```text
stored document bytes
+
same extraction configuration/version
```

the resulting extraction should be materially identical.

A rerun must not unnecessarily:

* OCR the same unchanged document
* recreate uncontrolled duplicate bundle records
* duplicate extraction artifacts

Use the existing document checksum and persisted extraction metadata to determine whether reuse is possible.

Do not reinterpret document checksum as Prompt 05 tender deduplication.

Prompt 05 remains responsible for tender-level:

```text
NEW
UPDATE
UNCHANGED
```

---

# 15. Extraction Versioning

Because extraction behavior can change when libraries/configuration change, record sufficient processing metadata to establish how the extraction was produced.

Where supported by the existing data model, record:

* extraction method
* processor/library/version
* OCR engine/version
* vision provider/model/profile if used
* extraction configuration/version
* processing timestamp
* correlation ID

This allows later stages to distinguish:

```text
same bytes + same processor
```

from:

```text
same bytes + changed processor
```

Do not silently reuse an old extraction if the specification requires reprocessing after an extraction implementation/configuration change.

---

# 16. Per-Document Failure Semantics

A single bad document must not kill the entire tender.

Required statuses include the exact values specified by the data model, including:

```text
extracted
ocr_failed
parse_failed
```

where applicable.

For example:

```text
Tender
├── document A → extracted
├── document B → extracted
├── document C → parse_failed
├── document D → extracted
└── document E → extracted
```

The tender continues downstream.

However, the bundle must record that its inputs are incomplete.

Compute:

```text
incomplete_inputs = true
```

when required by the pipeline semantics.

Do not silently hide failed documents.

Do not convert:

```text
some documents failed
```

into:

```text
all documents successfully understood
```

---

# 17. No AI Verdict Logic

Prompt 09 must not implement:

* `APPLY`
* `DO NOT APPLY`
* `APPLY WITH CONDITIONS`
* company-fit reasoning
* eligibility verdicts
* AI tender recommendations
* Stage A triage
* Stage B verdict generation

The output is evidence/content representation only.

Later prompts consume this representation.

---

# 18. Security

Never write raw document contents into logs.

Never log:

* full PDF text
* OCR output
* DOCX contents
* document bytes
* API keys
* provider credentials

Errors should identify the document/correlation context without exposing sensitive content.

Follow the project security specification for provider usage.

---

# 19. Library Selection

Use the proposed libraries in:

```text
docs/06-document-processing-spec.md §6.6
```

but validate them through actual fixtures.

Do not introduce unnecessary dependencies.

Before adding a library:

1. verify it fits the project's dependency rules;
2. verify licensing/compatibility where required by the project;
3. test it against representative fixtures;
4. record the choice.

Do not build speculative abstraction layers for capabilities not required by the specification.

---

# 20. Required Tests

Implement behavioral/integration tests covering at least:

## Native PDF

Fixture:

```text
native PDF
```

Verify:

* text extracted
* page boundaries preserved
* page mapping correct
* deterministic output

---

## Scanned PDF

Fixture:

```text
scanned/image PDF
```

Verify:

* native extraction insufficiency detected
* OCR executed
* text extracted
* page mapping preserved

If a vision profile is configured:

* execute the vision path
* compare against OCR
* record measurable results
* record selected/default path

If no approved vision profile is available:

* verify the non-vision path works
* record vision as unavailable rather than fabricating a result

---

## DOCX

Verify:

* text extraction
* structural content
* tables where present
* deterministic output

---

## Tables

Use representative tender tables for:

* deadline
* eligibility
* evaluation matrix

Verify structured rows/columns survive into the bundle.

---

## Multilingual

Use FR/EN/PT material.

Verify:

* document language metadata
* bundle language metadata
* no incorrect single-language assumption

---

## Reprocessing / Idempotency

Process the same document twice.

Verify:

* extraction artifact is reused when valid
* no unnecessary OCR
* no uncontrolled duplicate artifacts
* deterministic result

Then test the behavior when the extraction processor/version changes if the implementation supports version-aware invalidation.

---

## Partial Failure

Given five documents:

```text
A → valid
B → valid
C → corrupt
D → valid
E → valid
```

Verify:

```text
A = extracted
B = extracted
C = parse_failed or ocr_failed
D = extracted
E = extracted
```

and:

```text
incomplete_inputs = true
```

where required.

---

## Correlation

Verify every persisted extraction/bundle record is correlation-stamped according to the project's data model.

---

# 21. Regression Tests

Run the existing tests for:

```text
Prompt 04
Prompt 05
Prompt 06
Prompt 07
Prompt 08
```

Prompt 09 must not regress earlier stages.

---

# 22. Required Behavioral Verification

Do not consider Prompt 09 complete merely because unit tests pass.

At minimum verify the actual seam:

```text
stored Document
      ↓
storage read
      ↓
processor
      ↓
Document extraction metadata
      ↓
persisted extraction artifact
      ↓
Tender document bundle
      ↓
bundle reload
```

The persisted bundle must be usable by a later consumer without rerunning document acquisition or extraction.

---

# 23. Report

At completion report:

## Files changed

List every changed file.

## Support matrix

Use:

| Capability         | Supported | Method | Verified |
| ------------------ | --------- | ------ | -------- |
| Native PDF         |           |        |          |
| Scanned PDF        |           |        |          |
| OCR                |           |        |          |
| Vision             |           |        |          |
| DOCX               |           |        |          |
| Tables             |           |        |          |
| FR                 |           |        |          |
| EN                 |           |        |          |
| PT                 |           |        |          |
| Page boundaries    |           |        |          |
| Bundle persistence |           |        |          |
| Idempotent reuse   |           |        |          |

## Scanned-PDF decision

State:

* selected default
* alternatives tested
* evidence
* limitations
* provider/model/profile if applicable
* why the selected default is appropriate for the tested workload

Do not turn this into a subjective "best" claim. Report the measured evidence and resulting engineering choice.

## Bundle schema

Show how the implementation maps to the existing data model.

## Tests

Report exact commands and results.

## Regressions

Report Prompt 04–08 status.

## TODOs

List unresolved issues without hiding them.

## Final status

Use exactly one:

```text
PROMPT 09: VERIFIED — READY FOR PROMPT 10
```

or:

```text
PROMPT 09: NEEDS FIXES
```

Do not begin Prompt 10 unless Prompt 09 has passed its behavioral/integration verification.

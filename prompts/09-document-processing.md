# Prompt 09 — Document Processing

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/06-document-processing-spec.md` (full — §6.2 understanding, §6.3 bundle, §6.4–6.7)
- `docs/04-pipeline-spec.md` (§4.2 stage 5, §4.5, §4.12 bundle)
- `docs/03-tender-data-model.md` (document extraction fields/statuses, language)
- `docs/02-technical-architecture.md` (dependency rules for proposed libraries)

## Task

Implement **document understanding**: extract text/structured content from every stored
document and produce one reusable, persisted **document bundle** per tender — no LLM verdict
logic yet.

1. Support the required formats for WAHO documents (`docs/06` §6.2):
   - **native PDFs** → direct text extraction;
   - **scanned/image PDFs** → OCR; also test the **vision option** (`supports_vision`
     profile) on real scanned WAHO files and decide a default with recorded evidence
     (`docs/06` §6.2, §6.7 — proposed choices must be justified by tests);
   - **DOCX** → text extraction;
   - **ZIP** → already flattened by the download stage; treat inner files normally;
   - **tables** → structured table extraction (deadlines, eligibility, evaluation matrices)
     serialised into the bundle, not a raw text dump (`docs/06` §6.2);
   - **multilingual** (FR/EN/PT within one notice) → per-document and per-bundle language
     tags; never assume a single language.
2. Preserve **page boundaries** so the vision/OCR fallback and the verdict step can map
   content back to pages (`docs/06` §6.2).
3. Write the **bundle** (plain text + metadata + per-document extraction method + status) and
   persist it via `Document.extracted_text_ref` so it is **reusable** — never re-OCR the same
   PDF when a stage re-runs (`docs/06` §6.3).
4. Per-document `extraction_status`: `extracted` / `ocr_failed` / `parse_failed`; a failing
   document is a warning, never a pipeline failure (`docs/06` §6.4). Record whether
   incompleteness applies for `incomplete_inputs` (`docs/04` §4.6).
5. Keep extraction **deterministic and idempotent** (same stored bytes → same bundle).

## Out of scope

- Downloading bytes — `prompts/08-document-download.md`.
- AI triage / verdict — `prompts/13/14-ai-*.md`.
- Sending email — `prompts/11-test-mode-email.md`.
- Writing discovery/attachment rows — `prompts/06/07`.

## Tests

- Fixture native PDF → text extracted, correct page map.
- Fixture scanned PDF → OCR (and vision path, when a vision profile is configured) →
  comparable text; record the chosen default + evidence.
- Fixture DOCX → text extracted.
- Fixture tables (deadline/eligibility/eval matrix) → structured rows in the bundle.
- Mixed-language fixture → per-doc and per-bundle language tags correct.
- Re-run → bundle reused, no re-OCR (deterministic + idempotent).
- One corrupt PDF among five docs → that doc `ocr_failed`/`parse_failed`, others extracted,
  `incomplete_inputs` flag computed.
- Correlation-stamped rows only.

## Rules

- Library choices follow the proposed stack in `docs/06` §6.6 but must be validated by the
  fixtures above and recorded; nothing speculative ships.
- Requiring an LLM here (vision) is fine but the decision must be explicit + audited
  (`docs/15` #15); never silently read doc bytes into log lines.

## Report

Files changed; support matrix (PDF native/OCR/vision/DOCX/tables/lang); chosen default for
scanned PDFs + evidence; bundle schema mapping to `docs/03`; tests + results; TODOs.
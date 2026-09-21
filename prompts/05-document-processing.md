# Prompt 05 — Document Processing

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/06-document-processing-spec.md` (full)
- `docs/04-pipeline-spec.md` (§4.2 stage 4–5, §4.5, §4.6)
- `docs/03-data-model.md` (§Document)
- `docs/13-open-decisions.md` (O18 — vision/OCR accent; decide by testing, don't assume)

## Task

Implement **document acquisition and processing** (Document Fetcher + Document Understanding) producing a reusable document bundle per tender.

1. **Fetcher:**
   - Download every linked document on a tender's detail page; unzip ZIP archives and recurse into members.
   - Preserve original filenames and source URLs; store a local/cloud copy; never rely on the source URL staying valid.
   - Record per document: `mime_type`, size, best-guess `language`, `checksum`.
   - Per-document failure tolerance: one broken link must not block the others; record `download_status` and continue.
2. **Understanding:**
   - Native PDF text extraction.
   - Scanned/image PDFs: OCR path (vendor/library your choice; **no vendor is mandated**, `docs/06` §6.6) **and** a clearly-flagged vision-capable-model option for `supports_vision` profiles. Leave a switch; do not choose a default until you test both on a scanned fixture and record the comparison per O18.
   - DOCX text extraction.
   - ZIP recursion handled as individual documents.
   - Table-aware extraction for deadlines/eligibility/evaluation matrices; preserve structure.
   - Multi-language: preserve/tag language per document so the AI stage can work in the right language.
   - Keep page boundaries available for the vision path.
3. **Bundle output:** one structured, reusable "document bundle" per tender (plain text + metadata per document), persisted/referenced so the AI stage never re-extracts (no re-OCR).
4. **Statuses:** per-document `download_status` and `extraction_status`; per-stage summary (e.g. "4 of 5 extracted") for the tender timeline; `incomplete_inputs` handling set for downstream (`docs/04` §4.6).

## Out of scope

- AI triage/verdict, email, admin UI.
- Choosing/committing to a specific OCR **vendor** as a mandated default (option is fine; the decision belongs to O18-driven testing later).
- Real cloud storage vendor choice (storage abstraction exists in infra prompt/`docs/12`).

## Tests

- Fixtures: native PDF, scanned PDF, mixed PDF, DOCX, ZIP with members, corrupt/unreadable file, non-PDF mislabelled as PDF, oversized bundle, French/Portuguese content.
- Fetch failure isolation test (one 404 among many ⇒ others succeed + flag).
- ZIP recursion test; duplicate member handling via checksum.
- Deterministic checksum test (same bytes ⇒ same checksum; changed bytes ⇒ different).
- Reuse test: asking for the bundle twice does not re-OCR/re-extract.
- OCR vs vision path: both code paths excercised via fixture; results recorded to a decisions note (do not silently pick).
- Per-document failure → `incomplete_inputs` propagate correctly to the bundle/tender.

## Rules

- Failures are per-document; never fatal to the tender unless all inputs are unusable (and even then the pipeline flags, doesn't crash).
- Everything tagged with correlation ID; statuses recorded; nothing silently dropped.
- Do not let any framework choice leak secrets into logs.

## Report

Files changed; mapping to `docs/06`; extraction-method default decision + comparison of OCR vs vision on fixtures; tests run + results; TODOs.
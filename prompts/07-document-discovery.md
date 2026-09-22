# Prompt 07 — Document Discovery

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/05-source-adapter-spec.md` (§5.1–5.3 detail-page and attachment discovery)
- `docs/06-document-processing-spec.md` (§6.1 acquisition gate)
- `docs/04-pipeline-spec.md` (§4.2 stage 4)
- `prompts/01-architecture.md` (interface/DTO shapes)

## Task

Implement `get_detail()` and `get_attachments()` on the WAHO adapter — **discovering** every
document linked on a tender's detail page and returning loaded detail + attachment metadata.
No downloading, extraction, or AI here.

1. `get_detail(identifier)` returns the **verified neutral detail DTO** (`TenderDetail`):
   full title, description/body, all deadline components (date, time, timezone), reference
   numbers, applicant/stakeholder info, and the raw HTML retained for the pipeline.
2. `get_attachments(identifier)` returns **every linked document** as `TenderAttachment`:
   original filename, source URL, MIME guess, size (if advert), top-level vs **inside-ZIP**
   marker, and a stable checksum target. Do not actually fetch bytes unless required to know
   final truth (`docs/06` §6.1).
3. **ZIP awareness:** identify `.zip` links so the download stage (`prompts/08`) can unzip
   and recurse. Surface inner-file expectations only at the level the source page allows.
4. Keep every result correlation-stamped and source-API-shaped — nothing WAHO-specific may
   leak into the pipeline (`docs/15` #10).
5. Same adapter-style safeguards as the discovery prompt: no assumed selectors, fixtures for
   offline tests, dry-run support, structured error codes (`source_unreachable`,
   `parser_mismatch`).

## Out of scope

- Downloading/hashing/storing bytes — `prompts/08-document-download.md`.
- Extraction/OCR/bundle — `prompts/09-document-processing.md`.
- Deduplication, persistence, email, AI.
- Attachments enumeration that requires fetching the file to determine.

## Tests

- Fixture detail pages (FR/EN/PT, deadline formats) → correct `TenderDetail`, with timezone
  strings preserved, never parsed to a naive local timestamp.
- Fixture list of links → attachment metadata correct, ZIPs flagged.
- Missing detail / 404 → structured error, not a crash.
- ALL attachments surfaced; no dedup or filtering here.
- Correlation stamp present.

## Rules

- Deadlines: always retain original timezone string + normalize to UTC consistently
  (`docs/05`); never silently assume the server's timezone.
- Every discovered document ends in a `Document` row via `prompts/06` seams — discovery alone
  writes nothing (delegated to the download stage once its bytes are known).

## Report

Files changed; mapping to `TenderDetail`/`TenderAttachment` DTOs; fixture list; verified-vs-
unknown WAHO attachment behaviours; tests + results; TODOs.
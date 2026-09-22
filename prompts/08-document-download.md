# Prompt 08 — Document Download

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/06-document-processing-spec.md` (§6.1 acquisition — download every linked doc, ZIP
  unzip+recurse, preserve filenames/URLs, local copy, records, per-doc failure)
- `docs/04-pipeline-spec.md` (§4.2 stage 4, §4.5, §4.6)
- `docs/10-security-spec.md` (storage hardening relevant here)
- `docs/03-tender-data-model.md` (`Document` rows/statuses)

## Task

Implement the **document fetcher**: turn `TenderAttachment` metadata from
`prompts/07-document-discovery.md` into stored bytes with per-document statuses and checksums.

1. **Download every** attachment: preserve original filename and source URL; store a local/
   cloud copy referenced from `Document.storage_path` so the tender never depends on the
   source URL staying valid (`docs/06` §6.1).
2. **ZIP: unzip and recurse.** Inner files become additional `Document` rows with their own
   `download_status`/`extraction_status`. Return the flat doc set (not nested).
3. Per document record: `mime_type`, size, best-guess `language`, **checksum**
   (`docs/06` §6.1, §6.2 checksums). Checksum doubles as the change-detector for updates
   (`prompts/05`) and prevents refetching/duplicate sends.
4. **Per-document tolerance:** one broken link never blocks the rest. Failures are recorded
   per document and surfaced as warnings; a fully-missing doc set still passes the tender
   downstream (`docs/06` §6.5, `docs/04` §4.5).
5. Fetch **politely** (rate limits, backoff) and with timeouts; network errors map to
   structured codes (`document_download_failed`).
6. Idempotent by checksum: an already-downloaded, same-checksum document is left untouched
   (`docs/04` §4.7).

## Out of scope

- Text/OCR/table extraction — `prompts/09-document-processing.md`.
- Enumerating links — `prompts/07-document-discovery.md`.
- Verdict/email incompleteness handling (already flagged upstream, consumed downstream).

## Tests

- Fixture attachments → bytes stored, filename+URL preserved, checksum correct and stable.
- ZIP fixture → recursive members each become a `Document` with its own status.
- Broken/aborted download for one of five docs → other four stored, one `failed` status,
  no pipeline crash.
- Duplicate re-download skipped when checksum matches.
- Retry/backoff on transient network error (mocked).
- Correlation-stamped rows only.

## Rules

- Never trust a remote filename for local path building — sanitize (path traversal, `..`)
  (`docs/10`).
- Store location is configurable (filesystem/bucket) via the storage seam; no hardcoded paths.

## Report

Files changed; mapping to `Document` fields; ZIP recursion behaviour; per-doc failure tests;
checksum design; storage seam; tests + results; TODOs.
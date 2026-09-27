# Controlled Live Tender-to-Email Acceptance Test

**Gate: `BLOCKED — NO NEW/UPDATED TENDER`**

## Check record

- Evidence recorded: 2026-09-26 18:26 UTC (19:26 Africa/Lagos)
- Source: `WAHO Live Demo` (source ID `2`)
- Live worker entrypoint: not run; the safe source dry-run found no eligible tender.
- Test Mode: ON.
- Test recipient category: an active `dev_alert` recipient is configured; address omitted.
- Mail provider: Sendlib is active, credentials are configured, breaker state is closed.

## Discovery and deduplication

The Admin source test endpoint ran the existing read-only dry-run against the configured WAHO source. It returned:

- Correlation ID: `202939c6-6c34-4b79-b8ab-a5975223864b`
- Discovery: `COMPLETED`, 10 candidate listings
- Deduplication: `COMPLETED`
- Planned actions: `no new or changed tenders; nothing further to do`
- Persisted: no
- Email sent: no

A second read-only dry-run was made after re-checking the DeepSeek approval setting:

- Correlation ID: `f4d115fd-4ec1-43c6-a90f-73879eca4657`
- Discovery: `COMPLETED`, 10 candidate listings
- Deduplication: `COMPLETED`
- Planned actions: `no new or changed tenders; nothing further to do`
- Persisted: no
- Email sent: no

The database contained 10 existing WAHO tender rows before the dry-run. The current listing produced no new or materially changed work item, so no tender ID was eligible for this acceptance run. Existing rows had no persisted verdict. No tender data, fingerprints, or deduplication state were altered to force processing.

## Provider and safety configuration

- Triage role: active Token Harbor profile using `deepseek-v4-flash:free`, approved for company documents.
- Verdict role: the same active approved profile.
- API key configured: yes (value not recorded).
- `approved_for_company_docs`: **true** on the latest live check; the setting changed after the initial check.
- NVIDIA: not selected.
- Test Mode: ON; no production recipient routing or mail was attempted.

## Acceptance stages

The following were not executed because no eligible tender was available: detail retrieval, attachment discovery/acquisition, document processing, Stage A, Stage B, verdict persistence, notification dispatch, Sendlib delivery, actual inbox receipt, and a post-processing repeat crawl. No approved worker demo entrypoint for the repository's offline integration fixtures was found; those fixtures are test harnesses and do not constitute the requested live worker run.

## Result

This is a blocked acceptance attempt, not a product failure. DeepSeek approval is now satisfied. The remaining condition is a naturally new or materially updated WAHO tender. Keep Test Mode ON and use the normal worker entrypoint once one appears.

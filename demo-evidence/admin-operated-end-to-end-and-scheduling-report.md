# Admin-Operated Tender Processing and Scheduling

## Gate

**ADMIN-OPERATED END-TO-END & SCHEDULING: NEEDS FIXES — LIVE ACCEPTANCE PENDING**

The Admin controls, API boundaries, background operations, scheduler lifecycle, and durable run provenance are implemented. Offline verification passed. The live click-through, Sendlib test-recipient delivery, and temporary-cadence scheduler acceptance were not run against the operator's running Admin process, so no live email or schedule result is claimed.

## Implemented

- Sources have a **Run Now** action. It requires an active source and Test Mode ON, uses the ordinary discovery and deduplication pipeline, records a `manual_source` trigger, and returns a correlation ID for operation status.
- Persisted tenders have a **Process Now** action. It requires Test Mode ON and re-runs downstream detail, acquisition, processing, triage, verdict, and notification stages for the selected tender. Discovery, deduplication, and persistence are explicitly marked as not run. The tender identity and source `last_run_at` are not changed by this operation.
- Both actions use the regular notification dispatcher and Test Mode recipient guard. They do not bypass provider approval, configured budgets, schema validation, evidence requirements, or incomplete-input handling.
- Admin API operations expose progress and safe outcome data. Concurrent manual or scheduled runs of one source are rejected/serialized by the per-source lock. Queue and completion events are audit logged without secret values.
- Admin-owned coordinator instances host the scheduler. It checks due active sources using the configured polling interval, uses the current persisted crawl frequency at each pass, and keeps a single source failure from terminating the scheduler loop. The source screen shows last run, last success/failure, and an estimated next run.
- Run provenance stores the trigger and, for a manual existing-tender run, the tender ID. Tender timelines can find that run even when it fails before creating a triage link.
- Migration `0015_run_trigger` adds run trigger and tender association provenance.

## Verification

- `python -m compileall -q src/tender_intelligence migrations/versions/0015_run_trigger.py`: passed.
- `python -m pytest tests/integration/test_orchestrator_pipeline.py -q`: passed, including existing-tender reprocessing without listing requests or tender identity/source timestamp changes.
- `python -m pytest tests/integration/test_admin_api_prompt15.py -q -k "not test_admin_api_works_without_login_and_settings_read_does_not_write"`: passed, including Test Mode gating and audit coverage for both operator actions.
- `node --test tests/ui/*.test.mjs`: 28 passed.
- The excluded Admin API test starts the unconfigured global app and attempted to connect to the configured remote database, which this sandbox cannot reach. It was excluded from the offline API run; no remote database changes were made.

## Live acceptance still required

The currently running Admin process was started before these changes and must be restarted to load the new API routes, migration, and UI controls. After applying the migration, use the UI with Test Mode ON and the configured active dev-alert recipient:

1. Use **Process Now** on an existing persisted tender. Inspect all stage results and the tender timeline; confirm the run trigger is `manual_tender` and the run is associated with the selected tender.
2. Confirm any resulting message is labelled `[TEST]` and delivered through the configured Sendlib test path to the active dev-alert recipient. Do not count provider configuration or API acceptance alone as proof of receipt.
3. Temporarily set one active source's crawl frequency to a short interval, wait for a scheduler-driven run, inspect its `scheduled` trigger and run status, then restore the original frequency. Confirm the scheduler continues after any source reachability warning.

No production recipient, sender, provider credential, or company-content value is included in this evidence. Prompt 17 was not started.

# Prompt 12 — Audit & Timeline

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/04-pipeline-spec.md` (§4.8 correlation IDs & run history, §4.9 alerting)
- `docs/03-tender-data-model.md` (RunHistory, NotificationLog, AlertEvent, ConfigChangeLog)
- `docs/08-email-notification-spec.md` (§8.10 alerts, §8.11 attached-vs-linked logging)
- `docs/10-security-spec.md` (ConfigChangeLog scrubbing rules)

## Task

Implement the **audit/observability layer**: per-tender timelines reconstructable from
correlation IDs, run-history/notification/alerts persisted, and the alerting manager that
`prompts/10` and `prompts/11` hook into.

1. **Correlation timelines** (`docs/04` §4.8): from a correlation ID, reconstruct in under a
   minute: seen → documents fetched → extraction → triage → verdict → email, including which
   provider/profile was used. Provide the query seam the admin timeline UI
   (`prompts/16`) and alert deep-links consume.
2. **RunHistory** persistence is complete here (start/end, listings found, new count, error
   count, failed correlation IDs) if not already wired from `prompts/10`; guarantee
   cross-stage consistency.
3. **NotificationLog** (`docs/08` §8.11): store `attachments[]`, `links[]`, `recipients[]`
   snapshot at send time, `provider_used`, `dedupe_key`, `possible_duplicate`, `status` —
   recipient edits never rewrite history.
4. **ConfigChangeLog** (`docs/04` §4.11): every config change audit-logged, **never secret
   values** (`docs/10`).
5. **Alert manager** (`docs/04` §4.9): throttled alerts → dev alert list through the same
   provider chain; one when a source/stage becomes unhealthy, one on recovery, reminders at a
   configurable interval; triggers per `docs/04` §4.9 (source fails N runs, parser mismatch,
   AI fail/invalid output after retry, budget 80%/100%, email fails on every provider, breaker
   opens, daily health digest). Alerts carry correlation ID + deep link to the timeline.
6. **Incomplete-input awareness:** surface `incomplete_inputs` from `prompts/09` into the
   timeline/email trail so "docs missing" is never silent (`docs/04` §4.6).

## Out of scope

- The emails themselves (templates, chain, planner) — `prompts/11-test-mode-email.md`.
- Admin HTTP/UI screens — `prompts/15/16-admin-*.md` (consumers only).
- Retry/backoff of sends — owned by `prompts/11`.

## Tests

- Generate a full fixture run → timeline reconstruction returns all expected stages in order
  with provider/profile detail.
- NotificationLog snapshot immutability (edit recipient later → log unchanged).
- ConfigChangeLog stores change, never secrets (audit content test).
- Alert throttling: N failures → exactly one alert + recovery alert + reminder cadence.
- Daily health digest queue built when scheduling next run.
- Search/big-data cost check: reconstruction under the acceptable cost bound.

## Rules

- Timelines are read-only views over persisted rows — never synthesized.
- Alerts never carry secret values or full doc text (`docs/10`, `docs/15` #8).

## Report

Files changed; timeline query design + indexes; alert manager implementation + trigger table;
NotificationLog/ConfigChangeLog schema mapping; costs; tests + results; TODOs.
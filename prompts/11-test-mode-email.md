# Prompt 11 — Test-Mode Email (Notification Layer)

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/08-email-notification-spec.md` (full — §8.1–8.17)
- `docs/04-pipeline-spec.md` (§4.2 stages 8–9, §4.4 retry, §4.7 idempotency, §4.9 alerting,
  §4.14 update emails)
- `docs/03-tender-data-model.md` (Notification entities)
- `prompts/13-ai-triage.md`, `prompts/14-ai-verdict-engine.md` (outputs this layer consumes)

## Task

Implement the **notification/email layer** consumed after triage+verdict, designed so all
delivery is validated under **Test Mode** — no business recipients yet.

1. **MailProvider abstraction + first adapter (Sendlib)** (`docs/08` §8.2): one interface, one
   adapter per provider; `type`, encrypted write-only credentials, `from_address/from_name/
   reply_to`, `priority`, `active`, and the **capabilities** block (`max_attachments`,
   `max_attachment_mb`, `max_message_mb`, `daily_limit`, `rate_limit_per_min`,
   `needs_verified_domain`). Providers 2/3 stay TBD (`docs/13` O-open).
2. **Provider chain / failover** (`docs/08` §8.3): transient retry w/ exponential backoff;
   permanent errors skip to next provider + alert; every attempt recorded
   (`NotificationAttempt`); final log says which provider delivered; **circuit breaker** with
   cooldown + alert; **whole-chain-down** → `pending_retry` in a durable outbox + the **red
   banner** as the persisted fallback signal.
3. **Test Mode routing** (`docs/08` §8.4–8.5): while ON, every tender email goes **only to the
   dev alert list**, subject prefixed `[TEST]`. Dev-recipient list validated (`alert_types[]`,
   `min_severity`, `active`); first dev recipient seeded from an env var so alerts work before
   the admin app is opened; system **refuses to deactivate/delete the last active dev
   recipient**.
4. **Attachment planner** (`docs/08` §8.8): compute the attachment set, walk the chain and pick
   the **first provider whose capabilities fit the whole set**; else attach what fits on the
   best provider and link the rest as **secure expiring links** (default 14 days,
   configurable); if nothing fits, all links. Record exactly `attachments[]` vs `links[]`.
5. **Template** (`docs/08` §8.15): exact structure — Background, Requirements (exact deadline
   date/time/timezone), Why We Can/Cannot Apply ending APPLY / DO NOT APPLY / APPLY WITH
   CONDITIONS + confidence; notes footer (documents attached/linked-with-expiry/failed,
   `incomplete_inputs`); URGENT marker when set; `[TEST]` prefix in Test Mode; plain-text +
   HTML.
6. **Recipient handling** (`docs/08` §8.6): business tender list model in place with
   `delivery`, `recipient_scope`, `receives`, `active` — but **no business sends yet**; in Test
   Mode they are always routed to dev. Recipients from configured lists, never hardcoded.
7. **Idempotency** (`docs/08` §8.12): dedupe key = tender + verdict + recipient-set hash,
   stored and sent as an email header; re-runs never re-notify; ambiguous provider-timeout
   duplicates accepted + flagged `possible_duplicate`.
8. **Update emails** (`docs/08` §8.14, `docs/04` §4.14): lower priority, new/changed documents
   only, current deadline, verdict re-run only if material — following the §8.2 update
   template.

## Out of scope

- Triage/verdict production — `prompts/13/14`.
- Sending to business recipients / turning Test Mode off — that is the go-live gate
  (`docs/12`), owned later.
- Admin UI for recipients — `prompts/16-admin-ui.md`.
- Deep alert-event persistence — `prompts/12-audit-and-timeline.md` (alert sends may hook here).

## Tests

- Sendlib adapter against a mock HTTP service: behaviour + timeout/429/5xx → retry; bad creds
  → permanent → next provider + alert.
- Circuit breaker trips after N failures, cools down.
- Planner: whole set fits on provider 1; doesn't fit anywhere → links with expiry; fits-partial
  → attach-on-best + links.
- Template renders exact §8.15 structure for new + update emails; `[TEST]` prefix present when
  Test Mode ON; incomplete_inputs footer populated when any doc failed.
- Idempotency: same tender re-run sends exactly once; possible_duplicate flagged path tested.
- Dev-recipient refusal: last active dev recipient cannot be deactivated/deleted.
- Durable outbox: pending_retry survives a process restart.

## Rules

- Test Mode remains ON by default; nothing may send to business recipients while ON (`docs/08`
  §8.4).
- Provider credentials are encrypted write-only, never logged (`docs/10`).
- Send a fee/attachment correctness note: base64 inflates ~⅓, and budget/limits from §8.8.

## Report

Files changed; provider chain + breaker impl; planner logic; Test Mode routing proof (tests
cover it); template render fixtures; idempotency tests; tests + results; TODOs.
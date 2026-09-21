# Prompt 08 — Email Notifications

> Paste `prompts/00-master-context.md` first.

## Read

- `PROJECT_RULES.md`
- `docs/08-email-notification-spec.md` (full)
- `docs/04-pipeline-spec.md` (§4.2 stage 9, §4.4, §4.7, §4.9)
- `docs/03-data-model.md` (§NotificationLog, §NotificationAttempt, §MailProvider, §Recipient, §AlertEvent)
- `docs/10-security-spec.md`
- `docs/13-open-decisions.md` (O3, O4, O13 — sender mailbox, plan/providers; Sendlib notes in `docs/08` §8.8)

## Task

Implement **notification delivery + the mail provider abstraction**, including the Sendlib adapter (Phase 0/1 of `docs/12`), the failover chain, attachment planner, secure links, and the §8.x email templates.

1. **`MailProvider` interface + registry:** per-provider config (type, encrypted credentials, from/reply-to, priority, active + `capabilities` block) per `docs/03` §MailProvider. Provide a clean seam for SMTP/transactional providers later.
2. **Sendlib adapter:** implement per the **research notes** in `docs/08` §8.8 / `docs/05`…-style vendor notes; re-verify stated limits against Sendlib's `/docs` before relying on them (they are vendor-stated and dated 21 Sep 2026). Wire the endpoints/limits into config. Handles Google-OAuth-connected sender mailbox.
3. **Chain/failover:** as `docs/08` §8.3 — transient retries (default 2, exponential backoff), permanent errors skip to next provider + alert; per-attempt `NotificationAttempt` records; `NotificationLog.provider_used` on success; circuit breaker (N consecutive fails ⇒ cooldown + alert; test email/probe closes).
4. **Idempotency:** dedupe key (tender + verdict + recipient-set hash) stored + set as email header; `pending_retry` outbox so failed messages survive restarts; `possible_duplicate` flag for ambiguous timeout cases.
5. **Attachment planner:** per `docs/08` §8.8 — walk the chain picking first provider whose capabilities fit the whole attachment set; attach what fits, link the rest; if nothing fits, all links; log attached-vs-linked. Respect base64 blow-up (~⅓) in `max_message_mb` math.
6. **Secure links:** signed, expiring URLs served from the document archive (default expiry 14 days `[PROPOSED]`, configurable); each linked document listed by filename in the notes footer.
7. **Recipient routing:** tender list vs dev alert list; Test Mode routes tender emails ONLY to dev list with `[TEST]` subject prefix; business recipients never in Test Mode; last-active-dev-recipient refusal; recipients snapshotted into `NotificationLog` at send time.
8. **Templates:** §8.1 tender assessment (three sections + attachments/links footer, URGENT marker, `[TEST]` prefix), §8.2 update email, §8.3 system alert email (dev list only).
9. **Whole-chain-down fallback:** message stays `pending_retry`, health-view red banner state set.

## Out of scope

- Choosing providers 2/3 (O4/O13), the sender mailbox (O3), Sendlib Free/Pro tier (O4) — leave config-gated with defaults `[PROPOSED]` only.
- Admin UI screens (their data contracts are consumed here).
- OCR/AI/pipeline business logic.

## Tests

- Provider chain order + failover with fakes; permanent-error skip + alert; all-fail ⇒ `pending_retry` + red banner.
- Retry with backoff counts; attempted_at/duration/status recorded.
- Breaker: opens after N fails, cooldown, closes via test email/probe.
- Idempotency: re-send attempt for same dedupe key suppressed unless flagged; header present; `possible_duplicate` set on ambiguous case.
- Attachment planner: fits-all ⇒ attach; partial ⇒ attach+link; none ⇒ all links; correct attached-vs-linked logged; base64 budget respected.
- Test Mode: business recipients receive nothing; dev list gets `[TEST]`-prefixed mail; dry-run writes nothing and sends nothing.
- Secure links generated signed and expire; direct storage paths never in output.
- Recipient snapshot preserved in `NotificationLog`.
- Sendlib adapter unit-tested against a mock of the vendor API (never real credentials in CI).

## Rules

- Our notification log is the source of truth — never rely on vendor logs.
- No secrets in logs; credentials write-only.
- No business values decided; configuration-driven.

## Report

Files changed; mapping to `docs/08`; vendor-limit verification status; tier assumption (Free/Pro) clearly labelled `[PROPOSED]`; tests run + results; open decisions pending.
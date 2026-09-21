# 08 — Email Notification Specification

> Source of truth: v1.1 §5.7, §5.10, §8. Covers the `MailProvider` abstraction, the provider chain/failover, Test Mode routing, recipients, attachment planning, secure links, and logging.

## 8.1 Scope

- **Email only** for v1 (Slack/Teams/SMS out of scope). The notification layer must be designed so other channels can be added later without rework (v1.1 §3).
- One email per **new or updated** tender that **passes triage** — even when the eventual verdict is "do not apply" (OPEX still wants visibility).

## 8.2 `MailProvider` abstraction

- One interface; one adapter per provider type.
- First provider: **Sendlib** (`sendlib.samueltuoyo.com`), chosen because it needs **no domain and no DNS records**.
- Providers 2 and 3: **[TBD]** (open decision — `docs/13-open-decisions.md`). Select from conventional transactional APIs or SMTP after OPEX decides on a sending domain.

### Per-provider configuration
- `type` (Sendlib / transactional API / SMTP), credentials (encrypted, write-only), `from_address`, `from_name`, `reply_to`, `priority` (chain order), `active`, and a **capabilities** block:
  - `max_attachments` (max files per message)
  - `max_attachment_mb` (max size of one file)
  - `max_message_mb` (max total size; remember base64 inflates attachments ~⅓)
  - `daily_limit`, `rate_limit_per_min`
  - `needs_verified_domain` (DNS/domain setup required)

## 8.3 Provider chain / failover

1. Try provider 1. Transient errors (timeout, HTTP 429, 5xx) retried with exponential backoff (default 2 retries).
2. Permanent errors (bad credentials, invalid payload, quota exhausted) skip straight to the next provider **and** raise an alert.
3. Every attempt recorded (`NotificationAttempt`: provider, status, error code, duration). Final `NotificationLog` row says which provider actually delivered.
4. **Circuit breaker:** after N consecutive failures a provider is skipped for a cooldown period + alert; a successful "Send test email" or a probe closes the breaker.
5. **Whole chain down:** message stays queued `pending_retry`, retried on schedule; health view shows a **red banner**. An alert is attempted via the chain too, but the **persisted red banner is the fallback signal** (email may also fail).

## 8.4 Test Mode

- Global switch; **ON by default until go-live**.
- When ON, all tender emails go **only to the dev alert list**, subject prefixed `[TEST]`.
- Turning it off is an explicit, audit-logged admin action.
- "Test this source" / "Test connection" / "Send test email" are dry-runs or test-only sends — never to business recipients, no writes to seen-tenders.

## 8.5 Developer/test recipients

- **Dev alert list**: receives system alerts (§8.10) — validated entries with `alert_types[]`, `min_severity`, `active`.
- First dev recipient **seeded from an environment variable** so alerts work before the admin app is ever opened.
- The system **refuses to deactivate/delete the last active dev recipient** — a silent alert path is the failure this system exists to prevent.
- Tender emails in Test Mode go only here.

## 8.6 Business (tender) recipients

- **Tender list**: business team receiving assessment emails.
- Fields: `email`, `name`, `role`, `delivery` (To/CC/BCC), `sources` (all or selected subset via `recipient_scope`), `receives` (all verdicts / APPLY + APPLY WITH CONDITIONS only / urgent only), `active`.
- Actual recipients = open business decision.
- Recipients come from configured lists, never hardcoded addresses.

## 8.7 Subject prefixing in Test Mode

- Subject prefixed `[TEST]` in Test Mode (e.g. `[TEST][SOURCE] Expression of Interest – Assessment Report: <title>`).
- URGENT marker at the top of the subject when the urgency flag is set *[PROPOSED]*.

## 8.8 Attachment planning

Delivery decision, per message (v1.1 §5.7; supersedes v1.0's single ~20 MB threshold):

1. Compute the message's **attachment set** from the tender's documents.
2. Walk the mail provider chain and pick the **first provider whose capabilities fit the whole set**.
3. If no provider fits everything: attach what fits on the **best provider**, send the rest as **secure expiring download links** listed by filename in the body.
4. If nothing can be attached: send all as links.
5. Record exactly what was **attached vs linked** in the notification log.

**Sendlib implications (research notes from v1.1 §5.10.3, §Appendix B — re-verify before building):**

| Limit | Free | Pro |
|---|---|---|
| Attachments/email | 5 | 20 |
| Size/attachment | 1 MB | 10 MB |
| HTML body | 2 MB | 5 MB |
| API requests/min | 30 | 300 |
| Emails/day (Gmail / Workspace) | 200 / 1,000 | 500 / 2,000 |
| Log retention | 5 days | 90 days |

- **Free tier cannot carry typical tender documents** → the planner links most documents on Free; use Pro or a second provider whose limits fit to meet "every document attached."
- Sender is a real connected mailbox; use a **dedicated sender mailbox** (not a personal inbox); Gmail's own limits still apply; rate limits are low, so backlog flushes must be throttled.
- Our own notification log is the source of truth (Sendlib retention is short).
- The email lists every document **by name** whether attached or linked.

## 8.9 Secure links for attachments that cannot be included

- Signed, expiring URLs served from the document archive.
- Default expiry **14 days**, configurable *[PROPOSED]*.
- Each linked document listed by filename with its expiry in the notes footer.
- Never expose storage paths directly; links are signed and expiring (§10-security).

## 8.10 System alerts

- Go to the **dev alert list**, through the same provider chain.
- **Throttling:** one alert when a source/stage becomes unhealthy, one on recovery, reminders at configurable interval — not one email per failed crawl.
- **Triggers** (configurable): source fails N runs in a row; parser mismatch; AI call fails outright or invalid output after retry; budget passes 80%/100%; email fails on every provider; provider breaker opens; daily health digest.
- Alerts carry the **correlation ID** and a deep link to the tender/run **timeline in the admin app**.
- Optional business "operations" list (subset of alerts) = open question.

## 8.11 Actual attached-vs-linked logging

- `NotificationLog` stores `attachments[]` and `links[]`.
- Stores `recipients[]` as a **snapshot at send time** (later edits never rewrite history).
- Records `provider_used`, `dedupe_key`, `possible_duplicate`, `status`.

## 8.12 Notification idempotency

- Dedupe key = tender + verdict + **recipient-set hash**; stored and sent as an **email header**.
- Re-running a crawl never sends a duplicate for the same tender in normal operation.
- Ambiguous provider-timeout duplicates during failover are **accepted** ("rare duplicate is better than a missed tender"), flagged `possible_duplicate`, and logged.
- Failed sends persist as `pending_retry` in a durable outbox so messages survive restarts *[proposed: outbox table]*.

## 8.13 Provider attempts

- One `NotificationAttempt` per provider used: `status`, `error_code`, `duration_ms`, `attempted_at`.
- Final `NotificationLog.provider_used` reflects which provider actually delivered.

## 8.14 Tender update notifications

- Lower priority than new-tender emails.
- §8.2 template: what changed (documents added / deadline changed), current deadline, whether this changes the verdict (re-run verdict only if material).
- Attachments: the **new or changed documents only**; earlier assessment referenced by date.

## 8.15 Email template (confirmed structure, v1.1 §8.1)

```
Subject: [SOURCE] Expression of Interest – Assessment Report: <Tender Title>

1. Background
2. Requirements for [EOI/RFP/Tender]   (include exact deadline: date, time, timezone)
3. Why We Can / Cannot Apply           (ending with APPLY / DO NOT APPLY / APPLY WITH CONDITIONS + confidence)

[Attachments: every document found on the tender's detail page]
```

Additions that do not change the structure *[PROPOSED]*:
- URGENT marker when urgency set.
- Notes footer: documents attached, documents linked (with expiry), documents that failed to download, `incomplete_inputs` if it applies.
- `[TEST]` prefix in Test Mode.

## 8.16 Deliverability

- Proper `From` address; plain-text + HTML versions of the body.
- SPF/DKIM on any custom-domain sender — **deferred** (relevant when provider 2/3 needs a verified domain).

## 8.17 Confirmed vs proposed

- **Confirmed:** abstraction + chain + failover + breaker; capability-aware planner; attached-vs-linked logging; secure expiring links; Test Mode routing; idempotency; recipient snapshots; dev-list refusals/env seeding; update emails; §8.1 structure.
- **Proposed:** link expiry default (14 days), URGENT marker + notes footer, durable outbox Table, subject prefix details.
- **Open decisions:** recipients, sender mailbox, Sendlib Free/Pro, providers 2 & 3, operations list — see `docs/13-open-decisions.md`.
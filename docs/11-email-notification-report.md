# Prompt 11 — Test-Mode Email & Notification Layer Report

**Date:** 2026-09-24
**Scope:** notification preparation, recipient routing, Sendlib adapter, provider chain,
attachment planning, signed links, templates, durable outbox, Test Mode enforcement, and
update-notification input seam.  Prompts 13/14 AI stages were not implemented and no AI
behaviour was added.

## 1. Architecture

The layer is split into provider-neutral mail primitives and a persistence-backed notification
service:

```text
persisted Tender + Document + Verdict (+ optional NotificationEvent)
  -> NotificationService preparation
  -> RecipientRouter (Test Mode boundary)
  -> AttachmentPlanner + SecureLinkSigner
  -> deterministic text/HTML template
  -> durable NotificationLog reservation
  -> provider-priority chain / retry / circuit breaker
  -> NotificationAttempt rows + final NotificationLog state
```

`NotificationService` owns short database transactions around reservation, claim, and
finalisation.  Provider I/O occurs after the reservation is committed.  `NotificationLog` is
also the durable outbox; no in-memory queue is required for recovery.

## 2. MailProvider and first adapter

`mail.provider.MailProvider` exposes `capabilities` and `send` only.  `Capabilities` contains
all fields in v1.1 §5.10.2: attachment count/size, message size, daily/rate limits, and
verified-domain requirement.  `ProviderAdapterRegistry` keeps provider construction swappable;
Sendlib is the only registered concrete adapter.  Providers 2/3 remain TBD under O4/O13.

`SendlibProvider` implements the current documented `POST /api/send` contract against
`sendlib.samueltuoyo.com`, using a mockable HTTP client, Bearer authentication, the string
`from` mailbox/display-name form, `replyTo`, `type` attachment metadata, base64 attachments,
plain text + HTML, and CC/BCC.  The dedupe value is sent as the authenticated
`X-Tender-Notification-Dedupe-Key` request header because the current API does not document
an arbitrary MIME-header JSON field.  It classifies HTTP and transport failures without
logging request bodies or credentials, and sanitizes transport exception causes that could
otherwise retain the Authorization header.

## 3. Provider chain, retry, and breaker

Providers are considered active, ordered by priority, checked for domain readiness and
capability fit, and retried/failed over through `ProviderChain`.  `MailRetryPolicy` defaults
to three total attempts (the documented two retries plus the first attempt), with bounded
exponential backoff.  Timeout/429/5xx are transient; invalid credentials, invalid recipient,
unsupported request, configuration errors, and quota exhaustion are permanent.  `Retry-After`
is honoured when supplied.

`CircuitBreaker` persists `closed → open → half_open → closed` state and failure count in
`MailProvider`, uses a configurable threshold/cooldown, persists a half-open probe lease, and
emits safe transition notices.  Permanent provider failures are exposed through the
`PROVIDER_FAILOVER` alert-hook seam.  Listener failures cannot interrupt delivery.

## 4. Durable outbox and idempotency

A unique dedupe index protects `tender + verdict + recipient-set hash`; the key is also sent
as `X-Tender-Notification-Dedupe-Key` on the provider request.  Reservation happens before
provider I/O.  Sending rows carry a claim token and lease; stale `sending` rows become
`pending_retry` with `possible_duplicate=true`.  Ambiguous whole-chain failures receive one
bounded automatic retry, then move to `possible_duplicate` quarantine and require an explicit
operator requeue; clean failures retain scheduled durable retries.  Attempts record provider,
attempt number, status, safe error code, duration, correlation, provider message ID, and
possible-duplicate state.  Only safe provider metadata is stored.

## 5. Test Mode and recipients

`Setting.test_mode` remains ON in the migration and bootstrap seed.  While ON, the router
selects only active `dev_alert` recipients and the service repeats the assertion immediately
before every initial send, every provider attempt, and every outbox retry.  The `[TEST]`
prefix is applied idempotently, and a route change at send time recomputes the dedupe key.
Business recipients are selected only in an explicitly changed-OFF configuration, and the
separate lists are not mixed.  `TestModeSettings` requires an actor and reason for an audited
change.  `RecipientAdmin` protects the last active development recipient from deactivation
and deletion, including the first-recipient inactive case.  The first recipient can be seeded
from `TI_DEV_ALERT_EMAIL` through the existing bootstrap path.

## 6. Attachments and links

The planner measures raw per-file bytes, exact base64 contribution, rendered-body/MIME
allowance, and the final message cap.  It records `attachments[]` and `links[]` separately,
selects the first whole-set fit, otherwise the provider carrying the most documents, and
links the remainder.  Secure links use HMAC-SHA256 identity-based URLs, default 14-day expiry,
HTTP(S)-only archive roots, and no storage paths.  `SecureDocumentAccess` verifies signature,
expiry, tender/document ownership, and storage existence before returning bytes.  Expired
outbox links are re-signed on retry.

## 7. Templates and verdict integrity

The renderer provides the three required sections, exact WAT plus source-timezone deadline,
all three recommendation values, confidence, evidence-preserving structured values, update
change/current-deadline/reused-verdict information, urgent and Test Mode markers, and document
notes for attached/linked/failed/skipped/incomplete inputs.  It produces deterministic plain
text and escaped HTML; substantive content is available in plain text.  The service copies the
persisted recommendation and evidence without invoking or reinterpreting AI logic.

## 8. Update seam

`NotificationEvent` carries the explicit `NEW`/`UPDATE` identity, material-change result,
Prompt 05 change types/details, correlation, and optional changed-document IDs.  Updates are
lower priority and select changed documents when IDs are supplied.  A current pipeline event
consumer is intentionally not wired into `RunCoordinator`: Prompt 10 ends at stage 09 and
Prompts 13/14 do not yet produce verdict events.  No sticky `Tender.is_update` value is treated
as a new material event when an explicit event is supplied; the legacy fallback remains only
for current direct-service compatibility.

## 9. Security controls

Provider credentials are encrypted before persistence, write-only through
`MailProviderAdmin`, absent from safe views, recursively redacted from audit changes, and
never logged or returned by notification metadata views.  Sendlib transport failures are
converted to sanitized errors without request-bearing exception causes.  Storage paths and
raw provider payloads are not placed in notification/attempt logs.  Signed URLs are treated
as bearer secrets and are omitted from safe operational views.  Migration `0005` archives
legacy duplicate dedupe rows before adding the unique index, preserving history without
silently deleting it.

## 10. Tests and verification

The integration suite exercises the real service with migrated SQLite, encrypted provider
credentials, real local storage, a mock Sendlib HTTP transport, durable rows, attempts,
breaker state, and the red-banner fallback.  Additional tests cover mode revalidation, empty
route recovery, restart flush, stale-claim duplicate marking, permanent failover alerts,
signed archive access, recursive secret redaction, rate limiting, and HTML/text links.

Final command results (2026-09-24):

- `uv run pytest` — **469 passed, 1 warning in 102.71s**.
- `uv run ruff check src tests migrations/versions` — **All checks passed**.
- `uv run mypy src/tender_intelligence` — **Success: no issues found in 114 source files**.
- `uv run pytest tests/integration/test_migrations.py` — **4 passed in 3.69s**.
- `uv run alembic current` — **`0009_provider_usage (head)`**.
- `git diff --check` — **passed** (no whitespace errors; Git emitted only line-ending
  normalization warnings for the Windows working tree).

No real Sendlib endpoint or business-recipient delivery was exercised.

## 11. Known limitations and open decisions

- Sendlib Free/Pro, sender mailbox/OAuth authorisation, providers 2/3, and real business
  recipients remain O2–O4/O13 decisions.
- The archive HTTP route/deployment host remains O10; the signed access seam is implemented,
  but no public route is claimed here.
- Sendlib's current API documents the authenticated request contract but not arbitrary custom
  MIME headers; the dedupe value is therefore carried on the HTTP request header. A future
  provider/API that exposes MIME-header support can carry the same value into the email.
- Provider usage counters are durable in `mail_provider_usage`; a deployment-specific
  cleanup/retention policy for those counters remains an operations decision.
- A material update that reuses the exact same verdict ID and recipient set exposes the
  specification tension between the literal dedupe formula and distinct update events; a
  material update is expected to produce a new verdict artefact, while the event seam is
  ready for an approved event identity.
- Ambiguous timeout quarantine uses the explicit safety default of one automatic retry; the
  operator-facing requeue UI remains outside Prompt 11, while the repository requeue seam is
  available for a later admin surface.
- Real provider delivery, DNS/domain readiness, production LLM/verdict generation, business
  go-live, and Test Mode deactivation were not performed.

## 12. Files changed/created

Production changes are concentrated in `src/tender_intelligence/mail/`,
`src/tender_intelligence/notifications/`, `src/tender_intelligence/db/`, migrations
`0005`–`0009`, worker Test Mode seeding, and the configuration/security helpers.  Tests are
in `tests/unit/test_mail_*`, `tests/unit/test_notification_security.py`, and
`tests/integration/test_notification_service.py`.  Historical pipeline modules were not
rewired or semantically changed.

## 13. Final gate

The full regression command, migration check, lint, and type-check results are confirmed above.
No Prompt 12+ work was started.

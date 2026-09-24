# Prompt 12.2 — Sendlib Configuration Report

## Status

**Not ready for real Sendlib delivery.** The code path and safe configuration seam exist,
but this environment has no operator-supplied Sendlib credential, sender identity, test
recipient, or `TI_MASTER_KEY`. No live request was made and no business email was sent.

## Existing configuration path

```text
TI_MASTER_KEY (process environment / ignored .env)
  -> crypto.secrets.get_master_key()
  -> MailProviderAdmin (write-only credential configuration)
  -> encrypted MailProvider row + ConfigChangeLog metadata
  -> NotificationService loads provider row and decrypts credentials for adapter construction
  -> SendlibProvider POST https://sendlib.samueltuoyo.com/api/send
```

Test recipient path:

```text
TI_DEV_ALERT_EMAIL (process environment / ignored .env)
  -> worker bootstrap seed_dev_alert_recipient()
  -> Recipient(list_type="dev_alert", active=true)
  -> RecipientRouter (Settings.test_mode=true)
  -> [TEST] subject
```

The future Admin UI should call the same `MailProviderAdmin` and existing recipient/config
services. It should not maintain a parallel provider or recipient configuration store. No
Admin API or UI was added.

## Exact existing configuration keys and values

| Key / field | Source | Current state |
|---|---|---|
| `TI_MASTER_KEY` | Environment; required to encrypt/decrypt provider credentials | Not present in this process |
| `TI_DEV_ALERT_EMAIL` | Environment; worker bootstrap seed for first active dev recipient | Not present in this process; no operator address was provided |
| `MailProvider.provider_type` | Database, written by `MailProviderAdmin` | Must be `sendlib` |
| `MailProvider.credentials_encrypted` | Database, AES-GCM encrypted with `TI_MASTER_KEY` | No configured Sendlib row verified |
| credential JSON key `api_key` | Supplied to `MailProviderAdmin.add(... credentials={"api_key": ...})` | Required; plaintext is not stored in audit data or safe views |
| `MailProvider.from_address` | Database, set through `MailProviderAdmin.add` | Required valid email; not configured/verified here |
| `MailProvider.from_name` | Database, optional | Not configured here |
| `MailProvider.reply_to` | Database, optional; validated email | Not configured here |
| Provider endpoint | Fixed in adapter | `https://sendlib.samueltuoyo.com/api/send`; no base-URL setting is supported |
| Test Mode | `Setting.test_mode`, database; default/seed is `true` | Default and seeding enforce ON; live database value could not be inspected |

`.env` and `.env.*` are ignored by Git, with `.env.example` explicitly exempted. The existing
`.env.example` contains placeholders only. No new environment variable names were introduced.

## Sender verification

The repository documentation says Sendlib relays through an authorised Gmail/Google Workspace
mailbox and does not require domain DNS setup. The adapter cannot confirm mailbox authorisation
or sender verification. No sender was configured or verified in this task. Do not treat a
valid email syntax check as provider verification.

## Safe configuration errors

- Missing API key: `build_from_context`/`provider_from_row` return a sanitized mail
  configuration error before transport; no `NotificationAttempt` should be created for an
  unavailable provider configuration.
- Missing or invalid sender: `MailProviderAdmin.add` rejects absent/invalid `from_address`.
- Missing/invalid test recipient: `seed_dev_alert_recipient` rejects absent/invalid values;
  notification routing fails closed if there is no active dev recipient.
- Invalid API key: only provider can confirm it; the adapter maps non-2xx responses to safe
  status/error categories and does not log authorization headers or response bodies.
- Unavailable Sendlib: the existing provider chain, bounded retry, durable outbox,
  `NotificationAttempt`, and `NotificationLog` paths apply. No second retry system was added.

## Live configuration and delivery findings

- Test Mode: ON by default and `ensure_settings_row` seeds it ON. Runtime DB state was not
  inspected, so its active value is **not verified**.
- Development recipient: missing from environment; no operator-supplied address; active DB
  recipient list is **not verified**.
- Sendlib credential: missing from environment and no configured DB provider was verified.
- Sender: not configured; provider verification status unknown.
- Sendlib connectivity: not attempted.
- Test notification / provider response: not attempted; no live email was sent.
- `NotificationAttempt`: no live attempt result.
- `NotificationLog`: no live notification result.
- No production/business recipient was configured or sent to by this task.

## Prompt references

This configuration path reuses `docs/03-data-model.md` (MailProvider/Recipient/Settings),
`docs/08-email-notification-spec.md` (Sendlib and Test Mode), `docs/10-security-spec.md`
(encrypted/write-only secrets), and Prompt 11's notification/provider implementation.

# Notification Channel Architecture — Implementation Report

**Gate: NOTIFICATION ARCHITECTURE: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION**

This is an implementation report, not independent verification. Email is the only channel implemented. No Slack, Teams, SMS, webhook, or third-party notification provider was added.

## Existing architecture inspected

The notification service already owns notification preparation and durable delivery: it loads tender/verdict/document records, routes configured recipients, applies Test Mode and `[TEST]`, renders plain text and HTML, plans attachments versus signed secure links, sends through the configured `MailProvider` chain, applies retry/failover and persisted circuit breakers, and writes `NotificationLog` and per-provider `NotificationAttempt` audit rows. The Admin API and alert manager use this service. The tender `RunCoordinator` currently has no notification-send stage, so this change does not add or trigger delivery as part of a worker run.

Before this change, the notification service was a delivery entrypoint with a direct dependency on the email/provider implementation. A downstream tender caller had no channel-neutral send contract. The existing `NotificationEvent` also imported its `NotificationKind` enum from the email templates module, so even the event contract carried an email-module dependency.

## New boundary

`notifications/channels.py` defines the `NotificationChannel` protocol: `send(notification, dry_run=False)`. `NotificationDispatcher` depends only on that protocol and delegates without knowing provider, routing, formatting, or channel-specific behavior. `EmailNotificationChannel` is the sole v1 implementation. It adapts the channel-neutral `NotificationEvent` call to the existing `NotificationService.notify_tender()` implementation.

`NotificationKind` and the existing `NotificationOutcome` data contract now live in `notifications/contracts.py`; the email template module and notification service import those shared contracts. Existing `NotificationService` APIs, including `send_test_email()` and outbox retry, remain available and retain their behavior. The channel adapter does not duplicate email preparation or provider delivery.

No channel configuration setting or selector was added: current composition injects the email channel because it is the only usable implementation in v1. A future channel can implement `NotificationChannel` and be injected at composition time without changing tender discovery, processing, AI, verdict persistence, or the orchestration contract. There is no operational configuration for unavailable channels.

## Worker/pipeline boundary

The current worker coordinator does not send tender notifications. The new dispatcher is the generic call boundary for tender notification consumers, and the architecture test checks that worker/coordinator modules do not import or instantiate email providers directly. This remediation does not wire an automatic email send into the worker or change existing notification timing.

## Test evidence

| Coverage | Result |
|---|---|
| Generic boundary unit tests: channel protocol, dispatcher delegation, no mail-provider imports in pipeline modules | 2 passed |
| Existing notification service plus new real Email channel path (attachment, Test Mode, audit, provider failover, pending retry) | Included in 121-pass focused notification suite |
| Focused notification/mail/template/breaker/link/Test Mode/alert suite | 121 passed |
| Admin API + Sendlib adapter + notification security | 41 passed, 1 existing Starlette deprecation warning |
| Ruff on changed channel, contracts, service, templates, and test files | Passed |
| Full suite | 706 passed, 1 failed, 1 existing Starlette deprecation warning in 221.02s |

The full suite's sole failure is `tests/unit/test_deadline_waho_parser.py::TestFrenchDeadlinePatterns::test_date_limite_july_gmt` (expected French July deadline parsed as `None`). This is the pre-existing out-of-scope Prompt 12.1 WAHO deadline parser issue and was not changed. Full-suite command: `.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=.tmp-notification-full -o addopts='' -q`.

The new integration test sends a real tender notification via `NotificationDispatcher(EmailNotificationChannel(existing_service))`; it verifies email is selected, the development recipient and `[TEST]` subject are used, the document attachment and both body formats are present, and the sent notification/audit record retains tender, verdict, and correlation references. Existing notification service coverage continues to exercise provider failover, retries, circuit breaker, secure links, and Test Mode. Additional failover and pending-retry cases now call through the generic channel adapter.

## Remaining limits

- Only email is implemented and usable.
- No notification send stage is currently wired into `RunCoordinator`; this change establishes a downstream channel boundary without changing when the existing service is called.
- Future channels may need channel-specific rendering or delivery metadata, but can accept the shared event and outcome contract.
- Provider chain order and configuration remain the existing mail configuration; no new channel settings were introduced.
- The unrelated French WAHO deadline parser failure remains open.

## Final gate

**NOTIFICATION ARCHITECTURE: IMPLEMENTED — READY FOR INDEPENDENT VERIFICATION**

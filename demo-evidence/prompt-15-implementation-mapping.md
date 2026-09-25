# Prompt 15 implementation mapping

| Concern | Existing authoritative component | Admin API integration |
|---|---|---|
| HTTP/API | FastAPI in `tender_intelligence.admin.main` | Versioned router; explicit Pydantic DTOs |
| Database | SQLAlchemy models and repositories | Request-scoped SQLAlchemy sessions |
| Configuration | `Setting`, `Source`, `LLMProfile`, `LLMRoleAssignment`, `MailProvider`, `Recipient`, `KnowledgeBaseVersion` | Mutations update the existing rows; no duplicate configuration store |
| Audit | `log_config_change` and `ConfigChangeLog` | Actor/resource/changed-field metadata on each configuration mutation; secrets redacted |
| Timeline | `TimelineService` | Read-only query using the tender's persisted correlation ID |
| Health | persisted `RunHistory`, `AlertEvent`, notification, and provider state; `build_health` for liveness | Read-only aggregation; no crawl/provider calls |
| Source dry-run | `RunCoordinator.run_source(..., dry_run=True)` | Delegates through Prompt 10's coordinator, including discovery and dry-run dedup classification; no SeenTender, Tender, or RunHistory writes |
| Notification/test email | `NotificationService` provider chain and attempt repositories | Test-only route through `send_test_email`; Test Mode and active dev recipient validation remain authoritative |
| LLM test connection | `LLMClient` interface | Injected client factory; no concrete network provider is currently wired by the application |
| Authentication | `AdminUser` model exists, but identity provider/provisioning is O11 OPEN | Actor dependency is injectable; test-header auth is explicitly opt-in and rejected in production; default resolver denies when no auth integration is configured |
| Secrets | AES-GCM envelope helpers, master key in environment | Write-only DTOs; encrypted on input; response serializers expose only `configured` |

Confirmed implementation scope comes from `docs/09`, `docs/03`, `docs/04`, `docs/07`,
`docs/08`, and `docs/10`. Provider login, business users, monthly AI budget, production
provider approval and sender mailbox decisions remain open per `docs/13`.

`RunCoordinator` is currently composed by the worker only in later pipeline work and does
not expose a ready-made API factory. The Admin API accepts that existing coordinator via
application state so dry-run behavior is reused without creating another crawl path.

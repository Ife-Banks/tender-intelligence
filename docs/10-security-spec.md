# 10 — Security Specification

> Source of truth: v1.1 §5.9, §5.10, §6.2, §5.11. This document separates **confirmed security requirements** from **proposed implementation mechanisms**. Nothing here grants permission to weaken a requirement for convenience.

## 10.1 Confirmed requirements

### Secret storage
- Every secret is **encrypted at rest**: source credentials, LLM API keys, mail credentials, OAuth tokens. (v1.1 §6.2)
- The **encryption master key lives outside the database** — environment variable or a secrets manager, never in the same store as the ciphertext. (v1.1 §6.2)

### API key encryption
- LLM `api_key`s stored encrypted at rest (`api_key_encrypted`). (v1.1 §5.9.1)

### Never logging secrets
- Secrets must never appear in logs. Log metadata only (sizes, token counts, profile names), not content. (v1.1 §6.2)
- Never log full prompts containing company data. (v1.1 §6.2)

### Never returning secrets from APIs
- The UI and API **never return a stored secret**; they show only that one is set, plus the last few characters if useful. (v1.1 §6.2)
- Secrets are **write-only** in the UI. (v1.1 §5.8)

### Provider approval restrictions
- A profile flagged `approved_for_company_docs = false` (the **default**) is **refused** knowledge-base content — including as a **failover target**. (v1.1 §5.9.4, §11)
- Setting the flag to `true` is an **explicit admin action, audit-logged**. (v1.1 §5.9.4)
- Data handled by third parties must be inventoried with an owner in a short security note before go-live (LLM provider sees tender text + KB; mail provider sees email content and attachments; Sendlib relay holds OAuth tokens for the sender mailbox). (v1.1 §6.2)

### Company-document data policy
- Verdict calls that carry the KB (certificates, project values, staff details) are a **trust decision**; only `approved_for_company_docs = true` profiles receive them. (v1.1 §5.9.4)
- While an unapproved profile is assigned to the verdict role, the KB sent must be the **placeholder/non-sensitive version**. (v1.1 §5.12, §5.9.4)

### Test-provider restrictions
- Test providers (DeepSeek's own API, hosted gateways, third-party relays) run on **public tender text only**, with a placeholder/non-sensitive knowledge base. (v1.1 §5.9.4)

### Admin authentication & authorization
- Admin app is **authenticated users only**; at least Admin and Viewer roles [PROPOSED]. (v1.1 §5.11)
- **Viewers never see knowledge base or secrets.** (v1.1 §5.11)
- Exact login method and user provisioning = **open decision** (`docs/13-open-decisions.md`).
- Secrets in `ConfigChangeLog`: never stored — only field names metadataned. (v1.1 §7)

### Audit logs
- Configuration changes: actor, entity, changed fields, timestamp. (v1.1 §7)
- Operational audit: every tender/document/verdict/email with timestamps; recipients snapshotted at send time. (v1.1 §6, §5.10.1)

### Correlation IDs
- Correlation ID stamped on every log line, DB row and error for traceability across stages. (v1.1 §6.1)

### Data handling
- Knowledge-base documents (sensitive company/financial info) are **access-controlled**. (v1.1 §5.5, §6.2)
- Retention minimum for run history, stage logs and verdicts (e.g. 12 months, configurable). Third-party logs not relied on. (v1.1 §6.1)

### Secure attachment links
- Links are **signed, expiring URLs** served from the document archive; default expiry 14 days (configurable) [PROPOSED default]. (v1.1 §5.7)

### Configuration change history
- `ConfigChangeLog` records every config change, never secret values. (v1.1 §7)

## 10.2 Proposed implementation mechanisms

These satisfy the requirements but are implementation choices to be validated:
- Encryption scheme choice for secrets-at-rest (e.g. AES-GCM with a key managed outside the DB) `[PROPOSED]`.
- Secrets manager vs environment variable for the master key `[PROPOSED]` — source allows either.
- Auth mechanism (e.g. session cookie + bcrypt/argon2 password hashes, or SSO) `[PROPOSED]`; login method is open.
- Signed-URL implementation (HMAC expiry tokens) `[PROPOSED]`.
- Structured-log sanitisation filter to guarantee secrets/prompt content never reach logs `[PROPOSED]`.

## 10.3 Security invariants (do not weaken)

1. Secrets are write-only, encrypted at rest, never logged or returned.
2. Master key outside the database.
3. Unapproved LLM profiles cannot receive company KB content — even as fallback.
4. KB and secrets hidden from Viewer role.
5. Admin test ops are dry-runs (no data writes, no business sends).
6. Secure links are signed and expiring; storage paths never exposed.
7. Audit trail records who changed what, without values that are secret.
8. Security notes before go-live inventory every third party that sees data, with an owner.

## 10.4 Historical note

v1.0 required "source credentials (if any) encrypted at rest" and KB access-control but did not define recipient/dev separation, Test Mode, provider approval flags, or the full secrets inventory. v1.1 adds these (§6.2); v1.1 governs.
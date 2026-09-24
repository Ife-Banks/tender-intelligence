"""Write-only mail-provider configuration operations.

Prompt 15 will expose these operations through an API, so the security boundary is implemented
here rather than in a future controller.  Reads return metadata and ``credentials_set`` only;
plaintext credentials are encrypted immediately and are never placed in audit changes,
exceptions, logs, or returned mappings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from tender_intelligence.crypto.secrets import encrypt_secret
from tender_intelligence.db.audit import log_config_change
from tender_intelligence.db.models.mail import MailProvider
from tender_intelligence.db.models.recipients import is_valid_email
from tender_intelligence.mail.provider import Capabilities


class MailProviderConfigError(ValueError):
    """A provider configuration operation was invalid."""


@dataclass(frozen=True)
class MailProviderView:
    """Safe provider representation for an admin/API response."""

    id: int
    name: str
    provider_type: str
    from_address: str
    from_name: str | None
    reply_to: str | None
    priority: int
    active: bool
    capabilities: Capabilities
    breaker_state: str
    breaker_until: str | None
    credentials_set: bool


class MailProviderAdmin:
    """Session-scoped provider configuration service with write-only credentials."""

    def __init__(self, session: Session, *, encryption_key: bytes | None = None) -> None:
        self.session = session
        self._encryption_key = encryption_key

    def add(
        self,
        *,
        name: str,
        provider_type: str,
        credentials: dict[str, Any],
        from_address: str,
        from_name: str | None = None,
        reply_to: str | None = None,
        priority: int = 1,
        active: bool = True,
        capabilities: Capabilities | None = None,
        actor: str = "admin",
    ) -> MailProvider:
        if provider_type.lower() != "sendlib":
            raise MailProviderConfigError("only the confirmed Sendlib adapter is available")
        if not name.strip() or not is_valid_email(from_address):
            raise MailProviderConfigError("provider name and a valid from_address are required")
        if reply_to is not None and not is_valid_email(reply_to):
            raise MailProviderConfigError("reply_to must be a valid email address")
        if not isinstance(credentials, dict) or not credentials.get("api_key"):
            raise MailProviderConfigError("Sendlib credentials require api_key")
        row = MailProvider(
            name=name.strip(),
            provider_type=provider_type.lower(),
            credentials_encrypted=encrypt_secret(
                _credential_json(credentials), key=self._encryption_key
            ),
            from_address=from_address.strip(),
            from_name=from_name,
            reply_to=reply_to,
            priority=int(priority),
            active=bool(active),
            capabilities=_capabilities_dict(capabilities or Capabilities()),
        )
        self.session.add(row)
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="mail_provider",
            entity_id=row.id,
            changed_fields={
                "action": "added",
                "name": row.name,
                "provider_type": row.provider_type,
                "credentials": "[REDACTED]",
            },
        )
        return row

    def update_credentials(
        self,
        provider_id: int,
        credentials: dict[str, Any],
        *,
        actor: str = "admin",
    ) -> MailProvider:
        """Replace credentials without ever returning the old or new value."""

        row = self._get(provider_id)
        if not isinstance(credentials, dict) or not credentials.get("api_key"):
            raise MailProviderConfigError("Sendlib credentials require api_key")
        row.credentials_encrypted = encrypt_secret(
            _credential_json(credentials), key=self._encryption_key
        )
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="mail_provider",
            entity_id=row.id,
            changed_fields={"action": "credentials_rotated", "credentials": "[REDACTED]"},
        )
        return row

    def set_active(self, provider_id: int, active: bool, *, actor: str = "admin") -> MailProvider:
        row = self._get(provider_id)
        row.active = bool(active)
        self.session.flush()
        log_config_change(
            self.session,
            actor=actor,
            entity="mail_provider",
            entity_id=row.id,
            changed_fields={"action": "enabled" if active else "disabled"},
        )
        return row

    def view(self, provider_id: int) -> MailProviderView:
        return _view(self._get(provider_id))

    def _get(self, provider_id: int) -> MailProvider:
        row = self.session.get(MailProvider, provider_id)
        if row is None:
            raise MailProviderConfigError(f"mail provider {provider_id} not found")
        return row


def _view(row: MailProvider) -> MailProviderView:
    raw = row.capabilities or {}
    allowed = {
        key: raw[key]
        for key in (
            "max_attachments",
            "max_attachment_mb",
            "max_message_mb",
            "daily_limit",
            "rate_limit_per_min",
            "needs_verified_domain",
        )
        if key in raw
    }
    return MailProviderView(
        id=int(row.id),
        name=row.name,
        provider_type=row.provider_type,
        from_address=row.from_address,
        from_name=row.from_name,
        reply_to=row.reply_to,
        priority=int(row.priority),
        active=bool(row.active),
        capabilities=Capabilities(**allowed),
        breaker_state=row.breaker_state,
        breaker_until=row.breaker_until.isoformat() if row.breaker_until else None,
        credentials_set=bool(row.credentials_encrypted),
    )


def _credential_json(credentials: dict[str, Any]) -> str:
    # Import locally to keep this module's public surface focused on the admin operation.
    import json

    return json.dumps(credentials, separators=(",", ":"), sort_keys=True)


def _capabilities_dict(capabilities: Capabilities) -> dict[str, Any]:
    # Omit unbounded values.  In particular, Sendlib's adapter supplies conservative Free-tier
    # defaults; persisting explicit ``None`` values here would otherwise erase those defaults
    # when the row is later resolved by the notification service.
    values = {
        "max_attachments": capabilities.max_attachments,
        "max_attachment_mb": capabilities.max_attachment_mb,
        "max_message_mb": capabilities.max_message_mb,
        "daily_limit": capabilities.daily_limit,
        "rate_limit_per_min": capabilities.rate_limit_per_min,
        "needs_verified_domain": capabilities.needs_verified_domain,
    }
    return {key: value for key, value in values.items() if value is not None}


__all__ = ["MailProviderAdmin", "MailProviderConfigError", "MailProviderView"]

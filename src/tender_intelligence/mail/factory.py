"""Provider adapter registry.

Provider selection/failover depends on :class:`MailProvider`, never on a concrete provider
name.  Sendlib is registered by default because it is the only confirmed adapter; a later
Prompt/phase can register providers 2/3 without changing the notification service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tender_intelligence.mail.errors import configuration_error
from tender_intelligence.mail.provider import Capabilities, MailProvider


@dataclass(frozen=True)
class ProviderBuildContext:
    """Decrypted, provider-specific construction inputs."""

    credentials: dict[str, Any]
    from_address: str
    from_name: str | None
    reply_to: str | None
    capabilities: Capabilities
    client_factory: Callable[[], Any] | None = None


ProviderFactory = Callable[[ProviderBuildContext], MailProvider]


class ProviderAdapterRegistry:
    """Small explicit registry preserving the provider abstraction boundary."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def contains(self, provider_type: str) -> bool:
        return provider_type.strip().lower() in self._factories

    def register(self, provider_type: str, factory: ProviderFactory) -> None:
        key = provider_type.strip().lower()
        if not key:
            raise ValueError("provider_type must not be empty")
        if key in self._factories:
            raise ValueError(f"provider adapter already registered: {key}")
        self._factories[key] = factory

    def build(self, provider_type: str, context: ProviderBuildContext) -> MailProvider:
        factory = self._factories.get(provider_type.strip().lower())
        if factory is None:
            raise configuration_error(f"unsupported provider type {provider_type!r}")
        return factory(context)


__all__ = ["ProviderAdapterRegistry", "ProviderBuildContext", "ProviderFactory"]

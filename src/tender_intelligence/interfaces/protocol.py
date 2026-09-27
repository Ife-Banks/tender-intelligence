"""Protocol adapter abstraction for provider-agnostic LLM communication.

This module defines the protocol adapter interface and registry. Protocol adapters
handle the wire-format details for a specific API protocol (e.g., OpenAI-compatible,
Anthropic native). Provider names are metadata only — they never determine business
behavior.

Architecture:
    Admin UI -> LLM Profile -> LLM Role -> Generic LLMClient
    -> Protocol Resolver -> Protocol Adapter -> Endpoint

    Provider names are metadata only — they never determine business behavior.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from tender_intelligence.interfaces.llm import LLMMessage, LLMResponse


class LLMProtocolAdapter(ABC):
    """Abstract protocol adapter for LLM communication.

    Each adapter handles request construction, response parsing, and error
    normalization for a specific API protocol. Adapters are selected by the
    profile's ``protocol`` field, never by provider name or model name.
    """

    @abstractmethod
    def build_request(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Build the protocol-specific request body.

        Args:
            messages: The chat messages to send.
            max_tokens: Optional override for maximum output tokens.

        Returns:
            The complete request body as a dictionary.
        """
        ...

    @abstractmethod
    def get_endpoint(self) -> str:
        """Return the full URL for the protocol's chat endpoint."""
        ...

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """Return the protocol-specific headers (including authorization)."""
        ...

    @abstractmethod
    def parse_response(self, payload: dict[str, Any]) -> LLMResponse:
        """Parse the protocol-specific response into a generic LLMResponse."""
        ...

    @abstractmethod
    def normalize_error(
        self,
        status_code: int,
        error_body: dict[str, Any],
    ) -> str:
        """Normalize a protocol-specific error into an internal error code."""
        ...


# Protocol adapter registry
_PROTOCOL_ADAPTERS: dict[str, type[LLMProtocolAdapter]] = {}


def register_protocol(protocol: str):
    """Decorator to register a protocol adapter class.

    Args:
        protocol: The protocol identifier (e.g., "openai_compatible", "anthropic").
    """
    def decorator(cls: type[LLMProtocolAdapter]) -> type[LLMProtocolAdapter]:
        _PROTOCOL_ADAPTERS[protocol] = cls
        return cls
    return decorator


def get_protocol_adapter(protocol: str) -> type[LLMProtocolAdapter] | None:
    """Look up a protocol adapter by protocol identifier.

    Args:
        protocol: The protocol identifier from the LLM profile.

    Returns:
        The adapter class, or None if the protocol is not registered.
    """
    return _PROTOCOL_ADAPTERS.get(protocol)


def list_protocols() -> list[str]:
    """List all registered protocol identifiers."""
    return sorted(_PROTOCOL_ADAPTERS.keys())

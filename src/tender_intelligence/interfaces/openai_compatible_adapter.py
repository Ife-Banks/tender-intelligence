"""OpenAI-compatible protocol adapter.

This adapter handles request construction, response parsing, and error normalization
for any provider exposing an OpenAI-compatible chat-completions API. It is selected
by the profile's ``protocol`` field — never by provider name or model name.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx2

from tender_intelligence.db.models.llm import LLMProfile
from tender_intelligence.interfaces.llm import (
    LLMClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    LLMUsage,
)
from tender_intelligence.interfaces.protocol import LLMProtocolAdapter, register_protocol


@register_protocol("openai_compatible")
class OpenAICompatibleAdapter(LLMProtocolAdapter):
    """Protocol adapter for OpenAI-compatible chat-completions APIs.

    Used by any provider exposing a compatible API (OpenAI, Groq, DeepSeek,
    OpenCode-compatible endpoints, etc.). Provider identity is metadata only.
    """

    def __init__(self, profile: LLMProfile, api_key: str) -> None:
        self._profile = profile
        self._api_key = api_key

    def build_request(
        self,
        messages: list[LLMMessage],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Build the OpenAI-compatible request body.

        Request construction is driven entirely by the profile's capability flags:
        - supports_response_format: sends response_format={"type": "json_object"}
        - supports_include_reasoning: sends include_reasoning parameter
        - supports_chat_template_kwargs: sends chat_template_kwargs and reasoning_budget

        No provider-name or model-name detection is used.
        """
        effective_max_tokens = (
            max_tokens if max_tokens is not None else self._profile.max_output_tokens
        )
        body: dict[str, Any] = {
            "model": self._profile.model,
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "stream": False,
        }
        if effective_max_tokens is not None:
            body["max_tokens"] = effective_max_tokens
        if self._profile.temperature is not None:
            body["temperature"] = self._profile.temperature
        if self._profile.top_p is not None:
            body["top_p"] = self._profile.top_p
        # Capability-based structured output
        if self._profile.supports_response_format:
            body["response_format"] = {"type": "json_object"}
        # Capability-based reasoning inclusion
        if self._profile.supports_include_reasoning:
            body["include_reasoning"] = bool(self._profile.enable_thinking)
        # Capability-based thinking control
        if self._profile.supports_chat_template_kwargs:
            body["chat_template_kwargs"] = {
                "enable_thinking": bool(self._profile.enable_thinking)
            }
            reasoning_budget = self._effective_reasoning_budget(effective_max_tokens)
            if reasoning_budget is not None:
                body["reasoning_budget"] = reasoning_budget
        # Capability-based reasoning effort: some providers accept a reasoning_effort
        # parameter (e.g. "low", "medium", "high") to control reasoning depth.
        if self._profile.supports_reasoning_effort and self._profile.reasoning_effort:
            body["reasoning_effort"] = self._profile.reasoning_effort
        return body

    def get_endpoint(self) -> str:
        """Return the full URL for the chat-completions endpoint."""
        base_url = self._profile.base_url.rstrip("/")
        return f"{base_url}/chat/completions"

    def get_headers(self) -> dict[str, str]:
        """Return the headers for the OpenAI-compatible API."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        for key, value in (self._profile.extra_headers or {}).items():
            if key.lower() not in {"authorization", "proxy-authorization", "cookie"}:
                headers[key] = str(value)
        return headers

    def parse_response(self, payload: dict[str, Any]) -> LLMResponse:
        """Parse the OpenAI-compatible response into a generic LLMResponse."""
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        finish_reason = choices[0].get("finish_reason") if choices else None
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise LLMError(
                "Provider returned no completion content", "invalid_provider_response"
            )
        usage_data = payload.get("usage") or {}
        prompt_tokens = self._int_or_none(usage_data.get("prompt_tokens"))
        completion_tokens = self._int_or_none(usage_data.get("completion_tokens"))
        usage = LLMUsage(prompt_tokens, completion_tokens)
        return LLMResponse(
            content=content,
            profile_name=self._profile.name,
            model=str(payload.get("model") or self._profile.model),
            usage=usage,
            raw={
                "request_config": self._build_request_config(),
                "provider_http_status": 200,
                "finish_reason": finish_reason,
                "response_bytes": len(content.encode("utf-8")),
            },
        )

    def normalize_error(self, status_code: int, error_body: dict[str, Any]) -> str:
        """Normalize an OpenAI-compatible error into an internal error code."""
        if status_code in (401, 403):
            return "provider_authentication_failed"
        if status_code == 429:
            return "rate_limit"
        if status_code == 413:
            return "provider_payload_too_large"
        if status_code >= 500:
            return "provider_server_error"
        return "provider_request_rejected"

    def _effective_reasoning_budget(self, max_tokens: int | None) -> int | None:
        configured = self._profile.reasoning_budget
        if not self._profile.enable_thinking or configured is None:
            return None
        return min(configured, max_tokens) if max_tokens is not None else configured

    def _build_request_config(self) -> dict[str, Any]:
        effective_max_tokens = self._profile.max_output_tokens
        return {
            "temperature": self._profile.temperature,
            "top_p": self._profile.top_p,
            "max_tokens": effective_max_tokens,
            "enable_thinking": bool(self._profile.enable_thinking),
            "reasoning_budget": self._effective_reasoning_budget(effective_max_tokens),
            "reasoning_effort": self._profile.reasoning_effort,
        }

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (ValueError, TypeError):
            return None


class OpenAICompatibleClient(LLMClient):
    """Chat client that delegates to the OpenAI-compatible protocol adapter.

    This is the generic LLM client used by Stage A, Stage B, and other business
    logic. It contains no provider-specific code — all protocol details are
    handled by the adapter.
    """

    def __init__(self, profile: LLMProfile, api_key: str) -> None:
        self.profile_name = profile.name
        self._adapter = OpenAICompatibleAdapter(profile, api_key)

    def chat(self, messages: list[LLMMessage], *, max_tokens: int | None = None) -> LLMResponse:
        endpoint = self._adapter.get_endpoint()
        body = self._adapter.build_request(messages, max_tokens=max_tokens)
        headers = self._adapter.get_headers()
        response_headers_received = False
        try:
            timeout = httpx2.Timeout(self._adapter._profile.timeout_seconds)
            with (
                httpx2.Client(timeout=timeout) as client,
                client.stream("POST", endpoint, json=body, headers=headers) as response,
            ):
                response_headers_received = True
                if response.status_code >= 400:
                    try:
                        response.read()
                        error_body = response.json().get("error", {})
                    except (ValueError, TypeError, AttributeError):
                        error_body = {}
                    if not isinstance(error_body, dict):
                        error_body = {}
                    raise LLMError(
                        "OpenAI-compatible provider rejected the request",
                        self._adapter.normalize_error(response.status_code, error_body),
                        http_status=response.status_code,
                        diagnostic_phase="http_response_received",
                        provider_error_type=self._safe_diagnostic_token(error_body.get("type")),
                        provider_error_code=self._safe_diagnostic_token(error_body.get("code")),
                        provider_error_param=self._safe_diagnostic_token(error_body.get("param")),
                        provider_error_message=self._safe_provider_message(error_body.get("message")),
                    )
                response.read()
                payload = response.json()
            return self._adapter.parse_response(payload)
        except LLMError:
            raise
        except httpx2.ConnectTimeout as exc:
            raise LLMError(
                "Provider connection timed out before an HTTP response",
                "timeout_before_http_response",
                diagnostic_phase="before_http_response",
            ) from exc
        except httpx2.PoolTimeout as exc:
            raise LLMError(
                "Provider connection pool timed out before an HTTP response",
                "timeout_before_http_response",
                diagnostic_phase="before_http_response",
            ) from exc
        except httpx2.WriteTimeout as exc:
            raise LLMError(
                "Provider request write timed out before an HTTP response",
                "timeout_before_http_response",
                diagnostic_phase="before_http_response",
            ) from exc
        except httpx2.ReadTimeout as exc:
            if not response_headers_received:
                raise LLMError(
                    "Provider timed out before response headers",
                    "timeout_before_http_response",
                    diagnostic_phase="after_connection_before_response_headers",
                ) from exc
            raise LLMError(
                "Provider response timed out after HTTP response headers",
                "ai_call_timeout",
                diagnostic_phase="after_http_headers_before_body_complete",
            ) from exc
        except httpx2.TimeoutException as exc:
            raise LLMError(
                "Provider request timed out before an HTTP response",
                "timeout_before_http_response",
                diagnostic_phase="before_http_response",
            ) from exc
        except httpx2.TransportError as exc:
            raise LLMError("Provider could not be reached", "provider_unreachable") from exc
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise LLMError(
                "Provider returned an invalid response", "invalid_provider_response"
            ) from exc

    @staticmethod
    def _safe_diagnostic_token(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if len(cleaned) > 64:
            return None
        if not all(c.isalnum() or c in "-_" for c in cleaned):
            return None
        return cleaned

    @staticmethod
    def _safe_provider_message(value: Any) -> str | None:
        """Extract a safe, sanitized provider error message for display."""
        if not isinstance(value, str):
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        cleaned = "".join(c for c in cleaned if c.isprintable() or c in " \t\n\r")
        if len(cleaned) > 500:
            cleaned = cleaned[:497] + "..."
        return cleaned


def build_openai_compatible_client(*, profile: LLMProfile, api_key: str) -> LLMClient:
    """Factory for OpenAI-compatible clients.

    This factory is used when the profile's protocol is "openai_compatible".
    For other protocols, use build_protocol_client().
    """
    parsed = urlsplit(profile.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMError("Provider base URL is invalid", "invalid_provider_configuration")
    return OpenAICompatibleClient(profile, api_key)


def build_protocol_client(*, profile: LLMProfile, api_key: str) -> LLMClient:
    """Factory that selects the protocol adapter based on the profile's protocol field.

    Args:
        profile: The LLM profile containing protocol, base_url, model, capabilities.
        api_key: The decrypted API key.

    Returns:
        An LLMClient configured for the profile's protocol.

    Raises:
        LLMError: If the protocol is not supported or the configuration is invalid.
    """
    from tender_intelligence.interfaces.protocol import get_protocol_adapter

    protocol = profile.protocol or "openai_compatible"
    adapter_class = get_protocol_adapter(protocol)
    if adapter_class is None:
        raise LLMError(
            f"Unsupported LLM protocol: {protocol}",
            "unsupported_protocol",
        )
    parsed = urlsplit(profile.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMError("Provider base URL is invalid", "invalid_provider_configuration")
    return OpenAICompatibleClient(profile, api_key)

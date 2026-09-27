"""Small non-streaming OpenAI-compatible chat-completions client."""

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


class OpenAICompatibleClient(LLMClient):
    """Chat client for OpenAI-compatible endpoints such as NVIDIA NIM."""

    def __init__(self, profile: LLMProfile, api_key: str) -> None:
        self.profile_name = profile.name
        self._profile = profile
        self._api_key = api_key

    def chat(self, messages: list[LLMMessage], *, max_tokens: int | None = None) -> LLMResponse:
        base_url = self._profile.base_url.rstrip("/")
        endpoint = f"{base_url}/chat/completions"
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
        # Capability-based structured output: providers that support response_format
        # receive it; those that don't (configured via supports_response_format=False)
        # rely on prompt-instructed JSON output instead.
        if self._profile.supports_response_format:
            body["response_format"] = {"type": "json_object"}
        # Capability-based reasoning inclusion: some providers return reasoning in a
        # separate field that must be explicitly requested.
        if self._profile.supports_include_reasoning:
            body["include_reasoning"] = bool(self._profile.enable_thinking)
        request_config = {
            "temperature": self._profile.temperature,
            "top_p": self._profile.top_p,
            "max_tokens": effective_max_tokens,
            "enable_thinking": bool(self._profile.enable_thinking),
            "reasoning_budget": self._effective_reasoning_budget(effective_max_tokens),
            "reasoning_effort": self._profile.reasoning_effort,
            "supports_response_format": bool(self._profile.supports_response_format),
            "response_format_requested": bool(self._profile.supports_response_format),
            "supports_include_reasoning": bool(self._profile.supports_include_reasoning),
            "supports_chat_template_kwargs": bool(
                self._profile.supports_chat_template_kwargs
            ),
            "supports_reasoning_effort": bool(self._profile.supports_reasoning_effort),
            "stop_parameters_configured": "stop" in body,
        }
        response_headers_received = False
        # Capability-based thinking control: some providers use chat_template_kwargs
        # for thinking control and accept reasoning_budget as a top-level parameter.
        if self._profile.supports_chat_template_kwargs:
            body["chat_template_kwargs"] = {
                "enable_thinking": bool(self._profile.enable_thinking)
            }
            reasoning_budget = request_config["reasoning_budget"]
            if reasoning_budget is not None:
                body["reasoning_budget"] = reasoning_budget
        # Capability-based reasoning effort: some providers accept a reasoning_effort
        # parameter (e.g. "low", "medium", "high") to control reasoning depth.
        if self._profile.supports_reasoning_effort and self._profile.reasoning_effort:
            body["reasoning_effort"] = self._profile.reasoning_effort
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        for key, value in (self._profile.extra_headers or {}).items():
            if key.lower() not in {"authorization", "proxy-authorization", "cookie"}:
                headers[key] = str(value)
        try:
            # Streaming the HTTP response locally (while requesting stream=false from
            # the provider) lets us tell a pre-response connection timeout from a
            # timeout after response headers have arrived.
            with (
                httpx2.Client(timeout=httpx2.Timeout(self._profile.timeout_seconds)) as client,
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
                        _status_error_code(response.status_code, error_body),
                        http_status=response.status_code,
                        diagnostic_phase="http_response_received",
                        provider_error_type=_safe_diagnostic_token(error_body.get("type")),
                        provider_error_code=_safe_diagnostic_token(error_body.get("code")),
                        provider_error_param=_safe_diagnostic_token(error_body.get("param")),
                        provider_error_message=_safe_provider_message(error_body.get("message")),
                    )
                response.read()
                payload = response.json()
            choices = payload.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            finish_reason = choices[0].get("finish_reason") if choices else None
            content = message.get("content")
            if not isinstance(content, str) or not content:
                raise LLMError(
                    "Provider returned no completion content", "invalid_provider_response"
                )
            usage_data = payload.get("usage") or {}
            prompt_tokens = _int_or_none(usage_data.get("prompt_tokens"))
            completion_tokens = _int_or_none(usage_data.get("completion_tokens"))
            usage = LLMUsage(prompt_tokens, completion_tokens)
            return LLMResponse(
                content=content,
                profile_name=self.profile_name,
                model=str(payload.get("model") or self._profile.model),
                usage=usage,
                # Retain only non-content diagnostics; bodies/reasoning traces stay private.
                raw={
                    "request_config": request_config,
                    "provider_http_status": response.status_code,
                    "finish_reason": finish_reason,
                    "response_bytes": len(content.encode("utf-8")),
                    "choice_count": len(choices),
                    "selected_choice_index": 0 if choices else None,
                    "message_content_field_present": isinstance(message, dict)
                    and "content" in message,
                    "message_content_type": type(content).__name__,
                    "reasoning_field_present": isinstance(message, dict)
                    and any(key in message for key in ("reasoning", "reasoning_content")),
                    "refusal_field_present": isinstance(message, dict) and "refusal" in message,
                    "tool_calls_field_present": (
                        isinstance(message, dict) and "tool_calls" in message
                    ),
                },
            )
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

    def _effective_reasoning_budget(self, max_tokens: int | None) -> int | None:
        configured = self._profile.reasoning_budget
        if not self._profile.enable_thinking or configured is None:
            return None
        return min(configured, max_tokens) if max_tokens is not None else configured


def build_openai_compatible_client(*, profile: LLMProfile, api_key: str) -> LLMClient:
    """Factory signature shared by Admin tests, triage, and verdict runtime."""
    parsed = urlsplit(profile.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LLMError("Provider base URL is invalid", "invalid_provider_configuration")
    return OpenAICompatibleClient(profile, api_key)


def _status_error_code(status_code: int, error_body: dict[str, Any] | None = None) -> str:
    if status_code == 401 or status_code == 403:
        return "provider_authentication_failed"
    if status_code == 429:
        return "rate_limit"
    if status_code == 413:
        return "provider_payload_too_large"
    error_body = error_body or {}
    # Providers sometimes report context/token limits as HTTP 400. Normalize only
    # safe machine identifiers, without depending on provider or model names.
    identifiers = " ".join(
        str(error_body.get(key) or "").casefold()
        for key in ("type", "code", "param")
    ).replace("-", "_")
    if any(marker in identifiers for marker in (
        "payload_too_large", "request_too_large", "context_length", "token_limit",
        "max_tokens", "tokens",
    )):
        return "provider_payload_too_large"
    if status_code >= 500:
        return "provider_server_error"
    return "provider_request_rejected"


def _safe_diagnostic_token(value: Any) -> str | None:
    """Keep only short identifier-like error metadata; never retain provider messages."""
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    if not all(char.isalnum() or char in "_.-" for char in value):
        return None
    return value


def _safe_provider_message(value: Any) -> str | None:
    """Extract a safe, sanitized provider error message for display.

    Provider error messages are typically safe to show users (they are designed
    for API consumers), but we still sanitize: strip whitespace, limit length,
    and remove control characters that could be used for log injection or
    terminal escape sequences.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    # Remove control characters (except common whitespace) to prevent log injection
    cleaned = "".join(c for c in cleaned if c.isprintable() or c in " \t\n\r")
    if len(cleaned) > 500:
        cleaned = cleaned[:497] + "..."
    return cleaned


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

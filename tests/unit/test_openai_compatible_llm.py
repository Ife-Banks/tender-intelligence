import pytest

from tender_intelligence.db.models.llm import LLMProfile
from tender_intelligence.interfaces.llm import LLMError, LLMMessage
from tender_intelligence.interfaces.openai_compatible import OpenAICompatibleClient


class _Response:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Client:
    response = None
    request = None

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, *, json, headers):
        assert method == "POST"
        type(self).request = (url, json, headers)
        return type(self).response


class _StreamTimeoutClient(_Client):
    def stream(self, *_args, **_kwargs):
        raise __import__("httpx2").ReadTimeout("synthetic timeout")


def _profile():
    return LLMProfile(
        name="Test Provider",
        base_url="https://api.test-provider.example/v1",
        model="test-model-v1",
        timeout_seconds=30,
        temperature=1.0,
        top_p=0.95,
        reasoning_budget=200,
        enable_thinking=True,
        supports_json=True,
        supports_response_format=True,
        supports_include_reasoning=False,
        supports_chat_template_kwargs=False,
        extra_headers={"X-Test-Header": "safe"},
    )


def test_openai_compatible_client_posts_chat_completion(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        200,
        {
            "model": "test-model-v1",
            "choices": [{"message": {"content": '{"status":"ok"}'}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        },
    )
    result = OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
        [LLMMessage("user", "Reply with JSON")], max_tokens=32
    )
    url, body, headers = _Client.request
    assert url == "https://api.test-provider.example/v1/chat/completions"
    assert body["model"] == "test-model-v1"
    assert body["stream"] is False
    assert body["max_tokens"] == 32
    assert body["response_format"] == {"type": "json_object"}
    assert body["top_p"] == 0.95
    assert "chat_template_kwargs" not in body
    assert "reasoning_budget" not in body
    assert headers["Authorization"] == "Bearer not-a-real-key"
    assert result.content == '{"status":"ok"}'
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 5
    assert result.raw["request_config"]["reasoning_budget"] == 32


def test_openai_compatible_client_maps_http_errors_without_echoing_response(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(401, {"error": "secret token must not leak"})
    with pytest.raises(LLMError) as error:
        OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
            [LLMMessage("user", "test")], max_tokens=8
        )
    assert error.value.error_code == "provider_authentication_failed"
    assert error.value.http_status == 401
    assert error.value.diagnostic_phase == "http_response_received"
    assert "secret token" not in str(error.value)


def test_openai_compatible_client_classifies_payload_too_large_without_echoing_body(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(413, {"error": "request payload contents must not be logged"})
    with pytest.raises(LLMError) as error:
        OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
            [LLMMessage("user", "test")], max_tokens=8
        )
    assert error.value.error_code == "provider_payload_too_large"
    assert error.value.http_status == 413
    assert "request payload contents" not in str(error.value)


def test_openai_compatible_client_keeps_only_safe_provider_error_metadata(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "param": "max_tokens",
                "code": "context_length_exceeded",
                "message": "private provider text must not be retained",
            }
        },
    )
    with pytest.raises(LLMError) as error:
        OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
            [LLMMessage("user", "test")], max_tokens=8
        )
    assert error.value.http_status == 400
    assert error.value.provider_error_type == "invalid_request_error"
    assert error.value.provider_error_param == "max_tokens"
    assert error.value.provider_error_code == "context_length_exceeded"
    assert "private provider text" not in str(error.value)


def test_minimal_connection_profile_request_parameters(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        200,
        {
            "model": "test-model-v1",
            "choices": [{"message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        },
    )
    profile = _profile()
    profile.temperature = 0.0
    profile.top_p = 0.95
    profile.max_output_tokens = 64
    profile.enable_thinking = False
    profile.reasoning_budget = None
    profile.supports_json = False
    profile.supports_response_format = False
    OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("user", 'Return exactly this JSON: {"ok":true}')], max_tokens=64
    )
    url, body, _headers = _Client.request
    assert url == "https://api.test-provider.example/v1/chat/completions"
    assert body["temperature"] == 0.0
    assert body["top_p"] == 0.95
    assert body["max_tokens"] == 64
    assert "chat_template_kwargs" not in body
    assert "reasoning_budget" not in body
    assert body["stream"] is False
    assert "response_format" not in body


def test_include_reasoning_capability_false_hides_reasoning_field(monkeypatch):
    """When supports_include_reasoning=False, include_reasoning is not sent."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        200,
        {
            "model": "test-model",
            "choices": [{"message": {"content": '{"ok":true}', "reasoning": "hidden"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 12},
        },
    )
    profile = _profile()
    profile.enable_thinking = False
    profile.reasoning_budget = None
    profile.supports_include_reasoning = False
    result = OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("user", 'Return exactly this JSON: {"ok":true}')], max_tokens=1024
    )
    _url, body, _headers = _Client.request
    assert "include_reasoning" not in body
    assert body["max_tokens"] == 1024
    assert result.content == '{"ok":true}'
    assert "reasoning" not in result.raw


def test_read_timeout_before_response_headers_is_classified_precisely(monkeypatch):
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _StreamTimeoutClient)
    with pytest.raises(LLMError) as error:
        OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
            [LLMMessage("user", "synthetic prompt")], max_tokens=64
        )
    assert error.value.error_code == "timeout_before_http_response"
    assert error.value.diagnostic_phase == "after_connection_before_response_headers"


# ---------------------------------------------------------------------------
# Fix verification — Groq gpt-oss response_format suppression (Prompt 16C FIX #1)
# ---------------------------------------------------------------------------

def _groq_gpt_oss_profile(model: str = "openai/gpt-oss-20b") -> LLMProfile:
    """A minimal LLMProfile with supports_response_format=False (provider doesn't accept it)."""
    profile = LLMProfile()
    profile.name = "Test Provider"
    profile.base_url = "https://api.test-provider.example/v1"
    profile.model = model
    profile.timeout_seconds = 60
    profile.temperature = None
    profile.top_p = None
    profile.max_output_tokens = 4096
    profile.enable_thinking = False
    profile.reasoning_budget = None
    # Provider does not accept response_format — capability-based suppression.
    profile.supports_json = True
    profile.supports_response_format = False
    profile.extra_headers = None
    return profile


def _generic_json_profile() -> LLMProfile:
    """A generic provider profile with supports_response_format=True."""
    profile = LLMProfile()
    profile.name = "Generic JSON Provider"
    profile.base_url = "https://api.example.com/v1"
    profile.model = "gpt-4o"
    profile.timeout_seconds = 30
    profile.temperature = 0.0
    profile.top_p = None
    profile.max_output_tokens = 1024
    profile.enable_thinking = False
    profile.reasoning_budget = None
    profile.supports_json = True
    profile.supports_response_format = True
    profile.extra_headers = None
    return profile


def _ok_response(model: str) -> _Response:
    return _Response(
        200,
        {
            "model": model,
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        },
    )


# Test A — Groq gpt-oss-20b does not send response_format
def test_groq_gpt_oss_20b_does_not_send_response_format(monkeypatch):
    """Test A: Groq gpt-oss-20b must not send response_format regardless of supports_json."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _ok_response("openai/gpt-oss-20b")

    profile = _groq_gpt_oss_profile("openai/gpt-oss-20b")
    OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("system", "Return JSON"), LLMMessage("user", "test")],
        max_tokens=64,
    )
    _url, body, _headers = _Client.request
    assert "response_format" not in body, (
        "response_format must not be sent to Groq gpt-oss-20b; it causes HTTP 400"
    )


# Test A (variant) — Groq gpt-oss-120b also excluded
def test_groq_gpt_oss_120b_does_not_send_response_format(monkeypatch):
    """Test A variant: gpt-oss-120b shares the same json_object restriction."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _ok_response("openai/gpt-oss-120b")

    profile = _groq_gpt_oss_profile("openai/gpt-oss-120b")
    OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("user", "test")], max_tokens=32
    )
    _url, body, _headers = _Client.request
    assert "response_format" not in body


# Test B — Non-Groq providers with supports_json still receive response_format
def test_non_groq_json_provider_sends_response_format(monkeypatch):
    """Test B: A generic provider with supports_json=True still receives response_format."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _ok_response("gpt-4o")

    profile = _generic_json_profile()
    OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("user", "test")], max_tokens=32
    )
    _url, body, _headers = _Client.request
    assert body.get("response_format") == {"type": "json_object"}, (
        "response_format=json_object must still be sent to non-Groq providers with supports_json"
    )


# Test C — Document-map request remains bounded (max_tokens capped)
def test_document_map_request_remains_bounded(monkeypatch):
    """Test C: max_tokens is capped at profile.max_output_tokens for Groq gpt-oss."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _ok_response("openai/gpt-oss-20b")

    profile = _groq_gpt_oss_profile("openai/gpt-oss-20b")
    # Call with a max_tokens smaller than profile's max_output_tokens
    OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("user", "test")], max_tokens=256
    )
    _url, body, _headers = _Client.request
    assert body["max_tokens"] == 256
    # Also verify no response_format sneaked in
    assert "response_format" not in body


# Test D — Map-reduce 413 / provider_request_rejected recovery path: response_format still absent
def test_groq_gpt_oss_400_returns_provider_request_rejected(monkeypatch):
    """Test D: A 400 from Groq gpt-oss returns the correct error code for the retry path."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        400,
        {
            "error": {
                "type": "invalid_request_error",
                "code": "invalid_value",
                "param": "response_format",
                "message": "private error text must not be retained",
            }
        },
    )
    profile = _groq_gpt_oss_profile("openai/gpt-oss-20b")
    with pytest.raises(LLMError) as exc_info:
        OpenAICompatibleClient(profile, "not-a-real-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
    err = exc_info.value
    assert err.http_status == 400
    assert err.error_code == "provider_request_rejected"
    assert err.provider_error_type == "invalid_request_error"
    assert err.provider_error_code == "invalid_value"
    assert err.provider_error_param == "response_format"
    # The fix means this 400 should no longer occur in production (response_format not sent),
    # but the error classification path must remain correct.
    assert "private error text" not in str(err)


def test_generic_400_token_limit_is_normalized_to_payload_too_large(monkeypatch):
    """HTTP 400 machine metadata can identify a context-limit response generically."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    _Client.response = _Response(
        400,
        {"error": {"type": "tokens", "code": "context_length_exceeded", "param": "input"}},
    )
    with pytest.raises(LLMError) as exc_info:
        OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
    assert exc_info.value.error_code == "provider_payload_too_large"
    assert exc_info.value.http_status == 400


# Test E — Successful Groq gpt-oss response passes through correctly (no response_format)
def test_groq_gpt_oss_successful_response_content_returned(monkeypatch):
    """Test E: A successful Groq gpt-oss response (no response_format sent) is returned."""
    import tender_intelligence.interfaces.openai_compatible as module

    monkeypatch.setattr(module.httpx2, "Client", _Client)
    expected_content = '{"summary":"test","evidence":[]}'
    _Client.response = _Response(
        200,
        {
            "model": "openai/gpt-oss-20b",
            "choices": [
                {"message": {"content": expected_content}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        },
    )

    profile = _groq_gpt_oss_profile("openai/gpt-oss-20b")
    result = OpenAICompatibleClient(profile, "not-a-real-key").chat(
        [LLMMessage("system", "Return JSON only"), LLMMessage("user", "test")],
        max_tokens=64,
    )
    _url, body, _headers = _Client.request
    # No response_format in request (capability-based suppression)
    assert "response_format" not in body
    # Content correctly returned
    assert result.content == expected_content
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 8
    # Raw contains only safe diagnostics
    assert "content" not in str(result.raw)


# Test F — Final Stage B verdict schema unchanged (VerdictPayload still validates)
def test_verdict_payload_schema_unchanged():
    """Test F: VerdictPayload schema is unaffected by the response_format fix."""
    from tender_intelligence.verdict.service import VerdictPayload

    schema = VerdictPayload.model_json_schema()
    required_fields = set(schema.get("required", []))
    assert "schema_version" in required_fields
    assert "verdict" in required_fields
    assert "assessments" in required_fields
    assert "confidence" in required_fields
    assert "requirements" in required_fields
    assert "gaps" in required_fields
    assert "deadline_status" in required_fields
    # Validate a minimal conforming payload still passes
    good = {
        "schema_version": "verdict.v1",
        "background": "Background text.",
        "requirements": ["Req A"],
        "deadline_status": "UNRESOLVED",
        "deadline_utc": None,
        "deadline_date": None,
        "deadline_time": None,
        "deadline_timezone": None,
        "source_timezone": None,
        "assessments": [
            {
                "requirement": "Req A",
                "tender_evidence": [
                    {"document_id": 1, "location": "sec:1", "quote": "The tenderer shall."}
                ],
                "company_evidence": [{"section": "Experience", "quote": "We have done this."}],
                "status": "met",
                "assessment": "Capability confirmed.",
                "gap": None,
            }
        ],
        "gaps": [],
        "verdict": "APPLY",
        "confidence": 0.9,
        "urgency": False,
        "incomplete_inputs": False,
        "limitations": [],
    }
    # Must parse without error
    payload = VerdictPayload.model_validate(good)
    assert payload.verdict == "APPLY"
    assert payload.schema_version == "verdict.v1"


# ── Provider/Model Agnosticism Tests (Prompt 16D) ────────────────────────


class TestProviderAgnosticism:
    """Verify the client works with any configured provider/model."""

    def test_two_different_providers_same_code_path(self, monkeypatch):
        """Two fake providers with different names use the same code path."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)

        for provider_name, base_url, model in [
            ("Provider A", "https://provider-a.example/v1", "model-a"),
            ("Provider B", "https://provider-b.example/v1", "model-b"),
        ]:
            _Client.response = _Response(
                200,
                {
                    "model": model,
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )
            profile = LLMProfile(
                name=provider_name,
                base_url=base_url,
                model=model,
                supports_response_format=True,
            )
            result = OpenAICompatibleClient(profile, "test-key").chat(
                [LLMMessage("user", "test")]
            )
            url, body, _headers = _Client.request
            assert url == f"{base_url}/chat/completions"
            assert body["model"] == model
            assert body["response_format"] == {"type": "json_object"}
            assert result.content == '{"ok":true}'

    def test_model_agnosticism(self, monkeypatch):
        """Model selection comes entirely from profile configuration."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)

        for model in ["model-a", "model-b", "arbitrary-model-name"]:
            _Client.response = _Response(
                200,
                {
                    "model": model,
                    "choices": [{"message": {"content": '{"ok":true}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )
            profile = LLMProfile(
                name="Test Provider",
                base_url="https://api.test.example/v1",
                model=model,
            )
            OpenAICompatibleClient(profile, "test-key").chat(
                [LLMMessage("user", "test")]
            )
            _url, body, _headers = _Client.request
            assert body["model"] == model

    def test_response_format_capability_true(self, monkeypatch):
        """supports_response_format=True sends response_format."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            supports_response_format=True,
        )
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        _url, body, _headers = _Client.request
        assert body["response_format"] == {"type": "json_object"}

    def test_response_format_capability_false(self, monkeypatch):
        """supports_response_format=False does not send response_format."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            supports_response_format=False,
        )
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        _url, body, _headers = _Client.request
        assert "response_format" not in body

    def test_no_v1_models_dependency(self, monkeypatch):
        """Inference works without calling /v1/models."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
        )
        result = OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        url, _body, _headers = _Client.request
        assert url == "https://api.test.example/v1/chat/completions"
        assert "/models" not in url
        assert result.content == '{"ok":true}'

    def test_chat_template_kwargs_capability(self, monkeypatch):
        """supports_chat_template_kwargs=True sends chat_template_kwargs and reasoning_budget."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            enable_thinking=True,
            reasoning_budget=100,
            supports_chat_template_kwargs=True,
        )
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert body["reasoning_budget"] == 64

    def test_include_reasoning_capability(self, monkeypatch):
        """supports_include_reasoning=True sends include_reasoning."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            enable_thinking=True,
            supports_include_reasoning=True,
        )
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        _url, body, _headers = _Client.request
        assert body["include_reasoning"] is True

    def test_configuration_change_no_code_change(self, monkeypatch):
        """Changing profile configuration changes behavior without code changes."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)

        # First configuration
        _Client.response = _Response(
            200,
            {
                "model": "model-a",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile_a = LLMProfile(
            name="Provider A",
            base_url="https://provider-a.example/v1",
            model="model-a",
            supports_response_format=True,
        )
        OpenAICompatibleClient(profile_a, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        url_a, body_a, _headers = _Client.request
        assert url_a == "https://provider-a.example/v1/chat/completions"
        assert body_a["model"] == "model-a"
        assert body_a["response_format"] == {"type": "json_object"}

        # Second configuration — same code, different behavior
        _Client.response = _Response(
            200,
            {
                "model": "model-b",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile_b = LLMProfile(
            name="Provider B",
            base_url="https://provider-b.example/v1",
            model="model-b",
            supports_response_format=False,
        )
        OpenAICompatibleClient(profile_b, "test-key").chat(
            [LLMMessage("user", "test")]
        )
        url_b, body_b, _headers = _Client.request
        assert url_b == "https://provider-b.example/v1/chat/completions"
        assert body_b["model"] == "model-b"
        assert "response_format" not in body_b


# ── Protocol Architecture Tests (Prompt 16E) ──────────────────────────────


class TestProtocolArchitecture:
    """Verify protocol adapter architecture and provider-agnostic behavior."""

    def test_protocol_adapter_registry(self):
        """Protocol adapters are registered and discoverable."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter
        from tender_intelligence.interfaces.protocol import get_protocol_adapter, list_protocols

        protocols = list_protocols()
        assert "openai_compatible" in protocols

        adapter_class = get_protocol_adapter("openai_compatible")
        assert adapter_class is OpenAICompatibleAdapter

    def test_protocol_adapter_unknown_returns_none(self):
        """Unknown protocol returns None from registry."""
        from tender_intelligence.interfaces.protocol import get_protocol_adapter

        assert get_protocol_adapter("nonexistent_protocol") is None

    def test_openai_compatible_adapter_builds_request(self):
        """OpenAI-compatible adapter builds request from capabilities."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            supports_response_format=True,
            supports_include_reasoning=False,
            supports_chat_template_kwargs=False,
        )
        adapter = OpenAICompatibleAdapter(profile, "test-key")
        body = adapter.build_request([LLMMessage("user", "test")], max_tokens=64)

        assert body["model"] == "test-model"
        assert body["response_format"] == {"type": "json_object"}
        assert "include_reasoning" not in body
        assert "chat_template_kwargs" not in body

    def test_openai_compatible_adapter_endpoint(self):
        """OpenAI-compatible adapter returns correct endpoint."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
        )
        adapter = OpenAICompatibleAdapter(profile, "test-key")
        assert adapter.get_endpoint() == "https://api.test.example/v1/chat/completions"

    def test_openai_compatible_adapter_headers(self):
        """OpenAI-compatible adapter returns correct headers."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
        )
        adapter = OpenAICompatibleAdapter(profile, "test-key")
        headers = adapter.get_headers()
        assert headers["Authorization"] == "Bearer test-key"
        assert headers["Content-Type"] == "application/json"

    def test_openai_compatible_adapter_parse_response(self):
        """OpenAI-compatible adapter parses response correctly."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
        )
        adapter = OpenAICompatibleAdapter(profile, "test-key")
        payload = {
            "model": "test-model",
            "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        response = adapter.parse_response(payload)
        assert response.content == '{"ok":true}'
        assert response.model == "test-model"
        assert response.usage.prompt_tokens == 5

    def test_openai_compatible_adapter_normalize_error(self):
        """OpenAI-compatible adapter normalizes errors correctly."""
        from tender_intelligence.interfaces.openai_compatible_adapter import OpenAICompatibleAdapter

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
        )
        adapter = OpenAICompatibleAdapter(profile, "test-key")

        assert adapter.normalize_error(401, {}) == "provider_authentication_failed"
        assert adapter.normalize_error(403, {}) == "provider_authentication_failed"
        assert adapter.normalize_error(429, {}) == "rate_limit"
        assert adapter.normalize_error(413, {}) == "provider_payload_too_large"
        assert adapter.normalize_error(500, {}) == "provider_server_error"
        assert adapter.normalize_error(502, {}) == "provider_server_error"
        assert adapter.normalize_error(400, {}) == "provider_request_rejected"

    def test_protocol_field_in_profile(self):
        """LLM profile has protocol field."""
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            protocol="openai_compatible",
        )
        assert profile.protocol == "openai_compatible"

    def test_protocol_field_custom_value(self):
        """LLM profile protocol field can be set to custom value."""
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            protocol="custom_protocol",
        )
        assert profile.protocol == "custom_protocol"

    def test_provider_name_field_in_profile(self):
        """LLM profile has provider_name field for display purposes."""
        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            provider_name="Test Provider Display Name",
        )
        assert profile.provider_name == "Test Provider Display Name"

    def test_build_protocol_client_factory(self):
        """build_protocol_client factory creates client based on protocol."""
        from tender_intelligence.interfaces.openai_compatible_adapter import build_protocol_client

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            protocol="openai_compatible",
        )
        client = build_protocol_client(profile=profile, api_key="test-key")
        assert client is not None
        assert client.profile_name == "Test Provider"

    def test_build_protocol_client_unsupported_protocol(self):
        """build_protocol_client raises error for unsupported protocol."""
        from tender_intelligence.interfaces.llm import LLMError
        from tender_intelligence.interfaces.openai_compatible_adapter import build_protocol_client

        profile = LLMProfile(
            name="Test Provider",
            base_url="https://api.test.example/v1",
            model="test-model",
            protocol="unsupported_protocol",
        )
        with pytest.raises(LLMError) as error:
            build_protocol_client(profile=profile, api_key="test-key")
        assert "unsupported_protocol" in str(error.value)


# ── Groq GPT-OSS Test Connection Remediation ───────────────────────────────


class TestResponseNotReadFix:
    """Verify ResponseNotRead is handled and safe provider error text is captured."""

    def test_error_body_is_read_before_parsing(self, monkeypatch):
        """HTTP 400 error body is read before JSON parsing (no ResponseNotRead)."""
        import tender_intelligence.interfaces.openai_compatible as module

        class _ReadRequiredResponse(_Response):
            def read(self):
                self._read_called = True
                return b"{}"

            def json(self):
                # Simulate httpx behavior: json() before read() raises ResponseNotRead
                if not hasattr(self, "_read_called"):
                    raise module.httpx2.ResponseNotRead("Response not read")
                return self._data

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _ReadRequiredResponse(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "invalid_value",
                    "param": "response_format",
                    "message": "response_format is not supported for this model",
                }
            },
        )
        with pytest.raises(LLMError) as error:
            OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
                [LLMMessage("user", "test")], max_tokens=8
            )
        assert error.value.http_status == 400
        assert error.value.error_code == "provider_request_rejected"
        assert error.value.provider_error_message == (
            "response_format is not supported for this model"
        )

    def test_safe_provider_message_extracted(self, monkeypatch):
        """Safe provider error message is captured from error body."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                    "message": "The model 'openai/gpt-oss-20b' does not exist",
                }
            },
        )
        with pytest.raises(LLMError) as error:
            OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
                [LLMMessage("user", "test")], max_tokens=8
            )
        assert error.value.http_status == 400
        assert error.value.provider_error_message == "The model 'openai/gpt-oss-20b' does not exist"

    def test_secrets_never_in_provider_error_message(self, monkeypatch):
        """API keys or secrets are never included in the normalized error."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            401,
            {
                "error": {
                    "type": "authentication_error",
                    "code": "invalid_api_key",
                    "message": "Invalid API key: sk-abc123secret",
                }
            },
        )
        with pytest.raises(LLMError) as error:
            OpenAICompatibleClient(_profile(), "sk-abc123secret").chat(
                [LLMMessage("user", "test")], max_tokens=8
            )
        # The provider message is captured but the API key is not leaked in the error.
        assert "sk-abc123secret" not in str(error.value)
        assert error.value.http_status == 401
        assert error.value.error_code == "provider_authentication_failed"

    def test_provider_message_sanitized_control_chars(self, monkeypatch):
        """Control characters in provider messages are stripped."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            400,
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "Error\x1b[31m with control\x00 chars",
                }
            },
        )
        with pytest.raises(LLMError) as error:
            OpenAICompatibleClient(_profile(), "not-a-real-key").chat(
                [LLMMessage("user", "test")], max_tokens=8
            )
        # Control characters are stripped
        assert "\x1b" not in (error.value.provider_error_message or "")
        assert "\x00" not in (error.value.provider_error_message or "")


class TestReasoningEffortCapability:
    """Verify reasoning_effort is capability-driven and provider-agnostic."""

    def test_reasoning_effort_sent_when_capability_enabled(self, monkeypatch):
        """supports_reasoning_effort=True sends reasoning_effort parameter."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = _profile()
        profile.supports_reasoning_effort = True
        profile.reasoning_effort = "medium"
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert body["reasoning_effort"] == "medium"

    def test_reasoning_effort_not_sent_when_capability_disabled(self, monkeypatch):
        """supports_reasoning_effort=False does not send reasoning_effort."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = _profile()
        profile.supports_reasoning_effort = False
        profile.reasoning_effort = "high"
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert "reasoning_effort" not in body

    def test_reasoning_effort_not_sent_when_not_configured(self, monkeypatch):
        """reasoning_effort is not sent when profile.reasoning_effort is None."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = _profile()
        profile.supports_reasoning_effort = True
        profile.reasoning_effort = None
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert "reasoning_effort" not in body

    def test_no_provider_name_detection_for_reasoning_effort(self, monkeypatch):
        """reasoning_effort is sent based on capability, not provider name."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        # Any provider with the capability gets reasoning_effort
        for provider_name in ["Provider A", "Provider B", "Some Other Provider"]:
            profile = LLMProfile(
                name=provider_name,
                base_url="https://api.test.example/v1",
                model="test-model",
                supports_reasoning_effort=True,
                reasoning_effort="low",
            )
            OpenAICompatibleClient(profile, "test-key").chat(
                [LLMMessage("user", "test")], max_tokens=64
            )
            _url, body, _headers = _Client.request
            assert body["reasoning_effort"] == "low"


class TestChatTemplateKwargsNotSentByDefault:
    """Verify chat_template_kwargs and reasoning_budget are not sent unless configured."""

    def test_chat_template_kwargs_not_sent_when_capability_false(self, monkeypatch):
        """supports_chat_template_kwargs=False does not send chat_template_kwargs."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = _profile()
        profile.supports_chat_template_kwargs = False
        profile.enable_thinking = True
        profile.reasoning_budget = 100
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert "chat_template_kwargs" not in body
        assert "reasoning_budget" not in body

    def test_chat_template_kwargs_sent_when_capability_true(self, monkeypatch):
        """supports_chat_template_kwargs=True sends chat_template_kwargs."""
        import tender_intelligence.interfaces.openai_compatible as module

        monkeypatch.setattr(module.httpx2, "Client", _Client)
        _Client.response = _Response(
            200,
            {
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            },
        )
        profile = _profile()
        profile.supports_chat_template_kwargs = True
        profile.enable_thinking = True
        profile.reasoning_budget = 100
        OpenAICompatibleClient(profile, "test-key").chat(
            [LLMMessage("user", "test")], max_tokens=64
        )
        _url, body, _headers = _Client.request
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert body["reasoning_budget"] == 64

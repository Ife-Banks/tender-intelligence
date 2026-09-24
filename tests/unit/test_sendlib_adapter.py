"""Unit tests for the Sendlib provider adapter (prompt 11 §5, §33).

The wire contract (v1.1 §5.10.3) is exercised against an in-process ``httpx2.MockTransport``
only — no real network is ever touched, and the default base URL is always overridden.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest

from tender_intelligence.core import errors as codes
from tender_intelligence.mail.errors import MailError
from tender_intelligence.mail.message import EmailMessage, MailAttachment
from tender_intelligence.mail.provider import Capabilities, SendResult
from tender_intelligence.mail.sendlib import (
    DEFAULT_BASE_URL,
    SENDLIB_FREE_CAPABILITIES,
    SendlibProvider,
    _json_from,
    provider_from_row,
)

BASE = "https://sendlib.test.example"


def _provider(
    *,
    handler,
    capabilities: Capabilities | None = None,
    api_key: str = "secret-key",
) -> SendlibProvider:
    transport = httpx2.MockTransport(handler)
    return SendlibProvider(
        api_key=api_key,
        from_address="tenders@opex.example",
        from_name="OPEX Tender Intelligence",
        reply_to=None,
        capabilities=capabilities or SENDLIB_FREE_CAPABILITIES,
        base_url=BASE,
        client_factory=lambda: httpx2.Client(transport=transport),
    )


def _message() -> EmailMessage:
    return EmailMessage(
        to=("owner@opex.example",),
        subject="WAHO Expression of Interest – Assessment Report: Platform",
        text_body="1. Background\n...",
        html_body="<p>...</p>",
        attachments=(
            MailAttachment(
                filename="annex.pdf", content=b"pdf-bytes", content_type="application/pdf"
            ),
        ),
        dedupe_key="key-123",
    )


class _RecordingHandler:
    """Answers /api/send with the configured status and records what it saw."""

    def __init__(self, status: int = 200, payload: dict[str, Any] | None = None) -> None:
        self.status = status
        self.payload = payload
        self.requests: list[dict[str, Any]] = []
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(
            {
                "url": str(request.url),
                "auth": request.headers.get("authorization"),
                "dedupe": request.headers.get("x-tender-notification-dedupe-key"),
            }
        )
        self.bodies.append(body)
        data = self.payload
        return (
            httpx2.Response(self.status, json=data)
            if data is not None
            else httpx2.Response(self.status)
        )


class TestJsonPayload:
    def test_from_config_and_attachments(self) -> None:
        msg = _message()
        payload = _json_from(msg, from_address="a@x.io", from_name="N", reply_to="r@x.io")
        assert payload["from"] == "N <a@x.io>"
        assert payload["to"] == ["owner@opex.example"]
        assert payload["replyTo"] == "r@x.io"
        assert "reply_to" not in payload
        attach = payload["attachments"][0]
        assert attach["filename"] == "annex.pdf"
        assert attach["type"] == "application/pdf"
        assert attach["content"] == "cGRmLWJ5dGVz"  # base64 of b"pdf-bytes"
        assert "content_type" not in attach
        assert "headers" not in payload

    def test_omits_empty_cc_bcc(self) -> None:
        payload = _json_from(_message(), from_address="a@x.io", from_name=None, reply_to=None)
        assert "cc" not in payload
        assert "bcc" not in payload


class TestSendlibProvider:
    def test_sends_to_api_send_with_bearer_and_base(self) -> None:
        handler = _RecordingHandler(200, {"id": "send-42"})
        result = _provider(handler=handler).send(_message())
        assert isinstance(result, SendResult)
        assert result.ok and result.provider_message_id == "send-42"
        assert handler.requests[0]["url"] == f"{BASE}/api/send"
        assert handler.requests[0]["auth"] == "Bearer secret-key"
        assert handler.requests[0]["dedupe"] == "key-123"

    def test_2xx_without_id_still_ok(self) -> None:
        result = _provider(handler=_RecordingHandler(200, {})).send(_message())
        assert result.ok and result.provider_message_id is None

    def test_free_tier_capabilities_default(self) -> None:
        provider = SendlibProvider(api_key="k", from_address="a@x.io")
        assert provider.capabilities == SENDLIB_FREE_CAPABILITIES

    def test_default_base_url_constant(self) -> None:
        assert DEFAULT_BASE_URL == "https://sendlib.samueltuoyo.com"

    @pytest.mark.parametrize(
        ("status", "expected_code", "retryable", "duplicate"),
        [
            (401, codes.MAIL_INVALID_CREDENTIALS, False, False),
            (403, codes.MAIL_INVALID_CREDENTIALS, False, False),
            (400, codes.MAIL_UNSUPPORTED_REQUEST, False, False),
            (422, codes.MAIL_UNSUPPORTED_REQUEST, False, False),
            (429, codes.MAIL_RATE_LIMITED, True, False),
            (500, codes.MAIL_HTTP_5XX, True, False),
            (503, codes.MAIL_HTTP_5XX, True, False),
        ],
    )
    def test_http_classification(self, status, expected_code, retryable, duplicate) -> None:
        with pytest.raises(MailError) as caught:
            _provider(handler=_RecordingHandler(status)).send(_message())
        assert caught.value.error_code == expected_code
        assert caught.value.retryable is retryable
        assert caught.value.possible_duplicate is duplicate

    def test_transport_error_is_retryable_and_possible_duplicate(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ReadError("connection reset")

        with pytest.raises(MailError) as caught:
            _provider(handler=handler).send(_message())
        err = caught.value
        assert err.error_code == codes.MAIL_TIMEOUT
        assert err.retryable
        assert err.possible_duplicate
        assert err.__cause__ is None
        assert err.__context__ is None

    def test_connect_error_classified_as_timeout(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            raise httpx2.ConnectError("unreachable")

        with pytest.raises(MailError) as caught:
            _provider(handler=handler).send(_message())
        assert caught.value.error_code == codes.MAIL_TIMEOUT
        assert caught.value.retryable
        assert not caught.value.possible_duplicate

    def test_invalid_recipient_prevented_before_send(self) -> None:
        handler = _RecordingHandler(200)
        provider = _provider(handler=handler)
        with pytest.raises(ValueError):
            provider.send(EmailMessage(to=("not-valid",), subject="x"))
        assert handler.requests == []

    def test_empty_api_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            SendlibProvider(api_key="", from_address="a@x.io")


class TestProviderFromRow:
    def test_builds_from_decrypted_credentials(self) -> None:
        provider = provider_from_row(
            credentials={"api_key": "k2"},
            from_address="from@x.io",
            from_name="N",
            reply_to=None,
            capabilities=Capabilities(max_attachments=5),
        )
        assert provider.from_address == "from@x.io"
        assert provider.capabilities.max_attachments == 5

    def test_missing_api_key_is_configuration_error(self) -> None:
        with pytest.raises(MailError) as caught:
            provider_from_row(
                credentials={},
                from_address="from@x.io",
                from_name=None,
                reply_to=None,
                capabilities=Capabilities(),
            )
        assert caught.value.error_code == codes.MAIL_CONFIGURATION_ERROR

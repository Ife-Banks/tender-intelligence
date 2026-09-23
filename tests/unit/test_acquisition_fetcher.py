"""Unit tests for the acquisition fetcher (prompt 08 §4, §5, §13).

The fetcher turns a discovered attachment URL into bounded, validated bytes. These tests
exercise the URL-scheme guard, HTTP/failure classification, retry-with-backoff (policy
defaults zeroed so tests do not sleep), size guards and URL redaction — everything except
the storage/persistence behaviour, which belongs to the service integration suite.
"""

from __future__ import annotations

import httpx2
import pytest
from httpx2 import MockTransport, Request, Response

from tender_intelligence.acquisition.errors import (
    HTTP_ERROR,
    RESPONSE_TOO_LARGE,
    TRANSPORT,
    UNSUPPORTED_SCHEME,
    DocumentAcquisitionError,
)
from tender_intelligence.acquisition.fetcher import DocumentFetcher, redact_url
from tender_intelligence.sources.policy import CrawlPolicy


def _policy(*, max_retries: int = 2) -> CrawlPolicy:
    return CrawlPolicy(
        request_interval_seconds=0.0,
        backoff_base_seconds=0.0,
        backoff_max_seconds=0.0,
        max_retries=max_retries,
    )


def _fetcher(
    handler, *, max_retries: int = 2, max_response_bytes: int = 512 * 1024 * 1024
) -> DocumentFetcher:
    return DocumentFetcher(
        _policy(max_retries=max_retries),
        transport=MockTransport(handler),
        max_response_bytes=max_response_bytes,
    )


class TestSchemeAndHttp:
    def test_unsupported_scheme_rejected(self) -> None:
        def handler(request: Request) -> Response:
            raise AssertionError("must not be contacted")

        fetcher = _fetcher(handler)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("file:///etc/passwd")
        exc = exc_info.value
        assert exc.category == UNSUPPORTED_SCHEME
        assert exc.retryable is False
        assert exc.context["scheme"] == "file"

    def test_404_is_a_permanent_failure(self) -> None:
        called = 0

        def handler(request: Request) -> Response:
            nonlocal called
            called += 1
            return Response(404, text="not found")

        fetcher = _fetcher(handler)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("https://src.example/missing.pdf")
        exc = exc_info.value
        assert exc.category == HTTP_ERROR
        assert exc.retryable is False
        assert exc.context["status_code"] == 404
        assert exc.context["status_category"] == "client_error"
        assert called == 1  # never retried: 404 is permanent

    def test_retry_status_eventually_succeeds(self) -> None:
        attempts = []

        def handler(request: Request) -> Response:
            attempts.append(str(request.url))
            if len(attempts) == 1:
                return Response(503, text="service unavailable")
            return Response(200, content=b"first attempt failed, second worked")

        fetcher = _fetcher(handler, max_retries=3)
        response = fetcher.fetch("https://src.example/annex.pdf")
        assert response.content == b"first attempt failed, second worked"
        assert response.status_code == 200
        assert len(attempts) == 2

    def test_retry_limit_respected(self) -> None:
        attempts = []

        def handler(request: Request) -> Response:
            attempts.append(str(request.url))
            return Response(503, text="always down")

        fetcher = _fetcher(handler, max_retries=2)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("https://src.example/always.pdf")
        exc = exc_info.value
        assert exc.category == HTTP_ERROR
        assert exc.retryable is True  # 503 stays in retry_statuses
        assert exc.context["attempts"] == 3  # initial try + 2 retries
        assert len(attempts) == 3

    def test_transport_error_retried_then_succeeds(self) -> None:
        attempts = []

        def handler(request: Request) -> Response:
            attempts.append(str(request.url))
            if len(attempts) == 1:
                raise httpx2.ConnectError("connection refused")
            return Response(200, content=b"recovered")

        fetcher = _fetcher(handler, max_retries=3)
        response = fetcher.fetch("https://src.example/flaky.pdf")
        assert response.content == b"recovered"
        assert len(attempts) == 2

    def test_transport_error_exhausted(self) -> None:
        attempts = []

        def handler(request: Request) -> Response:
            attempts.append(str(request.url))
            raise httpx2.ReadError("peer reset")

        fetcher = _fetcher(handler, max_retries=2)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("https://src.example/never.pdf")
        exc = exc_info.value
        assert exc.category == TRANSPORT
        assert exc.retryable is True
        assert exc.context["last_error"] == "ReadError"
        assert len(attempts) == 3


class TestSizeGuards:
    def test_content_length_precheck(self) -> None:
        def handler(request: Request) -> Response:
            return Response(
                200,
                content=b"small",
                headers={"content-length": "1000000"},
            )

        fetcher = _fetcher(handler, max_response_bytes=100)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("https://src.example/huge.pdf")
        exc = exc_info.value
        assert exc.category == RESPONSE_TOO_LARGE
        assert exc.context["declared_bytes"] == 1000000
        assert exc.context["limit_bytes"] == 100
        assert exc.retryable is False

    def test_actual_bytes_checked_when_header_understates(self) -> None:
        # A lying/minimal content-length does not protect an oversized body: the
        # post-download guard measures the real bytes and rejects the response.
        def handler(request: Request) -> Response:
            return Response(
                200, content=b"x" * 50, headers={"content-length": "5"}
            )

        fetcher = _fetcher(handler, max_response_bytes=10)
        with pytest.raises(DocumentAcquisitionError) as exc_info:
            fetcher.fetch("https://src.example/actual.pdf")
        exc = exc_info.value
        assert exc.category == RESPONSE_TOO_LARGE
        assert exc.context["actual_bytes"] == 50
        assert exc.context["limit_bytes"] == 10


class TestRedactUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (
                "https://src.example/dl?a=1&token=SECRET&b=2",
                "https://src.example/dl?a=1&b=2",
            ),
            (
                "https://bucket.s3.example/item?sig=abc123&x=1&access_token=t0k",
                "https://bucket.s3.example/item?x=1",
            ),
            ("https://src.example/plain", "https://src.example/plain"),
            (
                "https://user:pass@src.example/x?a=1",
                "https://****@src.example/x?a=1",
            ),
            # Keep-blank-value query parameters are still subject to redaction.
            ("https://src.example/x?apikey=&a=1", "https://src.example/x?a=1"),
        ],
    )
    def test_redacts_credentials(self, url: str, expected: str) -> None:
        assert redact_url(url) == expected


def test_response_content_type_normalised() -> None:
    def handler(request: Request) -> Response:
        return Response(
            200,
            content=b"pdf-bytes",
            headers={"content-type": "Application/PDF; charset=utf-8"},
        )

    fetcher = _fetcher(handler)
    response = fetcher.fetch("https://src.example/doc.pdf")
    assert response.content_type() == "application/pdf"
    assert response.is_html is False
    assert response.final_url == "https://src.example/doc.pdf"

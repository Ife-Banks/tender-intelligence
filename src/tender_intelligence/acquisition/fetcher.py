""":mod:`tender_intelligence.acquisition.fetcher` — binary document fetcher (prompt 08 §4, §13).

Downloads a specific discovered attachment URL under the project's crawl policy
(``CrawlPolicy``: timeout, rate-limit pacing, retry-with-backoff, retry statuses) with
extra acquisition guards required by prompt 08 §4/§5:

* http/https schemes only — local-file and other unsafe schemes are refused;
* connection/read timeout from the policy;
* redirects followed with a bounded maximum;
* maximum response size (Content-Length pre-check and byte-count post-check);
* transient failures and retry statuses (429/5xx) retried with backoff; permanent
  failures (404, bad scheme/URL) fail immediately, never retried indefinitely;
* TLS verification stays enabled (httpx default, ``verify=True``).

Log/error context never includes authorization material: headers and cookies are never
logged and URLs are redacted of credential-like query parameters before they appear in
logs or error context (prompt 08 §4).
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from dataclasses import dataclass
from threading import Lock

import httpx2

from tender_intelligence.acquisition.errors import (
    HTTP_ERROR,
    RESPONSE_TOO_LARGE,
    TRANSPORT,
    UNSUPPORTED_SCHEME,
    DocumentAcquisitionError,
)
from tender_intelligence.sources.policy import CrawlPolicy

log = logging.getLogger("tender_intelligence.acquisition.fetcher")

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Query parameters that may carry credentials in a signed/exposed URL; removed from any
# URL that reaches a log line or error context (prompt 08 §4).
_SENSITIVE_QUERY_NAMES = (
    "token",
    "access_token",
    "auth",
    "authorization",
    "signature",
    "sig",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "key",
    "credential",
    "apikey",
    "api_key",
    "secret",
    "password",
    "passwd",
    "x-goog-signature",
    "sas",
    "se",
    "sp",
    "sv",
    "si",
)

_TRANSIENT_TRANSPORT_ERRORS = (
    httpx2.ConnectError,
    httpx2.ReadError,
    httpx2.TimeoutException,
    httpx2.RemoteProtocolError,
    httpx2.TransportError,
)

_HTML_MEDIA_TYPES = ("text/html", "application/xhtml+xml")


@dataclass(frozen=True)
class DocumentResponse:
    """Normalised binary fetch result."""

    status_code: int
    headers: dict[str, str]
    content: bytes
    url: str

    @property
    def final_url(self) -> str:
        return self.url

    def content_type(self) -> str | None:
        value = self.headers.get("content-type")
        return value.split(";", 1)[0].strip().lower() if value else None

    @property
    def is_html(self) -> bool:
        return self.content_type() in _HTML_MEDIA_TYPES


def redact_url(url: str) -> str:
    """Return *url* with credential-like query parameters and credentials removed."""
    parsed = urllib.parse.urlsplit(url)
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SENSITIVE_QUERY_NAMES
    ]
    query = urllib.parse.urlencode(kept) if kept else ""
    netloc = parsed.netloc
    if parsed.username is not None or parsed.password is not None:
        netloc = f"****@{netloc.split('@')[-1]}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, query, ""))


class DocumentFetcher:
    """Fetch attachment bytes under the crawl policy plus acquisition guards."""

    def __init__(
        self,
        policy: CrawlPolicy | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
        max_redirects: int = 5,
        max_response_bytes: int = 512 * 1024 * 1024,
        user_agent: str | None = None,
    ) -> None:
        self._policy = policy or CrawlPolicy()
        self._max_redirects = max_redirects
        self._max_response_bytes = max_response_bytes
        self._client = httpx2.Client(
            transport=transport,
            timeout=httpx2.Timeout(self._policy.timeout_seconds),
            headers={"user-agent": user_agent or self._policy.user_agent},
            follow_redirects=True,
            max_redirects=self._max_redirects,
        )
        self._pace_lock = Lock()
        self._last_request_at = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DocumentFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, url: str) -> DocumentResponse:
        """Download *url* returning the normalised response (bounded, validated)."""
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        if scheme not in ALLOWED_SCHEMES:
            raise DocumentAcquisitionError(
                f"unsupported URL scheme {scheme!r}",
                error_code="document_download_failed",
                category=UNSUPPORTED_SCHEME,
                retryable=False,
                context={"url": redact_url(url), "scheme": scheme},
            )

        attempt = 0
        while True:
            self._pace()
            try:
                response = self._client.get(url)
            except _TRANSIENT_TRANSPORT_ERRORS as exc:  # type: ignore[arg-type]
                log.info(
                    "document fetch transport error (%s, attempt %d)",
                    type(exc).__name__,
                    attempt + 1,
                    extra={
                        "stage": "acquisition",
                        "status": "retrying",
                        "url": redact_url(url),
                        "exc": type(exc).__name__,
                    },
                )
                if attempt >= self._policy.max_retries:
                    raise DocumentAcquisitionError(
                        f"document fetch failed: {url!r}",
                        error_code="document_download_failed",
                        category=TRANSPORT,
                        retryable=True,
                        context={
                            "url": redact_url(url),
                            "attempts": attempt + 1,
                            "last_error": type(exc).__name__,
                        },
                    ) from exc
                attempt += 1
                self._backoff(url, attempt, None)
                continue

            if response.status_code in self._policy.retry_statuses:
                log.info(
                    "document fetch http %d (attempt %d)",
                    response.status_code,
                    attempt + 1,
                    extra={"stage": "acquisition", "status": "retrying", "url": redact_url(url)},
                )
                if attempt >= self._policy.max_retries:
                    raise self._http_failure(url, response.status_code, attempt + 1)
                attempt += 1
                self._backoff(url, attempt, response.headers.get("retry-after"))
                continue

            if response.status_code >= 400:
                raise self._http_failure(url, response.status_code, attempt + 1)

            content_length = response.headers.get("content-length")
            if (
                content_length is not None
                and content_length.isdigit()
                and int(content_length) > self._max_response_bytes
            ):
                raise DocumentAcquisitionError(
                    "document response exceeds the configured size limit",
                    error_code="document_download_failed",
                    category=RESPONSE_TOO_LARGE,
                    retryable=False,
                    context={
                        "url": redact_url(url),
                        "declared_bytes": int(content_length),
                        "limit_bytes": self._max_response_bytes,
                    },
                )

            content = response.content
            if len(content) > self._max_response_bytes:
                raise DocumentAcquisitionError(
                    "document response exceeds the configured size limit",
                    error_code="document_download_failed",
                    category=RESPONSE_TOO_LARGE,
                    retryable=False,
                    context={
                        "url": redact_url(url),
                        "actual_bytes": len(content),
                        "limit_bytes": self._max_response_bytes,
                    },
                )

            document_response = DocumentResponse(
                status_code=response.status_code,
                headers={key.lower(): value for key, value in response.headers.items()},
                content=content,
                url=str(response.url),
            )
            log.info(
                "document fetched (%d bytes, %s)",
                len(document_response.content),
                document_response.content_type() or "unknown mime",
                extra={
                    "stage": "acquisition",
                    "status": "fetched",
                    "url": redact_url(url),
                },
            )
            return document_response

    def _http_failure(self, url: str, status_code: int, attempts: int) -> DocumentAcquisitionError:
        is_retryable = status_code in self._policy.retry_statuses
        return DocumentAcquisitionError(
            f"document fetch failed with HTTP {status_code}",
            error_code="document_download_failed",
            category=HTTP_ERROR,
            retryable=is_retryable,
            context={
                "url": redact_url(url),
                "status_code": status_code,
                "status_category": "server_error" if is_retryable else "client_error",
                "attempts": attempts,
            },
        )

    def _pace(self) -> None:
        interval = self._policy.request_interval_seconds
        if interval <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if self._last_request_at and elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_request_at = time.monotonic()

    def _backoff(self, url: str, attempt: int, retry_after: str | None) -> None:
        if retry_after is not None:
            try:
                delay = max(0.0, float(retry_after))
            except ValueError:
                delay = None
        else:
            delay = None
        if delay is None:
            delay = min(
                self._policy.backoff_max_seconds,
                self._policy.backoff_base_seconds * (2 ** (attempt - 1)),
            )
        log.info(
            "backing off %.2fs before retrying %s (attempt %d)",
            delay,
            url,
            attempt,
            extra={"stage": "acquisition", "status": "retrying", "url": redact_url(url)},
        )
        time.sleep(delay)

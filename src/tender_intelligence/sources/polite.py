""":mod:`tender_intelligence.sources.polite` — polite HTTP fetching for source adapters.

Responsibilities (docs/05 §5.2, prompt 04 §6):
- robots.txt behavior (cached per host, plain-HTTP friendly),
- per-source rate limiting,
- request timeout,
- retry policy with exponential backoff and ``Retry-After`` support.

Everything is injectable so tests run against mocked transports / off-line fixtures and never
hit the live source.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Protocol

import httpx2

from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_UNREACHABLE
from tender_intelligence.interfaces.source import SourceError
from tender_intelligence.sources.policy import CrawlPolicy

log = logging.getLogger("tender_intelligence.sources.polite")

TRANSIENT_TRANSPORT_ERRORS = (
    httpx2.ConnectError,
    httpx2.ReadError,
    httpx2.TimeoutException,
    httpx2.RemoteProtocolError,
    httpx2.TransportError,
)


@dataclass(frozen=True)
class HttpResponse:
    """Normalised fetch result decoupled from the underlying HTTP library."""

    status_code: int
    headers: dict[str, str]
    text: str
    url: str

    @property
    def final_url(self) -> str:
        return self.url


class Fetcher(Protocol):
    """Callable performing a single HTTP GET, returning :class:`HttpResponse`."""

    def __call__(self, url: str) -> HttpResponse:
        ...


class PoliteHttpClient:
    """HTTP client enforcing the crawl policy: robots, pacing, timeout, retries."""

    def __init__(self, client: httpx2.Client, policy: CrawlPolicy) -> None:
        self._client = client
        self._policy = policy
        self._last_request_at = 0.0
        self._pace_lock = Lock()
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._robots_lock = Lock()

    @classmethod
    def build(
        cls,
        policy: CrawlPolicy,
        *,
        transport: httpx2.BaseTransport | None = None,
        follow_redirects: bool = True,
    ) -> PoliteHttpClient:
        client = httpx2.Client(
            transport=transport,
            timeout=httpx2.Timeout(policy.timeout_seconds),
            headers={"user-agent": policy.user_agent},
            follow_redirects=follow_redirects,
        )
        return cls(client=client, policy=policy)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, url: str) -> HttpResponse:
        if self._policy.respect_robots and not self._robots_allows(url):
            raise SourceError(
                "request disallowed by robots.txt",
                error_code=PARSER_MISMATCH,
                context={"url": url, "reason": "robots_disallowed"},
            )

        attempt = 0
        while True:
            self._pace()
            try:
                response = self._client.get(url)
            except TRANSIENT_TRANSPORT_ERRORS as exc:
                log.info(
                    "transport error fetching %s (attempt %d)",
                    url,
                    attempt + 1,
                    extra={"stage": "discovery", "status": "retry", "exc": type(exc).__name__},
                )
                if attempt >= self._policy.max_retries:
                    raise SourceError(
                        f"source unreachable: {url}",
                        error_code=SOURCE_UNREACHABLE,
                        context={"url": url, "last_error": type(exc).__name__},
                    ) from exc
                attempt += 1
                self._backoff(url, attempt, retry_after=None)
                continue

            if response.status_code in self._policy.retry_statuses:
                log.info(
                    "http %d fetching %s (attempt %d)",
                    response.status_code,
                    url,
                    attempt + 1,
                    extra={"stage": "discovery", "status": "retry"},
                )
                if attempt >= self._policy.max_retries:
                    raise SourceError(
                        f"source unreachable: {url} (http {response.status_code})",
                        error_code=SOURCE_UNREACHABLE,
                        context={"url": url, "status_code": response.status_code},
                    )
                attempt += 1
                self._backoff(url, attempt, retry_after=response.headers.get("retry-after"))
                continue

            return HttpResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                text=response.text,
                url=str(response.url),
            )

    def _pace(self) -> None:
        """Sleep so consecutive requests respect the configured minimum interval."""
        interval = self._policy.request_interval_seconds
        if interval <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_at
            if self._last_request_at and elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_request_at = time.monotonic()

    def _backoff(
        self,
        url: str,
        attempt: int,
        retry_after: str | None,
    ) -> None:
        delay = self._retry_after_delay(retry_after)
        if delay is None:
            delay = min(
                self._policy.backoff_max_seconds,
                self._policy.backoff_base_seconds * (2 ** (attempt - 1)),
            )
        log.info(
            "backing off %.1fs before retrying %s (attempt %d)",
            delay,
            url,
            attempt,
            extra={"stage": "discovery", "status": "retry"},
        )
        time.sleep(delay)

    @staticmethod
    def _retry_after_delay(retry_after: str | None) -> float | None:
        if not retry_after:
            return None
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        delay = (when.timestamp() - time.time())
        return max(0.0, delay)

    def _robots_allows(self, url: str) -> bool:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            return True
        robots_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
        parser = self._robots_for(robots_url)
        if parser is None:
            return True
        return parser.can_fetch(self._policy.user_agent, url)

    def _robots_for(self, robots_url: str) -> urllib.robotparser.RobotFileParser | None:
        with self._robots_lock:
            cached = self._robots_cache.get(robots_url)
            if cached is not None:
                return cached
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                response = self._client.get(robots_url)
            except httpx2.HTTPError:
                response = None
            if response is not None and response.status_code == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(response.text.splitlines())
                self._robots_cache[robots_url] = parser
            else:
                code = getattr(response, "status_code", "unreachable")
                log.warning(
                    "robots.txt unavailable at %s (%s); allowing crawl",
                    robots_url,
                    code,
                    extra={"stage": "discovery", "status": "warning"},
                )
            return parser

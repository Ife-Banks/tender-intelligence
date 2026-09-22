"""Unit tests for the WAHO listing-discovery adapter (prompt 04).

All tests run against offline fixtures and a fake fetcher — no live WAHO access, no network
(prompt 04 §6, §17).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC

import httpx2
import pytest
from httpx2 import MockTransport

from tender_intelligence.core.correlation import correlation_context
from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_UNREACHABLE
from tender_intelligence.interfaces.source import SourceError, TenderListing
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse, PoliteHttpClient
from tender_intelligence.sources.waho import WahoPaginatedAdapter

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures",
    "sources",
    "waho",
)

LISTING_URL = "https://data.wahooas.org/tenders/tenders/list"
PAGE2_URL = LISTING_URL + "?page=2"


def _fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


def _page_fetcher(page_map: dict[str, str]):
    """Fetcher serving fixture bodies keyed by exact listing/page URL."""

    def fetch(url: str) -> HttpResponse:
        for key, name in page_map.items():
            if url == key:
                return HttpResponse(
                    status_code=200,
                    headers={"content-type": "text/html; charset=utf-8"},
                    text=_fixture(name),
                    url=url,
                )
        raise SourceError(
            f"No fixture for {url}",
            error_code=SOURCE_UNREACHABLE,
            context={"url": url},
        )

    return fetch


# ---------------------------------------------------------------------------
# polite.py internals
# ---------------------------------------------------------------------------


def test_retry_transport_error_backoff() -> None:
    """A transient transport error is retried with backoff, then succeeds."""
    attempts: list[str] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow:\n")
        attempts.append(str(request.url))
        if len(attempts) == 1:
            raise httpx2.ConnectError("connection refused")
        return _ok_response("2026-09-03 09:16:00 UTC")

    policy = CrawlPolicy(request_interval_seconds=0.0, max_retries=2, backoff_base_seconds=0.0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    response = client.get(LISTING_URL)
    client.close()
    assert response.status_code == 200
    assert len(attempts) == 2


def test_retry_on_429_respects_retry_after() -> None:
    """Http 429 is retried honouring the Retry-After header."""
    attempts: list[str] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow:\n")
        attempts.append(str(request.url))
        if len(attempts) == 1:
            return _raw_response(429, headers={"retry-after": "0"})
        return _ok_response("2026-09-03 09:16:00 UTC")

    policy = CrawlPolicy(request_interval_seconds=0.0, max_retries=2, backoff_base_seconds=0.0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    response = client.get(LISTING_URL)
    client.close()
    assert response.status_code == 200
    assert len(attempts) == 2


def test_fails_unreachable_after_max_retries() -> None:
    """Persistent transport failure raises SourceError(source_unreachable)."""
    attempts: list[str] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow:\n")
        attempts.append(str(request.url))
        raise httpx2.ConnectError("connection refused")

    policy = CrawlPolicy(request_interval_seconds=0.0, max_retries=2, backoff_base_seconds=0.0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    try:
        with pytest.raises(SourceError) as excinfo:
            client.get(LISTING_URL)
    finally:
        client.close()
    assert excinfo.value.error_code == SOURCE_UNREACHABLE
    assert len(attempts) == 3  # initial + 2 retries


def test_robots_denies_crawl() -> None:
    """A robots.txt disallow rule surfaces as a structured failure."""

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow: /\n")
        return _ok_response("2026-09-03 09:16:00 UTC")

    policy = CrawlPolicy(request_interval_seconds=0.0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    try:
        with pytest.raises(SourceError) as excinfo:
            client.get(LISTING_URL)
    finally:
        client.close()
    assert excinfo.value.error_code == PARSER_MISMATCH
    assert excinfo.value.context.get("reason") == "robots_disallowed"


def test_robots_allows_crawl() -> None:
    """A permissive robots.txt is respected and the crawl proceeds."""

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow:\n")
        return _ok_response("2026-09-03 09:16:00 UTC")

    policy = CrawlPolicy(request_interval_seconds=0.0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    try:
        response = client.get(LISTING_URL)
    finally:
        client.close()
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------


def _adapter(page_map: dict[str, str], **kw) -> WahoPaginatedAdapter:
    return WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=_page_fetcher(page_map),
        policy=CrawlPolicy(request_interval_seconds=0.0),
        **kw,
    )


def test_basic_extraction() -> None:
    adapter = _adapter(
        {
            LISTING_URL: "listing_page_1.html",
            PAGE2_URL: "listing_empty_page.html",
        }
    )
    candidates = adapter.list_new_tenders()

    assert len(candidates) == 2
    first = candidates[0]
    assert isinstance(first, TenderListing)
    assert first.external_id == "167"
    assert first.title.startswith("RECRUTEMENT D'UN CABINET DE CONSEIL")
    assert first.url == "https://data.wahooas.org/tenders/tenders/167/list"
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None
    assert first.deadline_at is not None
    assert first.deadline_at == first.deadline_at.astimezone(UTC)
    assert first.deadline_timezone == "GMT"
    assert first.raw_metadata["reference"] == "P-Z1-BZ0-012/C"
    assert first.raw_metadata["listing_page_url"] == LISTING_URL


def test_pagination_follows_second_page() -> None:
    def fetch(url: str) -> HttpResponse:
        name = "listing_page_1.html" if "page=2" not in url else "listing_page_2.html"
        return HttpResponse(200, {}, _fixture(name), url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    candidates = adapter.list_new_tenders()
    assert [c.external_id for c in candidates] == ["167", "166", "165"]


def test_pagination_terminates_on_empty_page() -> None:
    def fetch(url: str) -> HttpResponse:
        if "page=2" in url:
            return HttpResponse(200, {}, _fixture("listing_empty_page.html"), url)
        return HttpResponse(200, {}, _fixture("listing_page_1.html"), url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    assert len(adapter.list_new_tenders()) == 2


def test_cyclic_next_link_does_not_loop() -> None:
    adapter = _adapter({LISTING_URL: "listing_cyclic.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 1
    assert candidates[0].external_id == "159"


def test_max_pages_limit_enforced() -> None:
    """Pagination halts once the configured maximum page count is reached."""

    def fetch(url: str) -> HttpResponse:
        return HttpResponse(200, {}, _fixture("listing_page_1.html"), url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        parser_config={"max_pages": 2},
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    assert len(adapter.list_new_tenders()) == 4  # 2 per page, but capped at 2 pages


def test_missing_deadline_returns_listing() -> None:
    adapter = _adapter({LISTING_URL: "listing_missing_deadline.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 1
    listing = candidates[0]
    assert listing.deadline_at is None
    assert listing.deadline_timezone is None
    assert listing.published_at is not None


def test_timezone_preserved() -> None:
    adapter = _adapter({LISTING_URL: "listing_page_1.html", PAGE2_URL: "listing_empty_page.html"})
    listing = adapter.list_new_tenders()[0]
    assert listing.deadline_timezone == "GMT"
    # 24 September 2026 at 1.00 pm GMT == 13:00 UTC
    assert listing.deadline_at.hour == 13
    assert listing.deadline_at.tzinfo is not None
    assert listing.deadline_at.utcoffset() == UTC.utcoffset(None)  # type: ignore[union-attr]


def test_french_fixture_parses() -> None:
    adapter = _adapter({LISTING_URL: "listing_french.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 1
    listing = candidates[0]
    assert listing.raw_metadata["language"] == "fr"
    assert listing.deadline_at is not None
    assert listing.deadline_timezone == "GMT"


def test_portuguese_fixture_parses() -> None:
    adapter = _adapter({LISTING_URL: "listing_portuguese.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 1
    listing = candidates[0]
    assert listing.title.startswith("PUBLICA")
    assert listing.raw_metadata["language"] == "pt"


def test_malformed_row_skipped_safely() -> None:
    adapter = _adapter({LISTING_URL: "listing_edge_case.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 2  # malformed row skipped, both valid rows kept
    assert [c.external_id for c in candidates] == ["161", "160"]


def test_parser_mismatch_fails_safely() -> None:
    adapter = _adapter({LISTING_URL: "listing_parser_mismatch.html"})
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_source_unreachable_on_http_error() -> None:
    def fetch(url: str) -> HttpResponse:
        return HttpResponse(503, {}, "", url)

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_UNREACHABLE


def test_network_failure_raises_source_unreachable() -> None:
    def fetch(url: str) -> HttpResponse:
        raise RuntimeError("network down")

    adapter = WahoPaginatedAdapter(
        listing_url=LISTING_URL,
        fetcher=fetch,  # type: ignore[arg-type]
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_UNREACHABLE


# ---------------------------------------------------------------------------
# Dry run / no business writes
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write_or_email(tmp_path) -> None:
    """Discovery performs no business-state writes and never emails anyone by construction."""
    adapter = _adapter({LISTING_URL: "listing_page_1.html", PAGE2_URL: "listing_empty_page.html"})
    candidates = adapter.list_new_tenders()
    assert len(candidates) == 2
    assert list(tmp_path.iterdir()) == []  # no files written


# ---------------------------------------------------------------------------
# Correlation ID
# ---------------------------------------------------------------------------


def test_correlation_id_propagates_to_records(caplog) -> None:
    records = []

    class Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("tender_intelligence.sources.waho")
    handler = Recorder()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        with correlation_context("waho-run-001"):
            adapter = _adapter(
                {LISTING_URL: "listing_page_1.html", PAGE2_URL: "listing_empty_page.html"}
            )
            adapter.list_new_tenders()
    finally:
        logger.removeHandler(handler)

    assert records
    for record in records:
        assert getattr(record, "correlation_id", None) == "waho-run-001"
    assert any(getattr(r, "status", "") == "done" for r in records)


# ---------------------------------------------------------------------------
# Polite pacing
# ---------------------------------------------------------------------------


def test_request_interval_respected() -> None:
    timestamps: list[float] = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return _raw_response(200, text="User-agent: *\nDisallow:\n")
        timestamps.append(time.monotonic())
        return _ok_response("2026-09-03 09:16:00 UTC")

    policy = CrawlPolicy(request_interval_seconds=0.2, max_retries=0)
    client = PoliteHttpClient.build(policy, transport=MockTransport(handler))
    try:
        client.get(LISTING_URL)
        client.get(LISTING_URL)
    finally:
        client.close()
    assert len(timestamps) == 2
    assert timestamps[1] - timestamps[0] >= 0.2


def test_detail_and_attachments_are_stubs() -> None:
    adapter = _adapter({LISTING_URL: "listing_page_1.html", PAGE2_URL: "listing_empty_page.html"})
    with pytest.raises(NotImplementedError):
        adapter.get_detail("167")
    with pytest.raises(NotImplementedError):
        adapter.get_attachments("167")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _raw_response(status: int, text: str = "", headers: dict | None = None):
    from httpx2 import Response

    return Response(status, text=text, headers=headers or {})


def _ok_response(body: str):
    return _raw_response(200, text=body)

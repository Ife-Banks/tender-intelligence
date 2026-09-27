"""Unit tests for the filtered-HTML discovery strategy (v1.1 §5.1, docs/05).

Offline only: a synthetic site served by an in-process fetcher, no network and no live
portal (prompt 04 §6, §17). The point of these tests is that the strategy is *generic* —
the facets, the row selectors and the URL are configuration, and none of them names a site.
"""

from __future__ import annotations

import pytest

from tender_intelligence.core.errors import PARSER_MISMATCH
from tender_intelligence.interfaces.source import SourceError
from tender_intelligence.sources.filtered_html import FACET_KEY, FilteredHtmlAdapter
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse

LISTING_URL = "https://example.invalid/tenders"


def _page(ids: list[str], next_href: str | None = None) -> str:
    """A listing page for a synthetic site, matching the adapter's default selectors."""
    rows = "".join(
        '<div class="col-md-6"><div class="card"><div class="card-header"><h5>'
        f'<a href="/tenders/{tender_id}">Tender {tender_id}</a>'
        "</h5></div></div></div>"
        for tender_id in ids
    )
    nxt = f'<a rel="next" href="{next_href}">next</a>' if next_href else ""
    return f"<html><body><div>{rows}</div>{nxt}</body></html>"


def _adapter(fetcher, **config):
    """A filtered adapter over the synthetic site.

    ``detail_href_pattern`` is supplied here for the same reason a real source supplies it:
    how a detail link encodes the tender's identity is a property of the site, and the
    adapter must not assume the shape another one happens to use.
    """
    return FilteredHtmlAdapter(
        listing_url=LISTING_URL,
        fetcher=fetcher,
        parser_config={"detail_href_pattern": r"/tenders/(?P<id>[^/]+)$", **config},
        policy=CrawlPolicy(request_interval_seconds=0.0),
    )


def test_query_params_crawl_one_filtered_listing() -> None:
    """A single ``query_params`` filter is applied to the listing request."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        return HttpResponse(200, {}, _page(["a"]), url)

    listings = _adapter(fetch, query_params={"status": "open"}).list_new_tenders()
    assert requested == [f"{LISTING_URL}?status=open"]
    assert [c.external_id for c in listings] == ["a"]


def test_facets_are_walked_one_at_a_time() -> None:
    """Each configured facet is a full crawl; every facet URL is requested exactly once."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        ids = ["a"] if "category=works" not in url else ["a", "b"]
        return HttpResponse(200, {}, _page(ids), url)

    adapter = _adapter(
        fetch, facets=[{"status": "open"}, {"status": "open", "category": "works"}]
    )
    listings = adapter.list_new_tenders()
    assert requested == [
        f"{LISTING_URL}?status=open",
        f"{LISTING_URL}?status=open&category=works",
    ]
    # The tender listed under both facets is returned once.
    assert [c.external_id for c in listings] == ["a", "b"]


def test_facet_params_override_query_params() -> None:
    """A facet value wins over the source-wide ``query_params`` value for the same key."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        return HttpResponse(200, {}, _page(["a"]), url)

    _adapter(
        fetch, query_params={"status": "all", "lang": "en"}, facets=[{"status": "open"}]
    ).list_new_tenders()
    assert requested == [f"{LISTING_URL}?status=open&lang=en"]


def test_listing_records_the_facet_it_came_from() -> None:
    """Each listing names the facet that produced it, so a run report can explain itself."""

    def fetch(url: str) -> HttpResponse:
        ids = ["a"] if "status=open" in url else ["b"]
        return HttpResponse(200, {}, _page(ids), url)

    adapter = _adapter(fetch, facets=[{"status": "open"}, {"status": "closed"}])
    listings = adapter.list_new_tenders()
    assert {c.external_id: c.raw_metadata[FACET_KEY] for c in listings} == {
        "a": {"status": "open"},
        "b": {"status": "closed"},
    }
    assert all(c.raw_metadata["source_strategy"] == "html_listing" for c in listings)


def test_no_facets_configured_crawls_the_bare_listing_once() -> None:
    """Selecting the strategy with no filters configured is safe, not a failure."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        return HttpResponse(200, {}, _page(["a"]), url)

    assert [c.external_id for c in _adapter(fetch).list_new_tenders()] == ["a"]
    assert requested == [LISTING_URL]


def test_empty_facet_is_not_a_parser_failure() -> None:
    """A filter combination matching nothing is normal, and must not fail the crawl."""

    def fetch(url: str) -> HttpResponse:
        ids = [] if "category=none" in url else ["a"]
        return HttpResponse(200, {}, _page(ids), url)

    adapter = _adapter(fetch, facets=[{"category": "none"}, {"category": "works"}])
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a"]


def test_unfiltered_first_page_still_fails_on_structural_drift() -> None:
    """An empty *unfiltered* page means the selectors broke, and is reported as such."""

    def fetch(url: str) -> HttpResponse:
        return HttpResponse(200, {}, _page([]), url)

    with pytest.raises(SourceError) as excinfo:
        _adapter(fetch).list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_facets_preserve_pagination_and_deduplication() -> None:
    """Facets inherit the paginated strategy's loop guard, cap and per-crawl dedupe."""
    requested: list[str] = []

    def fetch(url: str) -> HttpResponse:
        requested.append(url)
        if url.endswith("page=2"):
            return HttpResponse(200, {}, _page(["a", "b"]), url)  # page 2 repeats nothing
        return HttpResponse(
            200, {}, _page(["a", "b"], next_href=f"{LISTING_URL}?status=open&page=2"), url
        )

    adapter = _adapter(fetch, facets=[{"status": "open"}])
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a", "b"]
    assert len(requested) == 2

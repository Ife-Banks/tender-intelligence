"""Unit tests for the search-form discovery strategy (v1.1 §5.1, docs/05).

The form flow is exercised through the injected :class:`FormDriver` seam and through the
default HTTP driver with a fake in-process transport. Nothing here opens a browser, and
nothing here reaches the network (prompt 04 §6, §17).
"""

from __future__ import annotations

import urllib.parse

import pytest

from tender_intelligence.core.errors import PARSER_MISMATCH, SOURCE_NOT_RUNNABLE
from tender_intelligence.interfaces.source import SourceError
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse
from tender_intelligence.sources.search_form import (
    FormSpec,
    HttpFormDriver,
    SearchFormAdapter,
    UnavailableFormDriver,
    _form_values,
)

FORM_URL = "https://example.invalid/tenders/search"
RESULT_URL = "https://example.invalid/tenders/search"


def _results(ids: list[str], next_href: str | None = None) -> str:
    """A result page in the shape the default listing selectors expect."""
    rows = "".join(
        '<div class="col-md-6"><div class="card"><div class="card-header"><h5>'
        f'<a href="/tenders/{tender_id}">Tender {tender_id}</a>'
        "</h5></div></div></div>"
        for tender_id in ids
    )
    nxt = f'<a rel="next" href="{next_href}">next</a>' if next_href else ""
    return f"<html><body><div>{rows}</div>{nxt}</body></html>"


def _search_page(
    *,
    action: str = "/tenders/search",
    method: str = "GET",
    extra: str = "",
) -> str:
    """A search page whose form carries a hidden token and a select, as real ones do."""
    return f"""<html><body>
      <form id="tender-search" action="{action}" method="{method}">
        <input type="hidden" name="authenticity_token" value="tok-123">
        <input type="text" name="keyword" value="">
        <select name="status">
          <option value="all" selected>All</option>
          <option value="open">Open</option>
        </select>
        <input type="checkbox" name="include_closed" value="1">
        <input type="text" name="ignored" value="x" disabled>
        <button type="submit" name="go" value="1">Search</button>
        {extra}
      </form>
    </body></html>"""


def _adapter(driver=None, *, requester=None, **config):
    return SearchFormAdapter(
        listing_url=FORM_URL,
        fetcher=None,
        parser_config={"detail_href_pattern": r"/tenders/(?P<id>[^/]+)$", **config},
        policy=CrawlPolicy(request_interval_seconds=0.0),
        form_driver=driver,
        requester=requester,
    )


# ---------------------------------------------------------------------------
# The execution boundary
# ---------------------------------------------------------------------------


def test_browser_requiring_source_fails_loudly_instead_of_finding_nothing() -> None:
    """A form that needs a browser must not be submitted as a plain request.

    Submitting it anyway returns a page with no results, which is indistinguishable from a
    portal with no tenders — a silent failure this project treats as a bug.
    """
    adapter = _adapter(form={"requires_browser": True})
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE


def test_an_injected_requester_does_not_turn_a_browser_form_into_a_plain_one() -> None:
    """A Requester is not a browser, so it must not quietly satisfy ``requires_browser``."""

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        return HttpResponse(200, {}, "", url)

    adapter = _adapter(requester=requester, form={"requires_browser": True})
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE


def test_a_browser_driver_can_be_supplied_instead() -> None:
    """The seam is what makes the strategy extendable: a driver, not a code change."""
    calls: list[FormSpec] = []

    class RecordingDriver:
        def run(self, spec: FormSpec) -> HttpResponse:
            calls.append(spec)
            return HttpResponse(200, {}, _results(["a"]), RESULT_URL)

    adapter = _adapter(RecordingDriver(), form={"requires_browser": True})
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a"]
    assert calls and calls[0].requires_browser is True


def test_unavailable_driver_raises_a_structured_error() -> None:
    driver = UnavailableFormDriver("no driver installed")
    with pytest.raises(SourceError) as excinfo:
        driver.run(FormSpec(form_url=FORM_URL))
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE
    assert excinfo.value.context["reason"] == "form_driver_unavailable"


# ---------------------------------------------------------------------------
# The default HTTP driver
# ---------------------------------------------------------------------------


def test_form_values_mirror_what_a_browser_submits() -> None:
    """Hidden tokens, selected options and checked boxes go; disabled controls do not."""
    from bs4 import BeautifulSoup

    form = BeautifulSoup(_search_page(), "html.parser").select_one("form")
    values = _form_values(form)
    assert values == {
        "authenticity_token": "tok-123",
        "keyword": "",
        "status": "all",
    }
    assert "include_closed" not in values  # unchecked
    assert "ignored" not in values  # disabled
    assert "go" not in values  # submit button


def test_get_submission_merges_form_values_with_configured_fields() -> None:
    """The configured field overrides the page's own value; the token still travels."""
    requested: list[str] = []

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        requested.append(url)
        if len(requested) == 1:
            return HttpResponse(200, {}, _search_page(), FORM_URL)
        return HttpResponse(200, {}, _results(["a"]), RESULT_URL)

    driver = HttpFormDriver(requester=requester)
    spec = FormSpec(
        form_url=FORM_URL,
        form_selector="form#tender-search",
        method="GET",
        fields={"status": "open", "keyword": "road"},
    )
    assert driver.run(spec).status_code == 200
    # A GET form is submitted exactly as a browser does: the fields ride in the query string.
    submitted = urllib.parse.parse_qs(urllib.parse.urlsplit(requested[1]).query)
    assert submitted["status"] == ["open"]
    assert submitted["keyword"] == ["road"]
    assert submitted["authenticity_token"] == ["tok-123"]


def test_post_submission_sends_the_form_as_a_body() -> None:
    """A form declaring POST is submitted as a body, not as a query string."""
    requested: list[tuple[str, dict | None, dict | None]] = []

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        requested.append((method, kwargs.get("data"), kwargs.get("params")))
        if len(requested) == 1:
            return HttpResponse(
                200, {}, _search_page(action="/tenders/find", method="post"), FORM_URL
            )
        return HttpResponse(200, {}, _results(["a"]), "https://example.invalid/tenders/find")

    driver = HttpFormDriver(requester=requester)
    driver.run(FormSpec(form_url=FORM_URL, form_selector="#tender-search", method="POST"))
    method, data, params = requested[1]
    assert method == "POST"
    assert data is not None and data["status"] == "all"
    assert not params


def test_missing_form_is_reported_as_a_parser_mismatch() -> None:
    """A search page whose form has moved is structural drift, not an empty result set."""

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        return HttpResponse(200, {}, "<html><body>no form here</body></html>", FORM_URL)

    driver = HttpFormDriver(requester=requester)
    with pytest.raises(SourceError) as excinfo:
        driver.run(FormSpec(form_url=FORM_URL, form_selector="form#tender-search"))
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_unreachable_search_page_fails_the_run() -> None:
    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        return HttpResponse(500, {}, "", FORM_URL)

    driver = HttpFormDriver(requester=requester)
    with pytest.raises(SourceError) as excinfo:
        driver.run(FormSpec(form_url=FORM_URL))
    assert excinfo.value.context["status_code"] == 500


# ---------------------------------------------------------------------------
# End to end through the adapter
# ---------------------------------------------------------------------------


def test_result_page_is_paginated_like_any_other_listing() -> None:
    """After the submit, the result page is an ordinary HTML list: paged and deduplicated."""
    requested: list[str] = []

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        if len(requested) == 0:
            requested.append("form")
            return HttpResponse(200, {}, _search_page(), FORM_URL)
        requested.append(url)
        if "page=2" in url:
            return HttpResponse(200, {}, _results(["a", "b"]), url)
        return HttpResponse(
            200, {}, _results(["a"], next_href=f"{RESULT_URL}?page=2"), url
        )

    adapter = _adapter(
        requester=requester,
        form={"form_selector": "#tender-search", "fields": {"status": "open"}},
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a", "b"]
    assert requested[0] == "form"


def test_the_form_runs_once_per_crawl() -> None:
    """A paginated crawl must not re-submit the search form for every page."""
    calls: list[str] = []

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        if url == FORM_URL and not calls:
            calls.append(url)
            return HttpResponse(200, {}, _search_page(), FORM_URL)
        return HttpResponse(
            200,
            {},
            _results(["a", "b"], next_href=f"{RESULT_URL}?page=2"),
            url,
        )

    adapter = _adapter(requester=requester, form={"form_selector": "#tender-search"})
    adapter.list_new_tenders()
    assert calls == [FORM_URL]


def test_the_form_still_runs_when_the_source_pins_query_params() -> None:
    """``query_params`` alters the entry URL, and the form must not be skipped because of it."""
    calls: list[str] = []

    def requester(method: str, url: str, **kwargs) -> HttpResponse:
        calls.append(url)
        if len(calls) == 1:
            return HttpResponse(200, {}, _search_page(), FORM_URL)
        return HttpResponse(200, {}, _results(["a"]), url)

    adapter = _adapter(
        requester=requester, query_params={"lang": "en"}, form={"form_selector": "#tender-search"}
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a"]
    assert len(calls) == 2  # the form page and the submit; neither was a bare listing GET

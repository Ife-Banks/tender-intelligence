"""Unit tests for the JSON API discovery strategy (v1.1 §5.1, docs/05).

Every response is synthetic and served from memory: no network, no live API, no credential
that leaves the process (prompt 04 §6, §17; PROJECT_RULES #12).
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime

import pytest

from tender_intelligence.core.errors import (
    PARSER_MISMATCH,
    SOURCE_NOT_RUNNABLE,
    SOURCE_UNREACHABLE,
)
from tender_intelligence.interfaces.source import SourceError
from tender_intelligence.sources.json_api import EnvAuthResolver, JsonApiAdapter
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse

ENDPOINT = "https://api.example.invalid/v1/tenders"
DETAIL_URL = "https://api.example.invalid/v1/tenders/42"

SECRET = "s3cr3t-token-value"


def _item(tender_id: str, **overrides) -> dict:
    item = {
        "id": tender_id,
        "title": f"Tender {tender_id}",
        "url": f"/v1/tenders/{tender_id}",
        "published_at": "2026-09-01T08:00:00Z",
    }
    item.update(overrides)
    return item


def _envelope(*items: dict, total: int | None = None) -> dict:
    payload: dict = {"data": {"results": list(items)}}
    if total is not None:
        payload["data"]["total"] = total
    return payload


def _adapter(responses, *, requester=None, **config) -> JsonApiAdapter:
    """An adapter over a requester that serves *responses* by URL, recording every request."""
    calls: list[tuple[str, str, dict]] = []

    def default_requester(method: str, url: str, **kwargs) -> HttpResponse:
        calls.append((method, url, kwargs))
        body = responses(url) if callable(responses) else responses
        if isinstance(body, HttpResponse):
            return body
        return HttpResponse(200, {"content-type": "application/json"}, json.dumps(body), url)

    used = requester or default_requester
    adapter = JsonApiAdapter(
        ENDPOINT,
        parser_config=config,
        policy=CrawlPolicy(request_interval_seconds=0.0),
        requester=used,
        auth_resolver=lambda ref: SECRET,
    )
    adapter.calls = calls  # type: ignore[attr-defined]
    return adapter


def _queried(calls) -> list[str]:
    return [url for _method, url, _kwargs in calls]


def _query_of(url: str) -> dict[str, str]:
    return {
        key: values[0]
        for key, values in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()
    }


# ---------------------------------------------------------------------------
# Mapping the response
# ---------------------------------------------------------------------------


def test_items_and_fields_are_read_through_configured_paths() -> None:
    """Dotted paths say where the data is, so the schema is configuration rather than code."""
    payload = {
        "payload": {
            "tenders": [
                {
                    "attributes": {
                        "reference": "REF-2026-42",
                        "heading": "Solar lighting",
                        "permalink": "/tenders/42",
                        "issued": "2026-09-01T08:00:00Z",
                        "closes": "2026-11-30T12:00:00+01:00",
                    }
                }
            ]
        }
    }
    adapter = _adapter(
        payload,
        items_path="payload.tenders",
        id_path="attributes.reference",
        title_path="attributes.heading",
        url_path="attributes.permalink",
        published_path="attributes.issued",
        deadline_path="attributes.closes",
        pagination_mode="none",
    )
    listings = adapter.list_new_tenders()
    assert len(listings) == 1
    listing = listings[0]
    assert listing.external_id == "REF-2026-42"
    assert listing.title == "Solar lighting"
    assert listing.url == "https://api.example.invalid/tenders/42"
    assert listing.published_at == datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    assert listing.deadline_at == datetime(2026, 11, 30, 11, 0, tzinfo=UTC)
    assert listing.deadline_timezone == "+01:00"
    assert listing.raw_metadata["source_strategy"] == "json_api"
    assert listing.raw_metadata["identity_source"] == "configured_id_path"


def test_field_paths_may_be_a_list_of_candidates() -> None:
    """One adapter serves two APIs that spell the same field differently."""
    payload = _envelope({"reference": "R-1", "name": "Road works"})
    adapter = _adapter(
        payload,
        items_path="data.results",
        id_path="reference",
        title_path=["heading", "name"],
        pagination_mode="none",
    )
    assert adapter.list_new_tenders()[0].title == "Road works"


def test_identity_falls_back_to_the_url_when_the_api_publishes_no_id() -> None:
    payload = _envelope({"title": "No id here", "url": "/tenders/xyz"})
    adapter = _adapter(payload, items_path="data.results", pagination_mode="none")
    listing = adapter.list_new_tenders()[0]
    assert listing.external_id == "api.example.invalid/tenders/xyz"
    assert listing.raw_metadata["identity_source"] == "derived_from_url"


def test_id_pattern_maps_an_api_id_onto_a_tender_reference() -> None:
    payload = _envelope({"id": "TENDER/2026/0042", "title": "Milling"})
    adapter = _adapter(
        payload,
        items_path="data.results",
        id_pattern=r"TENDER/\d{4}/(?P<id>\d{4})",
        pagination_mode="none",
    )
    listing = adapter.list_new_tenders()[0]
    assert listing.external_id == "0042"
    assert listing.raw_metadata["identity_source"].startswith("id_pattern:")


def test_an_item_with_no_title_or_identity_is_skipped_not_fatal() -> None:
    payload = _envelope({"id": "a", "title": "Good one"}, {"note": "no title, no id, no url"})
    adapter = _adapter(payload, items_path="data.results", pagination_mode="none")
    assert [c.external_id for c in adapter.list_new_tenders()] == ["a"]


# ---------------------------------------------------------------------------
# Deadlines: no timezone evidence, no deadline
# ---------------------------------------------------------------------------


def test_a_naive_deadline_is_reported_absent_with_its_raw_value() -> None:
    """An API that publishes a bare date has published no timezone, and none is invented."""
    payload = _envelope(_item("1", closing_date="2026-11-30"))
    adapter = _adapter(payload, items_path="data.results", pagination_mode="none")
    listing = adapter.list_new_tenders()[0]
    assert listing.deadline_at is None
    assert listing.deadline_timezone is None
    assert listing.raw_metadata["deadline_raw"] == "2026-11-30"


def test_an_epoch_deadline_is_accepted_because_it_names_an_instant() -> None:
    payload = _envelope(_item("1", deadline=1793390400))
    adapter = _adapter(payload, items_path="data.results", pagination_mode="none")
    listing = adapter.list_new_tenders()[0]
    assert listing.deadline_at == datetime.fromtimestamp(1793390400, tz=UTC)
    assert listing.deadline_timezone == "UTC"


def test_an_unreadable_deadline_is_kept_raw_and_left_unparsed() -> None:
    payload = _envelope(_item("1", deadline="see the notice for the closing date"))
    adapter = _adapter(payload, items_path="data.results", pagination_mode="none")
    listing = adapter.list_new_tenders()[0]
    assert listing.deadline_at is None
    assert "closing date" in listing.raw_metadata["deadline_raw"]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_page_mode_walks_until_a_short_page() -> None:
    """The API said how many items a page holds, so a short page ends the walk."""
    pages = {
        None: _envelope(_item("1"), _item("2"), total=3),
        "2": _envelope(_item("3"), total=3),
    }
    adapter = _adapter(
        lambda url: pages[_query_of(url).get("page")],
        items_path="data.results",
        pagination_mode="page",
        page_param="page",
        params={"page_size": 2},
    )
    listings = adapter.list_new_tenders()
    assert [c.external_id for c in listings] == ["1", "2", "3"]
    assert _queried(adapter.calls) == [ENDPOINT + "?page_size=2", ENDPOINT + "?page_size=2&page=2"]


def test_offset_mode_advances_by_what_each_page_returned() -> None:
    """With no declared page size, the walk continues from where the API left off."""
    pages = {
        "0": _envelope(_item("1"), _item("2")),
        "2": _envelope(_item("3")),
        "3": _envelope(),
    }
    adapter = _adapter(
        lambda url: pages[_query_of(url).get("offset", "0")],
        items_path="data.results",
        pagination_mode="offset",
        max_pages=5,
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["1", "2", "3"]
    assert [_query_of(url).get("offset", "0") for url in _queried(adapter.calls)] == ["0", "2", "3"]


def test_a_cursor_value_becomes_the_next_cursor_parameter() -> None:
    def responses(url: str) -> dict:
        query = _query_of(url)
        if "after" not in query:
            return {"data": {"items": [_item("1")], "next": "abc123"}}
        return {"data": {"items": [_item("2")], "next": None}}

    adapter = _adapter(
        responses,
        items_path="data.items",
        pagination_mode="cursor",
        cursor_param="after",
        next_path="data.next",
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["1", "2"]
    assert _query_of(_queried(adapter.calls)[1])["after"] == "abc123"


def test_a_next_link_in_the_response_is_followed_as_a_url() -> None:
    def responses(url: str) -> dict:
        if url == ENDPOINT:
            return {"data": {"items": [_item("1")]}, "links": {"next": "/v1/tenders?page=2"}}
        return {"data": {"items": [_item("2")]}}

    adapter = _adapter(
        responses, items_path="data.items", next_path="links.next", pagination_mode="cursor"
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["1", "2"]
    assert _queried(adapter.calls)[1] == ENDPOINT + "?page=2"


def test_a_declared_total_stops_the_crawl_before_another_request() -> None:
    adapter = _adapter(
        _envelope(_item("1"), total=1),
        items_path="data.results",
        pagination_mode="page",
        total_path="data.total",
        params={"page_size": 2},
    )
    assert len(adapter.list_new_tenders()) == 1
    assert len(adapter.calls) == 1


def test_repeated_items_end_the_crawl() -> None:
    """An API that keeps replaying the same page must not be read to the cap."""

    def responses(url: str) -> dict:
        return _envelope(_item("1"))

    adapter = _adapter(
        responses, items_path="data.results", pagination_mode="page", max_pages=10
    )
    assert len(adapter.list_new_tenders()) == 1
    assert len(adapter.calls) == 2  # the replay, then the stop


def test_the_page_cap_is_enforced() -> None:
    def responses(url: str) -> dict:
        page = int(_query_of(url).get("page", "1"))
        return _envelope(_item(str(page)))

    adapter = _adapter(
        responses, items_path="data.results", pagination_mode="page", max_pages=3
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["1", "2", "3"]
    assert len(adapter.calls) == 3


# ---------------------------------------------------------------------------
# Failures that must not look like "no tenders"
# ---------------------------------------------------------------------------


def test_a_wrong_items_path_is_reported() -> None:
    adapter = _adapter(
        _envelope(_item("1")), items_path="data.does_not_exist", pagination_mode="none"
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_an_object_response_without_an_items_path_is_reported() -> None:
    """The adapter refuses to guess where the list is instead of finding nothing."""
    adapter = _adapter({"data": {"count": 0}}, pagination_mode="none")
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH
    assert "items_path" in str(excinfo.value)


def test_an_empty_first_page_is_reported_unless_allowed() -> None:
    empty = _envelope()
    with pytest.raises(SourceError) as excinfo:
        _adapter(empty, items_path="data.results", pagination_mode="none").list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH
    allowed = _adapter(
        empty, items_path="data.results", allow_empty_listing=True, pagination_mode="none"
    )
    assert allowed.list_new_tenders() == []


def test_a_response_that_is_not_json_is_reported() -> None:
    html = HttpResponse(200, {"content-type": "text/html"}, "<html>login page</html>", ENDPOINT)
    adapter = _adapter(html, items_path="data.results", pagination_mode="none")
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == PARSER_MISMATCH


def test_an_http_error_fails_the_run() -> None:
    adapter = _adapter(HttpResponse(503, {}, "", ENDPOINT), pagination_mode="none")
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_UNREACHABLE
    assert excinfo.value.context["status_code"] == 503


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_a_resolved_credential_is_sent_but_never_retained() -> None:
    """The secret is used for the request and dropped again: not in metadata, not on state."""
    adapter = _adapter(
        _envelope(_item("1")),
        items_path="data.results",
        pagination_mode="none",
        auth={"type": "bearer", "ref": "TENDER_API_TOKEN"},
    )
    listing = adapter.list_new_tenders()[0]
    sent = adapter.calls[0][2]["headers"]
    assert sent["Authorization"] == f"Bearer {SECRET}"
    assert adapter._auth_headers == {}  # noqa: SLF001 - asserting the secret is not retained
    assert SECRET not in json.dumps(listing.raw_metadata)


def test_a_query_credential_is_sent_as_a_parameter() -> None:
    adapter = _adapter(
        _envelope(_item("1")),
        items_path="data.results",
        pagination_mode="none",
        auth={"type": "query", "name": "api_key", "ref": "TENDER_API_TOKEN"},
    )
    adapter.list_new_tenders()
    assert adapter.calls[0][2]["params"] == {"api_key": SECRET}


def test_an_unresolvable_reference_fails_the_run_before_any_request() -> None:
    """An unauthenticated request would return an empty list, which reads as 'no tenders'."""

    def failing(ref: str) -> str:
        raise KeyError(ref)

    adapter = JsonApiAdapter(
        ENDPOINT,
        parser_config={"items_path": "data.results", "auth": {"type": "bearer", "ref": "MISSING"}},
        policy=CrawlPolicy(request_interval_seconds=0.0),
        requester=lambda method, url, **kwargs: pytest.fail("no request should be made"),
        auth_resolver=failing,
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE
    assert excinfo.value.context["reason"] == "auth_secret_unavailable"


def test_auth_declared_without_a_reference_fails_the_run() -> None:
    adapter = _adapter(
        _envelope(_item("1")),
        items_path="data.results",
        pagination_mode="none",
        auth={"type": "header", "name": "X-Key"},
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE


def test_an_unsupported_auth_type_fails_the_run() -> None:
    adapter = _adapter(
        _envelope(_item("1")),
        items_path="data.results",
        pagination_mode="none",
        auth={"type": "oauth2", "ref": "TENDER_API_TOKEN"},
    )
    with pytest.raises(SourceError) as excinfo:
        adapter.list_new_tenders()
    assert excinfo.value.error_code == SOURCE_NOT_RUNNABLE


def test_the_default_resolver_reads_the_process_environment() -> None:
    resolver = EnvAuthResolver({"TENDER_API_TOKEN": "from-env"})
    assert resolver("TENDER_API_TOKEN") == "from-env"
    with pytest.raises(KeyError):
        resolver("NOT_SET")


# ---------------------------------------------------------------------------
# Methods, headers and static parameters
# ---------------------------------------------------------------------------


def test_a_post_source_is_queried_with_a_body() -> None:
    """A JSON API that needs a query is not forced into a GET the way HTML listing is."""
    adapter = _adapter(
        {"data": {"results": [_item("1")]}},
        items_path="data.results",
        method="POST",
        body={"filter": "open"},
        headers={"X-Client": "tender-intelligence"},
        pagination_mode="none",
    )
    adapter.list_new_tenders()
    method, _url, kwargs = adapter.calls[0]
    assert method == "POST"
    assert kwargs["json"] == {"filter": "open"}
    assert kwargs["headers"]["X-Client"] == "tender-intelligence"
    assert kwargs["headers"]["Accept"] == "application/json"


def test_an_injected_get_only_fetcher_is_honoured() -> None:
    """The same injection seam the HTML strategies take also works for JSON."""
    seen: list[str] = []

    def fetcher(url: str) -> HttpResponse:
        seen.append(url)
        return HttpResponse(
            200, {"content-type": "application/json"}, json.dumps(_envelope(_item("1"))), url
        )

    adapter = JsonApiAdapter(
        ENDPOINT,
        parser_config={"items_path": "data.results", "pagination_mode": "none"},
        policy=CrawlPolicy(request_interval_seconds=0.0),
        fetcher=fetcher,
    )
    assert [c.external_id for c in adapter.list_new_tenders()] == ["1"]
    assert seen == [ENDPOINT]


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


DETAIL_JSON = {
    "id": 42,
    "title": "Solar lighting, lot 2",
    "reference": "REF-2026-42",
    "reference_no": "NOTICE-77",
    "entity": "District Authority",
    "scope": "Supply and installation of street lighting.",
    "requirements": ["3-year warranty", "Local installation team"],
    "published_at": "2026-09-01T08:00:00Z",
    "closing": "2026-11-30T12:00:00+01:00",
    "files": [
        {
            "link": "/files/42/spec.pdf",
            "name": "Specification.pdf",
            "mime": "application/pdf",
            "size": 1048576,
        },
        {"link": "/files/42/annexes.zip"},
    ],
}


def _detail_adapter(**config) -> JsonApiAdapter:
    def responses(url: str) -> dict:
        return _envelope(_item("42")) if url == ENDPOINT else DETAIL_JSON

    adapter = _adapter(
        responses,
        items_path="data.results",
        detail_response_kind="json",
        detail_endpoint="/v1/tenders/{id}",
        **config,
    )
    adapter.list_new_tenders()
    return adapter


def test_a_json_detail_endpoint_is_mapped_onto_the_shared_detail_record() -> None:
    """Every detail field comes from the configured ``detail_paths`` block."""
    adapter = _detail_adapter(
        detail_paths={
            "title_path": "title",
            "published_path": "published_at",
            "deadline_path": "closing",
            "entity_path": "entity",
            "reference_path": ["reference", "reference_no"],
            "scope_path": "scope",
            "requirements_path": "requirements",
            "attachments_path": "files",
        }
    )
    detail = adapter.get_detail("42")
    assert adapter.calls[-1][1] == DETAIL_URL
    assert detail.listing.title == "Solar lighting, lot 2"
    assert detail.listing.deadline_at == datetime(2026, 11, 30, 11, 0, tzinfo=UTC)
    assert detail.listing.deadline_timezone == "+01:00"
    assert detail.reference_numbers == ["REF-2026-42", "NOTICE-77"]
    assert detail.procuring_body == "District Authority"
    assert detail.scope == "Supply and installation of street lighting."
    assert detail.requirements_hints == ["3-year warranty", "Local installation team"]


def test_json_attachments_carry_the_facts_the_api_published() -> None:
    adapter = _detail_adapter(
        detail_paths={
            "attachments_path": "files",
            "attachment_link_field": "link",
            "attachment_name_field": "name",
            "attachment_type_field": "mime",
            "attachment_size_field": "size",
        }
    )
    attachments = adapter.get_attachments("42")
    assert [a.source_url for a in attachments] == [
        "https://api.example.invalid/files/42/spec.pdf",
        "https://api.example.invalid/files/42/annexes.zip",
    ]
    assert attachments[0].filename == "Specification.pdf"
    assert attachments[0].mime_type == "application/pdf"
    assert attachments[0].advertised_size_bytes == 1048576
    # A bundle stays one attachment, and the size the API did not publish is left unset.
    assert attachments[1].is_zip is True
    assert attachments[1].advertised_size_bytes is None


def test_a_json_list_of_html_pages_uses_the_inherited_html_detail_parsing() -> None:
    """The common shape: JSON discovery, ordinary web pages for detail and documents."""
    detail_html = """<html><body><div class="col-lg-12"><div class="card">
        <div class="card-header"><h1>Road maintenance</h1></div>
        <div class="card-body"><div class="trix-content">
          <div class="elementToProof"><strong>REFERENCE :</strong> RM-2026-9</div>
          <div class="elementToProof"><strong>Client :</strong> Roads Directorate</div>
          <div class="elementToProof"><strong>Deadline for submission of applications:</strong>
            24 September 2026 at 1.00 pm GMT.</div>
          <div class="attachments"><div class="attachment">
            <a href="/uploads/9/tor.pdf">TOR (PDF)</a></div></div>
        </div></div></div></div></body></html>"""

    def responses(url: str) -> HttpResponse:
        if url == ENDPOINT:
            return HttpResponse(
                200,
                {"content-type": "application/json"},
                json.dumps(_envelope(_item("9", title="Road maintenance"))),
                url,
            )
        return HttpResponse(200, {"content-type": "text/html"}, detail_html, url)

    adapter = _adapter(
        responses, items_path="data.results", pagination_mode="none", id_path="id"
    )
    listing = adapter.list_new_tenders()[0]
    detail = adapter.get_detail(listing.external_id)
    assert _queried(adapter.calls) == [ENDPOINT, "https://api.example.invalid/v1/tenders/9"]
    assert detail.reference_numbers == ["RM-2026-9"]
    assert detail.procuring_body == "Roads Directorate"
    assert detail.listing.deadline_timezone == "GMT"
    assert [a.source_url for a in adapter.get_attachments(listing.external_id)] == [
        "https://api.example.invalid/uploads/9/tor.pdf"
    ]


def test_the_detail_endpoint_is_used_when_the_list_publishes_no_url() -> None:
    def responses(url: str) -> dict:
        if url == ENDPOINT:
            return _envelope({"id": 42, "title": "No url in the list"})
        return {"id": 42, "title": "No url in the list"}

    adapter = _adapter(
        responses,
        items_path="data.results",
        pagination_mode="none",
        id_path="id",
        detail_response_kind="json",
        detail_endpoint="/v1/tenders/{id}",
        detail_paths={"title_path": "title"},
    )
    listing = adapter.list_new_tenders()[0]
    assert listing.url == DETAIL_URL
    assert adapter.get_detail(listing.external_id).listing.title == "No url in the list"

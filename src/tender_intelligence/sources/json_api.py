""":mod:`tender_intelligence.sources.json_api` — JSON API source strategy.

docs/05 §5.1 names "JSON API" as a source type. A JSON source is the least ambiguous thing
this project consumes: the response *is* the data, so there is no scraping heuristic to get
wrong. The ambiguity moves instead into the response's shape, and every part of that shape is
operator configuration rather than code — which envelope holds the list, which field is the
tender's identity, which one is its title, how the next page is requested. Nothing in this
file names a portal, an endpoint or a field of any particular API (PROJECT_RULES #10).

**Field paths.** ``items_path``, ``id_path``, ``title_path`` and friends are dotted paths
understood by :func:`~tender_intelligence.sources.normalize.dig`, so ``data.results``,
``links[0].href`` and ``attributes.title`` all work. Each may be a list of candidates, tried
in order, which is how one adapter serves two APIs that spell the same field differently. The
fallbacks used when a path is not configured are ordinary field names (``id``, ``title``,
``url``, ...), and whichever rule produced a value is recorded in the listing's provenance
rather than left implicit.

**Pagination.** Real APIs page. Three modes are supported, all driven by configuration:
``page`` (an incrementing page number), ``offset`` (a moving offset) and ``cursor`` (whatever
the response says comes next, either a whole URL or a value for ``cursor_param``). The same
bounds as the HTML strategies apply — a page cap, a visited-URL set, and a stop as soon as a
page contributes no new item. Where the source declares how many items a page holds (as
``page_size``/``limit``/``per_page``) a short page ends the crawl, as does reaching a declared
``total_path``, so a well-behaved API is read to its end and not one request past it.

**Not every API is a GET.** ``method`` and ``body`` are configuration, so a source whose list
is only reachable by POSTing a query is served by the same adapter — still through the one
polite transport, still with the same robots, pacing, timeout and retry behaviour.

**Credentials never live in configuration.** ``parser_config`` may say *where* a credential
goes (``auth.type``, ``auth.name``) and *which reference* to look up (``auth.ref``), never the
credential itself. The reference is resolved through an injected :class:`AuthResolver`; the
default reads the process environment. A reference that cannot be resolved fails the run with
``source_not_runnable`` rather than issuing an unauthenticated request that would be
indistinguishable from a source with no tenders.

**Detail.** A JSON source usually has a JSON detail endpoint, and one is supported
(``detail_endpoint`` plus ``detail_paths``). A JSON list that links out to ordinary HTML pages
is just as common, so unless ``detail_response_kind`` is ``"json"`` the inherited HTML detail
and attachment parsing is used unchanged, pointed at the URL the listing published.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
from datetime import datetime
from typing import Any, Protocol

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.core.errors import (
    PARSER_MISMATCH,
    SOURCE_NOT_RUNNABLE,
    SOURCE_UNREACHABLE,
)
from tender_intelligence.interfaces.source import (
    SourceError,
    SourceType,
    TenderAttachment,
    TenderDetail,
    TenderListing,
)
from tender_intelligence.sources.normalize import (
    RESPONSE_KIND_JSON,
    absolute_url,
    apply_query_params,
    as_list,
    clean_text,
    dedupe_listings,
    derive_external_id,
    dig,
    parse_timestamp,
    selector_list,
    with_metadata,
)
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import (
    Fetcher,
    HttpResponse,
    PoliteHttpClient,
    Requester,
    requester_get,
)
from tender_intelligence.sources.waho import PaginatedHtmlAdapter

log = logging.getLogger("tender_intelligence.sources.json_api")

#: Field names tried for each mapped value when the source configures no path. These are
#: generic API vocabulary, not any API's schema; an explicitly configured path always wins.
_DEFAULT_ID_PATHS = ("id", "reference", "ref", "code", "number", "tender_id", "tenderId")
_DEFAULT_TITLE_PATHS = ("title", "name", "subject", "heading")
_DEFAULT_URL_PATHS = ("url", "link", "href", "permalink", "details_url", "detail_url")
_DEFAULT_PUBLISHED_PATHS = (
    "published_at",
    "published",
    "publishedAt",
    "date",
    "posted",
    "created",
)
_DEFAULT_DEADLINE_PATHS = (
    "deadline",
    "deadline_at",
    "deadlineAt",
    "closing_date",
    "closingDate",
    "submission_deadline",
    "submissionDeadline",
    "due_date",
    "dueDate",
    "end_date",
)
_DEFAULT_REFERENCE_PATHS = ("reference", "reference_no", "referenceNo", "ref")
_DEFAULT_ENTITY_PATHS = ("entity", "organisation", "organization", "buyer", "procuring_body")
_DEFAULT_SCOPE_PATHS = ("scope", "description", "summary")
_DEFAULT_LINK_FIELDS = ("url", "href", "link", "download_url")
_DEFAULT_NAME_FIELDS = ("name", "filename", "title", "label")
_DEFAULT_TYPE_FIELDS = ("mime_type", "mimeType", "content_type", "contentType")
_DEFAULT_SIZE_FIELDS = ("size", "size_bytes", "sizeBytes", "file_size", "fileSize")
_DEFAULT_HINT_FIELDS = ("text", "label", "name", "value", "description")

#: Suffixes marking a link as a document bundle (prompt 07 §3).
_ZIP_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".gz")

#: Default number of requests a paged crawl may make when the source sets no cap.
_DEFAULT_MAX_PAGES = 50


class AuthResolver(Protocol):
    """Resolves a configured credential *reference* to its secret value.

    Deliberately unable to return ``None``: a resolver that cannot produce the secret raises,
    and the adapter turns that into a structured, non-silent failure.
    """

    def __call__(self, ref: str) -> str:
        ...


class EnvAuthResolver:
    """Default resolver: a reference is the name of an environment variable.

    The secret is read per crawl, so rotating the variable takes effect on the next run, and
    the value is never written to configuration, to a log line or to ``raw_metadata``
    (PROJECT_RULES #12).
    """

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def __call__(self, ref: str) -> str:
        value = self._environ.get(ref)
        if not value:
            raise KeyError(ref)
        return value


class JsonApiAdapter(PaginatedHtmlAdapter):
    """Adapter for a source that publishes tenders as JSON.

    Listing discovery — and, where configured, detail discovery — is JSON. HTML detail and
    attachment discovery are inherited unchanged, so a JSON list whose items link to ordinary
    web pages needs no second adapter.
    """

    source_type: str = SourceType.JSON_API.value

    def __init__(
        self,
        endpoint: str,
        *,
        base_url: str | None = None,
        parser_config: dict[str, Any] | None = None,
        policy: CrawlPolicy | None = None,
        fetcher: Fetcher | None = None,
        requester: Requester | None = None,
        auth_resolver: AuthResolver | None = None,
    ) -> None:
        injected_fetcher = fetcher or (requester_get(requester) if requester is not None else None)
        super().__init__(
            endpoint,
            base_url=base_url or endpoint,
            parser_config=parser_config,
            policy=policy,
            # An injected Requester serves the whole crawl, not only the JSON calls: the
            # inherited HTML detail and attachment requests are ordinary GETs and must stay
            # on the same injected transport.
            fetcher=injected_fetcher,
        )
        self._requester = requester
        # A GET-only fetcher injected by a caller is honoured for JSON too, exactly as the
        # HTML strategies honour it; it simply cannot express headers or a request body.
        self._injected_fetcher = fetcher
        self._auth_resolver: AuthResolver = auth_resolver or EnvAuthResolver()
        self._url_by_id: dict[str, str] = {}
        self._auth_headers: dict[str, str] = {}
        self._auth_params: dict[str, str] = {}

    # -- Discovery ------------------------------------------------------------

    def list_new_tenders(self) -> list[TenderListing]:
        """Page through the endpoint and return one normalised listing per unique item."""
        correlation_id = get_correlation_id()
        self._resolve_auth()
        self._url_by_id = {}
        try:
            return self._page_through(correlation_id)
        finally:
            # The resolved credential is needed for the requests and for nothing else: it is
            # dropped as soon as the crawl ends rather than left on the adapter.
            self._auth_headers, self._auth_params = {}, {}

    def _page_through(self, correlation_id: str | None) -> list[TenderListing]:
        """The paged crawl itself, with the credential already resolved."""
        max_pages = self._max_pages()

        url: str | None = self._endpoint_url()
        visited: set[str] = set()
        listings: list[TenderListing] = []
        seen_ids: set[str] = set()
        items: list[Any] = []
        seen_items = 0
        page = 0
        terminated_by = "no_next_page"

        while url and page < max_pages:
            if url in visited:
                terminated_by = "already_visited_url"
                break
            visited.add(url)
            page += 1
            log.info(
                "fetching json page %d: %s",
                page,
                url,
                extra={"stage": "discovery", "status": "fetch", "correlation_id": correlation_id},
            )
            payload = self._request_json(url)
            items = self._items_of(payload, url)
            seen_items += len(items)
            if not items and page == 1 and not self._config.get("allow_empty_listing"):
                raise SourceError(
                    "json response carried no items; the configured items_path may be wrong",
                    error_code=PARSER_MISMATCH,
                    context={"url": url, "items_path": self._config.get("items_path")},
                )
            fresh = 0
            for item in items:
                listing = self._item_to_listing(item, url)
                if listing is None or listing.external_id in seen_ids:
                    continue
                seen_ids.add(listing.external_id)
                listings.append(listing)
                fresh += 1
            if fresh == 0 and page > 1:
                # Repeating items we already hold means this page is the end of the list.
                terminated_by = "no_new_items"
                break
            url = self._next_url(payload, page, len(items), url)
            if url is not None:
                reason = self._why_finished(payload, seen_items, len(items))
                if reason:
                    # The API has already told us this was the last page; not asking for
                    # another is both correct and one request fewer.
                    url, terminated_by = None, reason
            else:
                terminated_by = self._why_finished(payload, seen_items, len(items)) or (
                    "no_next_page"
                )

        if url and page >= max_pages:
            terminated_by = "max_pages_reached"
        unique, duplicates = dedupe_listings(listings)
        log.info(
            "json discovery complete: %d item(s) from %d page(s) -> %d candidate(s), "
            "%d duplicate(s), stopped: %s",
            seen_items,
            page,
            len(unique),
            duplicates,
            terminated_by,
            extra={"stage": "discovery", "status": "done", "correlation_id": correlation_id},
        )
        return unique

    def _item_to_listing(self, item: Any, page_url: str) -> TenderListing | None:
        """Map one API item onto a :class:`TenderListing`, or ``None`` when it is unusable."""
        title = self._first_text(item, self._title_paths())
        if not title:
            log.warning(
                "json item has no title; skipping",
                extra={"stage": "discovery", "status": "warning", "page_url": page_url},
            )
            return None
        raw_id = self._first_text(item, self._id_paths())
        raw_url = self._first_text(item, self._url_paths())
        url = absolute_url(page_url, raw_url) if raw_url else ""
        external_id, identity_source = self._identity(raw_id, url)
        if not external_id:
            log.warning(
                "json item has no identity and no usable url to derive one from; skipping",
                extra={"stage": "discovery", "status": "warning", "page_url": page_url},
            )
            return None
        if not url:
            url = self._detail_endpoint(external_id)

        deadline_at, deadline_tz, deadline_raw = self._deadline(item)
        raw_metadata: dict[str, Any] = {"api_page_url": page_url}
        reference = self._first_text(item, self._paths("reference_path", ()))
        if reference:
            raw_metadata["reference"] = reference
        entity = self._first_text(item, self._paths("entity_path", ()))
        if entity:
            raw_metadata["entity"] = entity
        if deadline_raw:
            raw_metadata["deadline_raw"] = deadline_raw

        if url:
            self._url_by_id[external_id] = url
        return with_metadata(
            TenderListing(
                external_id=external_id,
                title=title,
                url=url,
                published_at=self._timestamp(item, self._published_paths()),
                deadline_at=deadline_at,
                deadline_timezone=deadline_tz,
                raw_metadata=raw_metadata,
            ),
            response_kind=RESPONSE_KIND_JSON,
            discovery_url=page_url,
            identity_source=identity_source,
        )

    def _deadline(
        self, item: Any, paths: tuple[str, ...] | None = None
    ) -> tuple[datetime | None, str | None, str | None]:
        """The closing date, with a timezone taken only from evidence the API supplied.

        An API that publishes a bare date has published no timezone. Assuming UTC would
        manufacture a deadline wrong by hours in a way nothing downstream could detect, so
        the raw value is kept in ``raw_metadata`` and no timestamp is produced (docs/05 §5.2,
        prompt 07). The two values that *are* unambiguous are accepted: an offset or ``Z``
        stated in the text, and an epoch number, which denotes an instant rather than a
        wall-clock reading of one.
        """
        candidates = tuple(paths) if paths else self._deadline_paths()
        for _path, raw in self._find_pairs(item, candidates):
            text = clean_text(raw)
            if not text:
                continue
            if _is_epoch(raw, text):
                parsed = parse_timestamp(text)
                if parsed is not None:
                    return parsed, "UTC", text
                continue
            if not _has_offset(text):
                log.info(
                    "api deadline %r carries no timezone; reporting no deadline",
                    text,
                    extra={"stage": "discovery", "status": "warning"},
                )
                return None, None, text
            parsed = parse_timestamp(text)
            if parsed is not None:
                return parsed, _offset_label(text), text
        return None, None, None

    def _identity(self, raw_id: str, url: str) -> tuple[str, str]:
        """Resolve the item's dedupe identity, recording which rule produced it."""
        pattern = clean_text(self._config.get("id_pattern"))
        if pattern:
            for candidate, origin in ((raw_id, "id_path"), (url, "url_path")):
                match = re.search(pattern, candidate or "")
                if match:
                    value = match.groupdict().get("id") or match.group(0)
                    if value:
                        return value, f"id_pattern:{origin}"
        if raw_id:
            return raw_id, "configured_id_path"
        derived = derive_external_id(url)
        return derived, "derived_from_url" if derived else ""

    def _timestamp(self, item: Any, paths: tuple[str, ...]) -> datetime | None:
        """First parseable timestamp among *paths*; ``None`` when none is usable."""
        for _path, value in self._find_pairs(item, paths):
            text = clean_text(value)
            parsed = parse_timestamp(text) if text else None
            if parsed is not None:
                return parsed
        return None

    # -- Pagination -----------------------------------------------------------

    def _next_url(
        self, payload: Any, page: int, items_on_page: int, current_url: str = ""
    ) -> str | None:
        """The URL of the next page, or ``None`` when this was the last one."""
        next_path = clean_text(self._config.get("next_path"))
        if next_path:
            following = dig(payload, next_path)
            text = clean_text(following) if isinstance(following, str) else ""
            if not text:
                return None
            if text.lower().startswith(("http://", "https://", "/")):
                return absolute_url(self._base_url, text) or None
            param = clean_text(self._config.get("cursor_param")) or "cursor"
            return self._with_paging({param: text})

        mode = self._pagination_mode()
        if mode == "page":
            return self._with_paging({self._page_param(): page + 1})
        if mode == "offset":
            # The step is the page size the source declared, or — when it declared none —
            # the number of items this page actually returned, so the walk continues from
            # where the API left off rather than from a guess.
            step = self._items_per_page() or items_on_page
            if step <= 0:
                return None
            following = self._current_offset(current_url) + step
            return self._with_paging({self._offset_param(): following})
        return None

    def _current_offset(self, url: str) -> int:
        """The offset *url* was requested with, or 0."""
        if not url:
            return 0
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        raw = (query.get(self._offset_param()) or ["0"])[0]
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    def _why_finished(self, payload: Any, collected: int, items_on_page: int) -> str | None:
        """Why the list must end here — a declared total, a short page — else ``None``.

        A total the crawl has reached and a page shorter than the size the source asked for
        are both the API saying "this was the last page", and both are checked before another
        request is made (docs/05 §5.2: no request without a reason).
        """
        total_path = clean_text(self._config.get("total_path"))
        total = dig(payload, total_path) if total_path else None
        counted = isinstance(total, (int, float)) and not isinstance(total, bool)
        if counted and collected >= int(total):  # type: ignore[arg-type]
            return "total_reached"
        expected = self._items_per_page()
        if expected and items_on_page < expected:
            return "short_page"
        return None

    def _with_paging(self, values: dict[str, Any]) -> str:
        """The listing endpoint with *values* merged into its query string."""
        merged = {**(self._config.get("params") or {}), **values}
        return apply_query_params(self._endpoint_url(), merged)

    def _pagination_mode(self) -> str:
        mode = clean_text(self._config.get("pagination_mode")).lower()
        if mode in {"page", "offset", "cursor"}:
            return mode
        return "page" if self._config.get("page_param") else ""

    def _page_param(self) -> str:
        return clean_text(self._config.get("page_param")) or "page"

    def _offset_param(self) -> str:
        return clean_text(self._config.get("offset_param")) or "offset"

    def _max_pages(self) -> int:
        raw = self._config.get("max_pages", _DEFAULT_MAX_PAGES)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_PAGES

    def _items_per_page(self) -> int:
        """The page size the source asked for, or 0 when it did not declare one.

        Read from the top level and from ``params``, because a page size is only meaningful
        if the adapter actually sends it — and a source expresses it either way.
        """
        params = self._config.get("params") or {}
        for key in ("page_size", "limit", "per_page"):
            for source in (self._config, params):
                raw = source.get(key)
                if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                    return raw
                if isinstance(raw, str) and raw.strip().isdigit() and int(raw) > 0:
                    return int(raw)
        return 0

    # -- Request / response ---------------------------------------------------

    def _endpoint_url(self) -> str:
        """The listing endpoint, with configured static query parameters applied."""
        endpoint = clean_text(self._config.get("endpoint")) or self._listing_url
        if not endpoint:
            raise SourceError(
                "json source has no endpoint configured",
                error_code=PARSER_MISMATCH,
                context={"source_type": self.source_type},
            )
        return apply_query_params(endpoint, self._config.get("params"))

    def _request_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch *url* and decode JSON, mapping every failure onto a structured code."""
        correlation_id = get_correlation_id()
        method = (clean_text(self._config.get("method")) or "GET").upper()
        headers = {
            "Accept": "application/json",
            **{str(k): str(v) for k, v in (self._config.get("headers") or {}).items()},
            **self._auth_headers,
        }
        query = {**(params or {}), **self._auth_params}
        body = self._config.get("body")
        try:
            response = self._send(
                method, url, params=query, headers=headers, **({"json": body} if body else {})
            )
        except SourceError:
            raise
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            raise SourceError(
                f"source unreachable: {url}",
                error_code=SOURCE_UNREACHABLE,
                context={
                    "url": url,
                    "last_error": type(exc).__name__,
                    "correlation_id": correlation_id,
                },
            ) from exc
        if response.status_code >= 400:
            raise SourceError(
                f"source unreachable: {url} (http {response.status_code})",
                error_code=SOURCE_UNREACHABLE,
                context={
                    "url": url,
                    "status_code": response.status_code,
                    "correlation_id": correlation_id,
                },
            )
        try:
            return json.loads(response.text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourceError(
                "response is not valid JSON; the endpoint may not be a JSON API",
                error_code=PARSER_MISMATCH,
                context={"url": url, "content_type": response.headers.get("content-type")},
            ) from exc

    def _send(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
        """Issue one request through the polite transport.

        The inherited ``_fetch`` is a GET-only helper bound to the configured fetcher, which
        is right for the HTML strategies. A JSON API may need a POST, extra headers or an
        auth parameter, so this prefers the method-aware :class:`Requester` and otherwise
        falls back to the injected fetcher, then to a client carrying the same policy —
        identical robots handling, pacing, timeout, retry and backoff (docs/05 §5.3).
        """
        if self._requester is not None:
            return self._requester(method, url, **kwargs)
        if self._injected_fetcher is not None:
            return self._injected_fetcher(url)
        client = PoliteHttpClient.build(self._policy)
        try:
            return client(method, url, **kwargs)
        finally:
            client.close()

    def _resolve_auth(self) -> None:
        """Resolve the configured credential once per crawl into headers and query params.

        The resolved value is used for the requests and then dropped: it is never stored on
        the adapter, in ``raw_metadata`` or in a log line (PROJECT_RULES #12).
        """
        auth = self._config.get("auth")
        if not isinstance(auth, dict) or not auth:
            self._auth_headers, self._auth_params = {}, {}
            return
        ref = clean_text(auth.get("ref"))
        kind = (clean_text(auth.get("type")) or "bearer").lower()
        if not ref:
            raise SourceError(
                "json source declares auth but names no auth.ref to resolve",
                error_code=SOURCE_NOT_RUNNABLE,
                context={"auth_type": kind},
            )
        try:
            secret = self._auth_resolver(ref)
        except Exception as exc:
            raise SourceError(
                f"could not resolve the credential referenced by auth.ref ({ref})",
                error_code=SOURCE_NOT_RUNNABLE,
                context={"reason": "auth_secret_unavailable", "auth_ref": ref},
            ) from exc
        if not secret:
            raise SourceError(
                f"the credential referenced by auth.ref ({ref}) is empty",
                error_code=SOURCE_NOT_RUNNABLE,
                context={"reason": "auth_secret_empty", "auth_ref": ref},
            )

        name = clean_text(auth.get("name"))
        if kind == "bearer":
            self._auth_headers, self._auth_params = {"Authorization": f"Bearer {secret}"}, {}
        elif kind == "header":
            self._auth_headers = {name or "X-Api-Key": secret}
            self._auth_params = {}
        elif kind == "query":
            self._auth_headers = {}
            self._auth_params = {name or "api_key": secret}
        else:
            raise SourceError(
                f"unsupported auth type {kind!r} for a json source",
                error_code=SOURCE_NOT_RUNNABLE,
                context={"auth_type": kind},
            )

    # -- Field access ---------------------------------------------------------

    def _items_of(self, payload: Any, url: str) -> list[Any]:
        """The list of items in a response, honouring ``items_path``."""
        items_path = clean_text(self._config.get("items_path"))
        if items_path:
            value = dig(payload, items_path)
            if isinstance(value, list):
                return value
            if value is None:
                raise SourceError(
                    "json response has nothing at the configured items_path",
                    error_code=PARSER_MISMATCH,
                    context={"url": url, "items_path": items_path},
                )
            return as_list(value)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            raise SourceError(
                "json response is an object; configure items_path to say where the list is",
                error_code=PARSER_MISMATCH,
                context={"url": url, "top_level_keys": sorted(payload)[:10]},
            )
        return []

    def _paths(self, key: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
        """Paths configured under *key*, else *defaults*."""
        configured = selector_list(self._config.get(key))
        return tuple(configured) if configured else defaults

    def _id_paths(self) -> tuple[str, ...]:
        return self._paths("id_path", _DEFAULT_ID_PATHS)

    def _title_paths(self) -> tuple[str, ...]:
        return self._paths("title_path", _DEFAULT_TITLE_PATHS)

    def _url_paths(self) -> tuple[str, ...]:
        return self._paths("url_path", _DEFAULT_URL_PATHS)

    def _published_paths(self) -> tuple[str, ...]:
        return self._paths("published_path", _DEFAULT_PUBLISHED_PATHS)

    def _deadline_paths(self) -> tuple[str, ...]:
        return self._paths("deadline_path", _DEFAULT_DEADLINE_PATHS)

    def _find_pairs(self, item: Any, paths: tuple[str, ...]) -> list[tuple[str, Any]]:
        """``(path, value)`` for every path in *paths* that yields a scalar in *item*."""
        pairs: list[tuple[str, Any]] = []
        for path in paths:
            value = dig(item, path)
            if value is None or isinstance(value, (dict, list, bool)):
                continue
            if isinstance(value, str) and not value.strip():
                continue
            pairs.append((path, value))
        return pairs

    def _first_text(self, item: Any, paths: tuple[str, ...]) -> str:
        """Cleaned text of the first path in *paths* that yields one."""
        for _path, value in self._find_pairs(item, paths):
            text = clean_text(value)
            if text:
                return text
        return ""

    def _values_at(self, payload: Any, path: Any) -> list[Any]:
        """Every value at *path*, whether it holds one item or a list of them."""
        return as_list(dig(payload, clean_text(path)))

    # -- Detail discovery -----------------------------------------------------

    def _detail_endpoint(self, tender_id: str) -> str:
        """The configured detail URL for *tender_id*, or ``""`` when none is configured."""
        template = clean_text(self._config.get("detail_endpoint"))
        if not template:
            return ""
        path = template.format(id=urllib.parse.quote(str(tender_id), safe=""))
        return urllib.parse.urljoin(self._base_url, path)

    def _detail_url(self, tender_id: str) -> str:
        """The URL the listing published for *tender_id*, else the configured endpoint."""
        discovered = self._url_by_id.get(tender_id)
        if discovered:
            return discovered
        configured = self._detail_endpoint(tender_id)
        if configured:
            return configured
        return super()._detail_url(tender_id)

    def get_detail(self, tender_id: str) -> TenderDetail:
        """Map the tender's JSON detail payload, or fall back to inherited HTML parsing."""
        if not self._json_detail():
            return super().get_detail(tender_id)
        url = self._detail_url(tender_id)
        log.info(
            "fetching json detail for tender %s: %s",
            tender_id,
            url,
            extra={
                "stage": "discovery",
                "status": "fetch",
                "correlation_id": get_correlation_id(),
            },
        )
        payload = self._request_json(url)
        paths = self._detail_paths()

        deadline_at, deadline_tz, deadline_raw = self._deadline(
            payload, self._detail_path(paths, "deadline_path", ())
        )
        raw_metadata: dict[str, Any] = {"detail_page_url": url}
        if deadline_tz:
            raw_metadata["deadline_timezone"] = deadline_tz
        if deadline_raw:
            raw_metadata["deadline_raw"] = deadline_raw
        title = self._first_text(
            payload, self._detail_path(paths, "title_path", _DEFAULT_TITLE_PATHS)
        )
        listing = with_metadata(
            TenderListing(
                external_id=tender_id,
                title=title or tender_id,
                url=url,
                published_at=self._timestamp(
                    payload, self._detail_path(paths, "published_path", _DEFAULT_PUBLISHED_PATHS)
                ),
                deadline_at=deadline_at,
                deadline_timezone=deadline_tz,
                raw_metadata=dict(raw_metadata),
            ),
            response_kind=RESPONSE_KIND_JSON,
            discovery_url=url,
            identity_source="detail_endpoint",
        )
        return TenderDetail(
            listing=listing,
            procuring_body=self._first_text(
                payload, self._detail_path(paths, "entity_path", _DEFAULT_ENTITY_PATHS)
            )
            or None,
            reference_numbers=self._detail_references(payload, paths),
            scope=self._first_text(
                payload, self._detail_path(paths, "scope_path", _DEFAULT_SCOPE_PATHS)
            )
            or None,
            requirements_hints=self._hints(payload, paths.get("requirements_path")),
            attachments=self._attachments_from(payload, url, paths),
        )

    def get_attachments(self, tender_id: str) -> list[TenderAttachment]:
        """Attachments from the JSON detail payload, or from the HTML detail page."""
        if not self._json_detail():
            return super().get_attachments(tender_id)
        url = self._detail_url(tender_id)
        return self._attachments_from(self._request_json(url), url, self._detail_paths())

    def _json_detail(self) -> bool:
        """Whether the detail endpoint is JSON, as opposed to an HTML page to inherit."""
        return clean_text(self._config.get("detail_response_kind")).lower() == "json"

    def _detail_paths(self) -> dict[str, Any]:
        paths = self._config.get("detail_paths")
        return dict(paths) if isinstance(paths, dict) else {}

    def _detail_path(
        self, paths: dict[str, Any], key: str, defaults: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Paths for one detail field.

        ``detail_paths`` groups every detail field in one block, so it is read first; the flat
        ``detail_<field>_path`` form stays supported for a configuration that sets one field
        without the group. A field configured in neither place falls back to the generic API
        vocabulary.
        """
        configured = selector_list(paths.get(key)) or selector_list(
            self._config.get(f"detail_{key}")
        )
        return tuple(configured) if configured else defaults

    def _detail_references(self, payload: Any, paths: dict[str, Any]) -> list[str]:
        """Every reference number the detail payload publishes, in configured order.

        Several paths are read because a tender commonly carries a publisher's reference
        *and* a notice identifier, and both are worth keeping: the first is what the
        publisher calls the tender, the second is what their own portal searches on.
        """
        configured = selector_list(paths.get("reference_path"))
        names = configured or list(_DEFAULT_REFERENCE_PATHS)
        return [text for text in (self._first_text(payload, (name,)) for name in names) if text]

    def _hints(self, payload: Any, path: Any) -> list[str]:
        """Requirement hints, accepting either a list of strings or a list of objects."""
        hints: list[str] = []
        for entry in self._values_at(payload, path):
            if isinstance(entry, str):
                text = clean_text(entry)
            elif isinstance(entry, dict):
                text = self._first_text(entry, _DEFAULT_HINT_FIELDS)
            else:
                text = ""
            if text:
                hints.append(text)
        return hints

    def _attachments_from(
        self, payload: Any, page_url: str, paths: dict[str, Any]
    ) -> list[TenderAttachment]:
        """Map a configured array of document objects onto :class:`TenderAttachment` records.

        Each object needs a link; filename, media type and size are taken from the configured
        field when the API publishes one and left unset otherwise. The four fields are
        independent — ``attachment_link_field``, ``attachment_name_field``,
        ``attachment_type_field`` and ``attachment_size_field`` — and each is only consulted
        for its own value. As everywhere, a bundle is one attachment: inner files are never
        enumerated (prompt 07 §3).
        """
        entries = self._values_at(payload, paths.get("attachments_path"))
        link_field = clean_text(paths.get("attachment_link_field"))
        name_field = clean_text(paths.get("attachment_name_field"))
        type_field = clean_text(paths.get("attachment_type_field"))
        size_field = clean_text(paths.get("attachment_size_field"))
        # Each configured field is used for its own job only. A filename field is never tried
        # as a link: preferring it produced URLs built out of file names.
        link_names = (link_field, *_DEFAULT_LINK_FIELDS) if link_field else _DEFAULT_LINK_FIELDS
        name_names = (name_field, *_DEFAULT_NAME_FIELDS) if name_field else _DEFAULT_NAME_FIELDS
        type_names = (type_field, *_DEFAULT_TYPE_FIELDS) if type_field else _DEFAULT_TYPE_FIELDS
        attachments: list[TenderAttachment] = []
        for entry in entries:
            if isinstance(entry, str):
                entry = {"url": entry}
            if not isinstance(entry, dict):
                continue
            href = absolute_url(page_url, self._first_text(entry, link_names))
            if not href:
                continue
            filename = self._first_text(entry, name_names) or href.rsplit("/", 1)[-1] or href
            size = dig(entry, size_field) if size_field else None
            measured = (
                int(size)
                if isinstance(size, (int, float)) and not isinstance(size, bool)
                else None
            )
            attachments.append(
                TenderAttachment(
                    source_url=href,
                    filename=filename,
                    mime_type=self._first_text(entry, type_names) or None,
                    advertised_size_bytes=measured,
                    is_zip=href.rsplit("?", 1)[0].lower().endswith(_ZIP_SUFFIXES),
                )
            )
        return attachments


#: A numeric value that denotes an instant, and so needs no timezone evidence.
_EPOCH_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _is_epoch(raw: Any, text: str) -> bool:
    """Whether *raw* is an epoch number (a number, or its digits written as a string)."""
    if isinstance(raw, bool):
        return False
    if isinstance(raw, (int, float)):
        return True
    return bool(_EPOCH_RE.fullmatch(text))


def _has_offset(text: str) -> bool:
    """Whether *text* carries an explicit UTC offset or ``Z``."""
    return bool(_OFFSET_RE.search(text.strip()))


def _offset_label(text: str) -> str | None:
    """The timezone label the value's own text states, or ``None`` when it states none."""
    match = _OFFSET_RE.search(text.strip())
    if match is None:
        return None
    label = match.group(1)
    return "UTC" if label in {"Z", "z"} else label


#: Trailing UTC offset or ``Z`` in a timestamp string.
_OFFSET_RE = re.compile(r"(Z|z|[+-]\d{2}:?\d{2})$")

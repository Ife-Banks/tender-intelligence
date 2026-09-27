""":mod:`tender_intelligence.sources` — source adapters (docs/05).

Each adapter implements the generic :class:`~tender_intelligence.interfaces.source.SourceAdapter`
interface and stays isolated behind it (PROJECT_RULES #10). Concrete source adapters live in
their own modules; the five v1.1 strategies are:

============================  ==============================================
:class:`PaginatedHtmlAdapter` paginated/faceted HTML list (also ``filtered_html``)
:class:`FilteredHtmlAdapter`  HTML list reached through configured facets
:class:`SearchFormAdapter`    HTML list reached by submitting a search form
:class:`FeedAdapter`          RSS 2.0, RSS 1.0/RDF or Atom syndication feed
:class:`JsonApiAdapter`       JSON endpoint, optionally authenticated
============================  ==============================================

Which one a configured source uses is a ``source_type`` in the database and a key in
:class:`~tender_intelligence.orchestrator.registry.AdapterRegistry` — never a code change.
No module here contains a site name, hostname or site-specific selector.

:func:`~tender_intelligence.sources.normalize.dig`,
:func:`~tender_intelligence.sources.normalize.apply_query_params` and the rest of
:mod:`tender_intelligence.sources.normalize` are the shared rules every strategy applies, so
identity, timestamps, query parameters and provenance stay identical across transports.
"""

from __future__ import annotations

from tender_intelligence.sources.feed import FeedAdapter
from tender_intelligence.sources.filtered_html import FilteredHtmlAdapter
from tender_intelligence.sources.json_api import AuthResolver, EnvAuthResolver, JsonApiAdapter
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse, PoliteHttpClient, Requester
from tender_intelligence.sources.search_form import (
    FormDriver,
    FormSpec,
    HttpFormDriver,
    SearchFormAdapter,
    UnavailableFormDriver,
)
from tender_intelligence.sources.waho import PaginatedHtmlAdapter, WahoPaginatedAdapter

__all__ = [
    "AuthResolver",
    "CrawlPolicy",
    "EnvAuthResolver",
    "FeedAdapter",
    "FilteredHtmlAdapter",
    "FormDriver",
    "FormSpec",
    "HttpFormDriver",
    "HttpResponse",
    "JsonApiAdapter",
    "PaginatedHtmlAdapter",
    "PoliteHttpClient",
    "Requester",
    "SearchFormAdapter",
    "UnavailableFormDriver",
    "WahoPaginatedAdapter",
]

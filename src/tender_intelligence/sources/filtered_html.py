""":mod:`tender_intelligence.sources.filtered_html` — Filtered/faceted HTML list strategy.

docs/05 §5.1 names "Filtered/faceted HTML list" as its own source type, exemplified by a site
whose tenders are reachable only through a query-string filter (``?status=open``). The filter
is a *source configuration* fact, not code: which parameters exist, what values they take and
whether several of them must be combined are all operator-supplied, so this strategy holds no
site name, hostname or parameter name of its own (PROJECT_RULES #10).

Two configuration shapes are supported, and they compose with the paginated strategy's own
``query_params``:

``query_params``
    One parameter set applied to every request in the chain, e.g. ``{"status": "open"}``.

``facets``
    A list of parameter sets to walk one at a time, e.g.
    ``[{"status": "open"}, {"status": "open", "category": "works"}]``. Each facet is a full
    paginated crawl; a tender listed under several facets is returned once. This is the shape
    a site with a category/status matrix needs, and the reason a filtered source is not merely
    a paginated source with a parameter attached.

An *empty* facet result is not a parser failure. A filter combination that matches nothing is
ordinary for a faceted site, so it yields no listings and the crawl moves on; only the
unfiltered first page failing to parse is treated as structural drift (``parser_mismatch``).
"""

from __future__ import annotations

import logging
from typing import Any

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.interfaces.source import SourceType, TenderListing
from tender_intelligence.sources.normalize import (
    DISCOVERY_URL_KEY,
    RESPONSE_KIND_HTML,
    with_metadata,
)
from tender_intelligence.sources.waho import PaginatedHtmlAdapter

log = logging.getLogger("tender_intelligence.sources.filtered_html")

#: ``raw_metadata`` key recording which facet produced a listing. Kept on the listing so a
#: later run can explain why a tender was considered, and so two facets returning the same
#: tender are visibly the same tender.
FACET_KEY = "filter_params"


class FilteredHtmlAdapter(PaginatedHtmlAdapter):
    """Configuration-driven adapter for faceted HTML tender lists.

    Row parsing, next-page following, loop prevention, politeness and detail-page extraction
    are inherited unchanged from :class:`~tender_intelligence.sources.waho.PaginatedHtmlAdapter`
    — a filtered list is a paginated list reached through a configured filter, so the two
    strategies differ only in how the entry URL is built and how many times the chain is
    walked.
    """

    source_type: str = SourceType.FILTERED_HTML.value

    def list_new_tenders(self) -> list[TenderListing]:
        """Crawl every configured facet and return the deduplicated union of the results."""
        correlation_id = get_correlation_id()
        facets = self.facets()
        log.info(
            "filtered discovery starting: %d facet(s) configured",
            len(facets),
            extra={"stage": "discovery", "status": "start", "correlation_id": correlation_id},
        )

        collected: list[TenderListing] = []
        seen_ids: set[str] = set()
        for index, facet in enumerate(facets, start=1):
            for listing in self._crawl(self._listing_url, facet):
                if listing.external_id in seen_ids:
                    continue
                seen_ids.add(listing.external_id)
                collected.append(
                    with_metadata(
                        listing,
                        response_kind=RESPONSE_KIND_HTML,
                        discovery_url=listing.raw_metadata.get(DISCOVERY_URL_KEY)
                        or self._listing_url,
                        **{FACET_KEY: facet, "facet_index": index},
                    )
                )
        return collected

    def facets(self) -> list[dict[str, Any]]:
        """The parameter sets to walk, in configured order.

        Always at least one entry: a source with neither ``facets`` nor ``query_params``
        configured crawls the bare listing URL exactly once, which makes the strategy safe to
        select for a site whose filters are added to the configuration later.
        """
        raw = self._config.get("facets")
        facets: list[dict[str, Any]] = []
        if isinstance(raw, (list, tuple)):
            for entry in raw:
                if isinstance(entry, dict) and entry:
                    facets.append({str(key): value for key, value in entry.items()})
        elif isinstance(raw, dict) and raw:
            facets.append({str(key): value for key, value in raw.items()})
        if not facets:
            query_params = self._config.get("query_params") or {}
            facets.append({str(key): value for key, value in query_params.items()})
        return facets

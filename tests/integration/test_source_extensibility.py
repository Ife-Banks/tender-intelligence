"""Offline proof that configured paginated sources share the generic pipeline seam."""

from __future__ import annotations

from tender_intelligence.db.models.sources import Source
from tender_intelligence.dedup.service import DedupService
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.orchestrator.scheduler import SourceSpec
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse


class _FixtureFetcher:
    """Return source-specific listing markup without making network requests."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def __call__(self, url: str) -> HttpResponse:
        return HttpResponse(200, {}, self.pages[url], url)


def test_two_paginated_sources_with_different_selectors_reach_dedup(
    session_factory_gr,
) -> None:
    pages = {
        "https://alpha.example/list": (
            '<article class="card"><a class="name" '
            'href="/tenders/tenders/101/list">Alpha opportunity</a></article>'
        ),
        "https://beta.example/notices": (
            '<li class="notice"><a class="notice-title" '
            'href="/tenders/tenders/202/list">Beta opportunity</a></li>'
        ),
    }
    parser_defaults = {
        "detail_href_pattern": r"/tenders/tenders/(?P<id>\d+)/list",
        "published_date_pattern": r"(?!)",
        "deadline_patterns": [],
    }

    with session_factory_gr() as session:
        alpha = Source(
            name="alpha",
            source_type="paginated_html_list",
            base_url="https://alpha.example",
            listing_url="https://alpha.example/list",
            parser_config={
                **parser_defaults,
                "row_selector": "article.card",
                "title_selector": "a.name",
            },
            active=True,
        )
        beta = Source(
            name="beta",
            source_type="paginated_html_list",
            base_url="https://beta.example",
            listing_url="https://beta.example/notices",
            parser_config={
                **parser_defaults,
                "row_selector": "li.notice",
                "title_selector": "a.notice-title",
            },
            active=True,
        )
        session.add_all([alpha, beta])
        session.commit()
        alpha_id, beta_id = alpha.id, beta.id
        specs = [SourceSpec.from_row(alpha), SourceSpec.from_row(beta)]

    registry = AdapterRegistry.default(
        policy=CrawlPolicy(request_interval_seconds=0),
        fetcher=_FixtureFetcher(pages),
    )
    candidates = [registry.build(spec).list_new_tenders() for spec in specs]

    assert [items[0].title for items in candidates] == [
        "Alpha opportunity",
        "Beta opportunity",
    ]
    assert [items[0].external_id for items in candidates] == ["101", "202"]

    dedup = DedupService(session_factory_gr)
    alpha_result = dedup.run(alpha_id, candidates[0])
    beta_result = dedup.run(beta_id, candidates[1])

    assert alpha_result.new_count == beta_result.new_count == 1

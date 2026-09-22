""":mod:`tender_intelligence.sources` — source adapters (docs/05).

Each adapter implements the generic :class:`~tender_intelligence.interfaces.source.SourceAdapter`
interface and stays isolated behind it (PROJECT_RULES #10). Concrete source adapters live in
their own modules; WAHO begins with the discovery half (``prompts/04-waho-discovery.md``).
"""

from __future__ import annotations

from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import HttpResponse, PoliteHttpClient
from tender_intelligence.sources.waho import WahoPaginatedAdapter

__all__ = [
    "CrawlPolicy",
    "HttpResponse",
    "PoliteHttpClient",
    "WahoPaginatedAdapter",
]

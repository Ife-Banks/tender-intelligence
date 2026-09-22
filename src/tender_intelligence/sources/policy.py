""":mod:`tender_intelligence.sources.policy` — polite-crawling policy (docs/05 §5.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_USER_AGENT = (
    "TenderIntelligence/0.1 (+https://example.invalid/contact; research/business crawler)"
)


@dataclass(frozen=True)
class CrawlPolicy:
    """Per-source polite-crawling settings (``parser_config``-overridable, docs/05 §5.2).

    All values are operator/adapter configuration, not business decisions (prompt 04 §7).
    """

    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = 20.0
    request_interval_seconds: float = 2.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    respect_robots: bool = True
    retry_statuses: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 500, 502, 503, 504})
    )
    max_pages: int = 15

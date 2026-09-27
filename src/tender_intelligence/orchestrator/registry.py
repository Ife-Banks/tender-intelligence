"""Mapping a configured ``source_type`` onto a discovery adapter (prompt 10 §7).

No adapter registry existed before this prompt: stages 04–07 are realised by a single
concrete adapter, :class:`~tender_intelligence.sources.waho.WahoPaginatedAdapter`, which the
tests instantiate directly. The orchestrator needs to go from a *database row* to an
adapter, so this module supplies that one mapping — and nothing else. It holds no
source-specific logic of its own (PROJECT_RULES #10); it only decides which class to build.

All five v1.1 strategies are registered. They are peer strategies reached by
configuration, not a hierarchy: ``paginated_html_list``, ``filtered_html``, ``search_form``,
``rss_atom`` and ``json_api`` each read a different transport, and none of them is a
fallback for another. The legacy ``wahoo`` value stays registered against the paginated
adapter, because that is what the existing fixtures and seeds use and it must keep working
rather than become a stage-04 failure.

**An unknown ``source_type`` is a failure, never a skip.** Prompt 10 §7 permits
``SKIPPED_NOT_IMPLEMENTED`` only when the implementation status of a stage is *explicitly
known*; a source whose type this worker cannot build has no such knowledge behind it, so the
run stops at stage 04 with ``source_not_runnable``. Silently skipping it would let a
misconfigured source look like a source with no tenders.
"""

from __future__ import annotations

from collections.abc import Callable

from tender_intelligence.interfaces.source import SourceAdapter, SourceType
from tender_intelligence.orchestrator.errors import SourceNotRunnableError
from tender_intelligence.orchestrator.scheduler import SourceSpec
from tender_intelligence.sources.feed import FeedAdapter
from tender_intelligence.sources.filtered_html import FilteredHtmlAdapter
from tender_intelligence.sources.json_api import AuthResolver, JsonApiAdapter
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import Fetcher, Requester
from tender_intelligence.sources.search_form import FormDriver, SearchFormAdapter
from tender_intelligence.sources.waho import PaginatedHtmlAdapter

#: Builds an adapter for one source. Injected so tests can supply an offline adapter.
AdapterFactory = Callable[[SourceSpec], SourceAdapter]

#: Legacy ``source_type`` values that mean "the paginated HTML adapter". The canonical
#: value is ``paginated_html_list``; ``wahoo`` is what the existing prompt 04–06 fixtures
#: and seeds use, and it must keep working rather than become a stage-04 failure.
PAGINATED_HTML_ALIASES: tuple[str, ...] = ("wahoo",)


class AdapterRegistry:
    """Resolves a :class:`SourceSpec` to a concrete adapter (prompt 10 §7)."""

    def __init__(self, factories: dict[str, AdapterFactory] | None = None) -> None:
        self._factories: dict[str, AdapterFactory] = dict(factories or {})

    def register(self, source_type: str, factory: AdapterFactory) -> None:
        self._factories[source_type.strip().lower()] = factory

    def supports(self, source_type: str) -> bool:
        return (source_type or "").strip().lower() in self._factories

    @property
    def source_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))

    def build(self, spec: SourceSpec) -> SourceAdapter:
        """Return an adapter for *spec*.

        Raises :class:`SourceNotRunnableError` when the type is unregistered or the source
        lacks the configuration its adapter needs (prompt 10 §7, §10).
        """
        key = (spec.source_type or "").strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            raise SourceNotRunnableError(
                f"no discovery adapter registered for source_type {spec.source_type!r}",
                context={"source_id": spec.id, "known_types": list(self.source_types)},
            )
        return factory(spec)

    @classmethod
    def default(
        cls,
        *,
        policy: CrawlPolicy | None = None,
        fetcher: Fetcher | None = None,
        requester: Requester | None = None,
        form_driver: FormDriver | None = None,
        auth_resolver: AuthResolver | None = None,
    ) -> AdapterRegistry:
        """The production registry: all five strategies plus the legacy alias.

        *fetcher* and *requester* are threaded through so the worker can inject a transport
        (a test's in-process transport, or an offline one) without any stage knowing about
        it. *requester* is the method-aware form, needed by the strategies that issue a POST
        or send headers — the search form's submission and a JSON API's authenticated GET.
        *form_driver* and *auth_resolver* are the two execution boundaries the JSON and
        search-form strategies define; left unset, each uses its default implementation.
        """
        registry = cls()

        def _entry_url(spec: SourceSpec) -> str:
            """The URL a strategy reads its listing from, or a structured failure."""
            if not spec.listing_url:
                raise SourceNotRunnableError(
                    "source has no listing_url configured",
                    context={"source_id": spec.id, "source_type": spec.source_type},
                )
            return spec.listing_url

        def build_paginated(spec: SourceSpec) -> SourceAdapter:
            return PaginatedHtmlAdapter(
                _entry_url(spec),
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
            )

        def build_filtered(spec: SourceSpec) -> SourceAdapter:
            return FilteredHtmlAdapter(
                _entry_url(spec),
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
            )

        def build_search_form(spec: SourceSpec) -> SourceAdapter:
            return SearchFormAdapter(
                _entry_url(spec),
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
                requester=requester,
                form_driver=form_driver,
            )

        def build_feed(spec: SourceSpec) -> SourceAdapter:
            return FeedAdapter(
                _entry_url(spec),
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
            )

        def build_json_api(spec: SourceSpec) -> SourceAdapter:
            return JsonApiAdapter(
                _entry_url(spec),
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
                requester=requester,
                auth_resolver=auth_resolver,
            )

        registry.register(SourceType.PAGINATED_HTML.value, build_paginated)
        registry.register(SourceType.FILTERED_HTML.value, build_filtered)
        registry.register(SourceType.SEARCH_FORM.value, build_search_form)
        registry.register(SourceType.RSS_ATOM.value, build_feed)
        registry.register(SourceType.JSON_API.value, build_json_api)
        for alias in PAGINATED_HTML_ALIASES:
            registry.register(alias, build_paginated)
        return registry

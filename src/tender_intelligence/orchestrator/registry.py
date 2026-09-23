"""Mapping a configured ``source_type`` onto a discovery adapter (prompt 10 §7).

No adapter registry existed before this prompt: stages 04–07 are realised by a single
concrete adapter, :class:`~tender_intelligence.sources.waho.WahoPaginatedAdapter`, which the
tests instantiate directly. The orchestrator needs to go from a *database row* to an
adapter, so this module supplies that one mapping — and nothing else. It holds no
source-specific logic of its own (PROJECT_RULES #10); it only decides which class to build.

**An unknown ``source_type`` is a failure, never a skip.** Prompt 10 §7 permits
``SKIPPED_NOT_IMPLEMENTED`` only when the implementation status of a stage is *explicitly
known*; a source whose type this worker cannot build has no such knowledge behind it, so
the run stops at stage 04 with ``source_not_runnable``. Silently skipping it would let a
misconfigured source look like a source with no tenders.
"""

from __future__ import annotations

from collections.abc import Callable

from tender_intelligence.interfaces.source import SourceAdapter
from tender_intelligence.orchestrator.errors import SourceNotRunnableError
from tender_intelligence.orchestrator.scheduler import SourceSpec
from tender_intelligence.sources.policy import CrawlPolicy
from tender_intelligence.sources.polite import Fetcher
from tender_intelligence.sources.waho import SOURCE_TYPE as WAHO_SOURCE_TYPE
from tender_intelligence.sources.waho import WahoPaginatedAdapter

#: Builds an adapter for one source. Injected so tests can supply an offline adapter.
AdapterFactory = Callable[[SourceSpec], SourceAdapter]

#: Legacy ``source_type`` values that mean "the WAHO paginated adapter". The canonical
#: value is ``paginated_html_list``; ``wahoo`` is what the existing prompt 04–06 fixtures
#: and seeds use, and it must keep working rather than become a stage-04 failure.
WAHO_ALIASES: tuple[str, ...] = ("wahoo",)


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
    ) -> AdapterRegistry:
        """The production registry: the WAHO paginated adapter and its alias.

        *fetcher* is threaded through so the worker can inject a transport (a test's
        in-process fetcher, or an offline one) without any stage knowing about it.
        """
        registry = cls()

        def build_waho(spec: SourceSpec) -> SourceAdapter:
            if not spec.listing_url:
                raise SourceNotRunnableError(
                    "source has no listing_url configured",
                    context={"source_id": spec.id, "source_type": spec.source_type},
                )
            return WahoPaginatedAdapter(
                spec.listing_url,
                base_url=spec.base_url or None,
                parser_config=spec.parser_config or None,
                policy=policy,
                fetcher=fetcher,
            )

        registry.register(WAHO_SOURCE_TYPE, build_waho)
        for alias in WAHO_ALIASES:
            registry.register(alias, build_waho)
        return registry

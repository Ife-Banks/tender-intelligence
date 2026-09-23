"""Orchestrator-level failures (prompt 10 §10).

Prompt 10 §10 is explicit that stage failures must reuse the *owning stage's* error code
where one exists — ``source_unreachable`` from discovery, ``dedup_*`` from dedup,
``persistence_*`` from persistence — and that a new code is justified only for a condition
that is genuinely orchestration-specific. These are the two:

* :class:`ConfigurationLoadError` — the run could not read its configuration.
* :class:`SourceNotRunnableError` — the source's configuration names a discovery adapter
  the worker does not have, or omits what that adapter needs. This is *not* a parser
  mismatch (the page was never fetched) and *not* source unreachable (the site was never
  contacted); it is the run refusing to pretend it can crawl something it cannot.
"""

from __future__ import annotations

from tender_intelligence.core.errors import CONFIGURATION_LOAD_FAILED, SOURCE_NOT_RUNNABLE


class OrchestratorError(Exception):
    """Base class for orchestration failures, carrying a machine-readable code."""

    error_code: str = SOURCE_NOT_RUNNABLE

    def __init__(self, message: str, *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}


class ConfigurationLoadError(OrchestratorError):
    """The run's configuration could not be read (prompt 10 §3, §10)."""

    error_code = CONFIGURATION_LOAD_FAILED


class SourceNotRunnableError(OrchestratorError):
    """The source cannot be crawled by this worker (prompt 10 §7, §10)."""

    error_code = SOURCE_NOT_RUNNABLE

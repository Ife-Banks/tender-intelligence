"""Pipeline orchestration for stages 04 → 09 (prompt 10).

The shape prompt 10 §21 asks for, and the shape this package has::

    Worker                          worker.py     one pass over the due sources
      └── RunCoordinator            coordinator.py  stages 04–09 for one source
            ├── ConfigLoader        config.py       re-read per run, never cached
            ├── SourceScheduler     scheduler.py    which sources are due
            ├── StageRunner         stages.py       order, attribution, containment
            ├── RetryPolicy         retry.py        bounded, non-multiplying retry
            ├── AdapterRegistry     registry.py     source_type → discovery adapter
            └── RunHistory repo     (prompt 06)     run row create/complete/stage map

Plus :mod:`~tender_intelligence.orchestrator.alerts` (the alert seam prompt 12 will fill)
and :mod:`~tender_intelligence.orchestrator.status` (the stage/run status vocabulary).

Nothing here reimplements a stage. Discovery is prompt 04's adapter, dedup is prompt 05's
service, persistence is prompt 06's repositories, detail discovery is prompt 07's adapter
method, acquisition is prompt 08's service, and extraction is prompt 09's service — all
reached through their existing public entry points.
"""

from tender_intelligence.orchestrator.alerts import (
    AlertHook,
    AlertNotice,
    NullAlertHook,
)
from tender_intelligence.orchestrator.config import (
    ConfigLoader,
    RuntimeConfigSnapshot,
)
from tender_intelligence.orchestrator.coordinator import RunCoordinator, TenderWorkItem
from tender_intelligence.orchestrator.errors import (
    ConfigurationLoadError,
    OrchestratorError,
    SourceNotRunnableError,
)
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.orchestrator.retry import (
    RetryOutcome,
    RetryPolicy,
    run_with_retry,
)
from tender_intelligence.orchestrator.scheduler import (
    ScheduleDecision,
    SourceScheduler,
    SourceSpec,
)
from tender_intelligence.orchestrator.stages import (
    StageExecution,
    StageReport,
    StageRunner,
)
from tender_intelligence.orchestrator.status import (
    STAGE_ORDER,
    RunReport,
    RunStatus,
    StageNumber,
    StageOutcome,
    StageStatus,
)
from tender_intelligence.orchestrator.worker import (
    SourceFailure,
    Worker,
    WorkerReport,
)

__all__ = [
    "STAGE_ORDER",
    "AdapterRegistry",
    "AlertHook",
    "AlertNotice",
    "ConfigLoader",
    "ConfigurationLoadError",
    "NullAlertHook",
    "OrchestratorError",
    "RetryOutcome",
    "RetryPolicy",
    "RunCoordinator",
    "RunReport",
    "RunStatus",
    "RuntimeConfigSnapshot",
    "ScheduleDecision",
    "SourceFailure",
    "SourceNotRunnableError",
    "SourceScheduler",
    "SourceSpec",
    "StageExecution",
    "StageNumber",
    "StageOutcome",
    "StageReport",
    "StageRunner",
    "StageStatus",
    "TenderWorkItem",
    "Worker",
    "WorkerReport",
    "run_with_retry",
]

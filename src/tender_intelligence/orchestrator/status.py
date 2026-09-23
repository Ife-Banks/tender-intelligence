"""Stage and run status vocabulary for the pipeline orchestrator (prompt 10 §8).

Prompt 10 §8 fixes six stage statuses and forbids collapsing them into a single final run
boolean: a run that half-succeeded must be distinguishable from one that wholly failed, and
a completed *or* failed run must be reconstructable from the persisted record alone. These
are the values written into ``RunHistory.stages`` (migration 0004).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class StageStatus(StrEnum):
    """The six per-stage outcomes prompt 10 §8 requires.

    ``StrEnum`` so the value serialises into ``RunHistory.stages`` as a plain string and a
    persisted run can be read back without importing this module.
    """

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED_NOT_IMPLEMENTED = "SKIPPED_NOT_IMPLEMENTED"


class RunStatus(StrEnum):
    """Run-level status, derived — never stored (docs/03 §3.2, docs/04 §4.8)."""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    DRY_RUN = "DRY_RUN"


class StageNumber(StrEnum):
    """The pipeline stages this orchestrator drives, in execution order (prompt 10 §6).

    The numeric prefix is the owning prompt, so a stage name maps onto the specification
    section that defines its behaviour. The order here *is* the execution order; prompt 10
    §6 forbids reordering and forbids skipping a stage.
    """

    DISCOVERY = "04-discovery"
    DEDUP = "05-dedup"
    PERSISTENCE = "06-persistence"
    DETAIL = "07-detail-document-discovery"
    ACQUISITION = "08-acquisition"
    PROCESSING = "09-processing"


#: Execution order, derived from the enum's declaration order (prompt 10 §6).
STAGE_ORDER: tuple[StageNumber, ...] = tuple(StageNumber)


@dataclass(frozen=True)
class StageOutcome:
    """What one stage did, in enough detail to reconstruct the run (prompt 10 §8)."""

    stage: StageNumber
    status: StageStatus
    correlation_id: str
    started_at: datetime
    ended_at: datetime
    error_code: str | None = None
    message: str | None = None
    detail: str | None = None
    item_count: int = 0
    failed_items: int = 0
    attempts: int = 0

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())


@dataclass(frozen=True)
class RunReport:
    """The orchestrator's return value for one source run (prompt 10 §18, §22).

    Carries no secrets and no raw document contents: a report is safe to log, return from
    an API call, or persist (prompt 10 §3, §10).
    """

    source_id: int
    source_name: str
    correlation_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime
    stages: tuple[StageOutcome, ...]
    run_history_id: int | None = None
    config_version: int | None = None
    failed_stage: StageNumber | None = None
    error_code: str | None = None
    tenders_considered: int = 0
    documents_acquired: int = 0
    documents_processed: int = 0
    dry_run: bool = False
    planned_actions: tuple[str, ...] = field(default=())

    def stage(self, stage: StageNumber) -> StageOutcome | None:
        """The outcome recorded for *stage*, if it ran."""
        for outcome in self.stages:
            if outcome.stage is stage:
                return outcome
        return None

    def stage_map(self) -> dict[str, str]:
        """The ``{stage: status}`` map persisted on the run row (prompt 10 §8)."""
        return {outcome.stage.value: str(outcome.status) for outcome in self.stages}

    @property
    def duration_seconds(self) -> float:
        return max(0.0, (self.ended_at - self.started_at).total_seconds())

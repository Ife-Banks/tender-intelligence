"""Running one stage, and recording what it did (prompt 10 §6, §8, §9, §10, §15).

The runner owns exactly four things and nothing else:

1. **Order** — it is driven by the coordinator strictly in :data:`STAGE_ORDER` (prompt 10 §6);
   it never chooses what runs next and never runs a stage twice.
2. **Attribution** — every stage records its own start/end, correlation id and error code, so a
   completed *or* failed run is reconstructable from the record alone (prompt 10 §8).
3. **Error translation** — a stage's own error code is preserved where it has one (prompt 10
   §10); ``stage_failed`` is used only when the stage raised something that carries no code.
4. **Failure containment** — an exception is captured into the outcome rather than escaping, so
   the coordinator decides whether the run continues (§9, §15).

It deliberately knows nothing about sources, tenders, documents or HTTP. Every stage's actual
behaviour stays with the prompt that owns it (prompt 10 §20).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tender_intelligence.core.correlation import get_correlation_id
from tender_intelligence.core.errors import STAGE_FAILED
from tender_intelligence.orchestrator.status import StageNumber, StageOutcome, StageStatus

log = logging.getLogger("tender_intelligence.orchestrator.stages")

#: Cap on a failure message copied into an outcome. The row stores only the code; this text
#: goes to the log and the in-memory report, and must stay short and content-free
#: (prompt 10 §10).
MAX_MESSAGE_CHARS = 300

#: Exception attributes searched, in order, for a stage's own machine-readable code. Duck-typed
#: rather than importing every stage's exception class: the orchestrator must not depend on the
#: internals of prompts 04–09 to translate their failures (prompt 10 §20).
_ERROR_CODE_ATTR = "error_code"


@dataclass(frozen=True)
class StageReport:
    """What a stage wants recorded about itself, beyond its timing and status."""

    detail: str | None = None
    item_count: int = 0
    failed_items: int = 0
    status: StageStatus | None = None
    attempts: int = 1
    message: str | None = None


@dataclass(frozen=True)
class StageExecution[T]:
    """A stage's recorded outcome plus whatever the next stage needs from it."""

    outcome: StageOutcome
    payload: T | None


def error_code_of(exc: BaseException) -> str:
    """The stage's own error code, or ``stage_failed`` when it carries none (prompt 10 §10)."""
    code = getattr(exc, _ERROR_CODE_ATTR, None)
    if isinstance(code, str) and code:
        return code
    return STAGE_FAILED


def safe_message(exc: BaseException) -> str:
    """A short, content-free description of a failure (prompt 10 §10).

    Prefers an exception's own ``message`` attribute — every stage error in this codebase
    curates one — over ``str(exc)``, and truncates. Error *context* is deliberately not
    copied: it is the field most likely to carry a URL or a payload.
    """
    raw = getattr(exc, "message", None)
    text = raw if isinstance(raw, str) and raw else f"{type(exc).__name__}"
    text = " ".join(text.split())
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 3] + "..."
    return text


class StageRunner:
    """Times, records and contains one stage call (prompt 10 §6, §8, §9)."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def run[T](
        self,
        stage: StageNumber,
        action: Callable[[], tuple[StageReport, T]],
    ) -> StageExecution[T]:
        """Execute *action*, returning its outcome and payload (prompt 10 §8)."""
        stage_cid = get_correlation_id() or ""
        started_at = self._clock()
        self._log(stage, "start", stage_cid, None, "stage started")

        try:
            report, payload = action()
        except Exception as exc:  # noqa: BLE001 - containment is this method's whole job
            ended_at = self._clock()
            code = error_code_of(exc)
            message = safe_message(exc)
            outcome = StageOutcome(
                stage=stage,
                status=StageStatus.FAILED,
                correlation_id=stage_cid,
                started_at=started_at,
                ended_at=ended_at,
                error_code=code,
                message=message,
            )
            self._log(stage, "error", stage_cid, code, message, level=logging.ERROR)
            return StageExecution(outcome=outcome, payload=None)

        ended_at = self._clock()
        status = report.status or (
            StageStatus.PARTIAL if report.failed_items > 0 else StageStatus.COMPLETED
        )
        outcome = StageOutcome(
            stage=stage,
            status=status,
            correlation_id=stage_cid,
            started_at=started_at,
            ended_at=ended_at,
            error_code=None,
            message=report.message,
            detail=report.detail,
            item_count=report.item_count,
            failed_items=report.failed_items,
            attempts=report.attempts,
        )
        self._log(
            stage,
            "done",
            stage_cid,
            None,
            report.detail or "stage complete",
            extra_status=str(status),
        )
        return StageExecution(outcome=outcome, payload=payload)

    @staticmethod
    def _log(
        stage: StageNumber,
        status: str,
        correlation_id: str,
        error_code: str | None,
        message: str,
        *,
        level: int = logging.INFO,
        extra_status: str | None = None,
    ) -> None:
        log.log(
            level,
            "%s %s: %s",
            stage.value,
            status,
            message,
            extra={
                "stage": stage.value,
                "status": extra_status or status,
                "correlation_id": correlation_id,
                "error_code": error_code,
            },
        )

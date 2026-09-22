"""Result types for pipeline stages.

Stages must record status, not just outcomes (PROJECT_RULES #16, ``docs/04`` pipeline-spec).
A ``StageResult`` is the small, typed unit every stage returns so failures are explicit and
never silently swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tender_intelligence.core.errors import is_valid_error_code


@dataclass(frozen=True)
class StageResult:
    """Outcome of a single pipeline stage.

    Attributes:
        stage: Pipeline stage name (e.g. ``source_fetch``, ``document_download``).
        status: One of ``success``, ``failed``, ``partial``, ``skipped``.
        correlation_id: Correlation ID the stage ran under.
        error_code: One of the codes in ``core.errors`` when ``status == failed``.
        message: Human-readable summary (never contains secrets).
        details: Extra structured context for logging (values must be scrub-safe).
    """

    stage: str
    status: str
    correlation_id: str | None = None
    error_code: str | None = None
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in {"success", "failed", "partial", "skipped"}:
            raise ValueError(f"invalid stage status: {self.status!r}")
        if self.status == "failed" and not is_valid_error_code(self.error_code or ""):
            raise ValueError(
                f"failed stage requires a valid error_code; got {self.error_code!r}"
            )
        if self.status != "failed" and self.error_code is not None:
            raise ValueError("error_code is only allowed on failed stages")

    @property
    def ok(self) -> bool:
        return self.status == "success"

    @classmethod
    def success(
        cls,
        stage: str,
        correlation_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> StageResult:
        return cls(
            stage=stage,
            status="success",
            correlation_id=correlation_id,
            message=message,
            details=details or {},
        )

    @classmethod
    def failed(
        cls,
        stage: str,
        error_code: str,
        correlation_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> StageResult:
        return cls(
            stage=stage,
            status="failed",
            correlation_id=correlation_id,
            error_code=error_code,
            message=message,
            details=details or {},
        )

    @classmethod
    def partial(
        cls,
        stage: str,
        correlation_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> StageResult:
        return cls(
            stage=stage,
            status="partial",
            correlation_id=correlation_id,
            message=message,
            details=details or {},
        )

    @classmethod
    def skipped(
        cls,
        stage: str,
        correlation_id: str | None = None,
        message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> StageResult:
        return cls(
            stage=stage,
            status="skipped",
            correlation_id=correlation_id,
            message=message,
            details=details or {},
        )

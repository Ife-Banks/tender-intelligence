"""Alert seam for run-level failures (prompt 10 §16).

Prompt 10 §16 scopes this deliberately narrow: the orchestrator must expose *the smallest
clean seam* through which a failed run can be reported, and nothing more. Transport,
templating, recipients, deduplication of alerts, retry and persistence all belong to
prompt 12 (audit/alerting) and prompt 11 (email), and must not be anticipated here.

So this module contains a notice value object, a one-method protocol, and a null
implementation that does nothing. The worker receives an :class:`AlertHook` by injection and
defaults to :class:`NullAlertHook`; wiring a real one is a later prompt's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class AlertNotice:
    """A run-level failure worth telling someone about (prompt 10 §16).

    Carries identifiers and a machine-readable code only. It must never carry secrets, raw
    document contents, or configuration values (prompt 10 §3, §10).
    """

    source_id: int
    source_name: str
    correlation_id: str
    error_code: str
    stage: str | None = None
    run_history_id: int | None = None
    message: str = ""


@runtime_checkable
class AlertHook(Protocol):
    """One method, one argument, no transport (prompt 10 §16)."""

    def notify(self, notice: AlertNotice) -> None:
        """Report a failed run. Implementations own their own failure handling."""


class NullAlertHook:
    """The default: accept the notice and do nothing.

    Explicit rather than ``None`` so the coordinator has no branch to forget, and so a test
    can assert that a run *would* have alerted without a transport existing yet.
    """

    def __init__(self) -> None:
        self.notices: list[AlertNotice] = []

    def notify(self, notice: AlertNotice) -> None:
        self.notices.append(notice)

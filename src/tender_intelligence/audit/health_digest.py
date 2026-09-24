"""Daily health digest builder (Prompt 12 §27).

Constructs daily health summary from persisted state and queues via Prompt 11.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.alerts import AlertEvent
from tender_intelligence.db.models.mail import NotificationLog
from tender_intelligence.db.models.runs import RunHistory


@dataclass(frozen=True)
class HealthSummary:
    """Daily health digest data (Prompt 12 §27).

    Does NOT contain secrets or full document text.
    """

    period_start: datetime
    period_end: datetime
    total_runs: int
    successful_runs: int
    failed_runs: int
    source_failures: list[dict]
    parser_mismatches: int
    ai_failures: int
    email_failures: int
    breaker_states: list[dict]
    recovery_events: int
    new_open_alerts: int


class HealthDigestBuilder:
    """Constructs daily health digest from persisted records (Prompt 12 §27)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def build_digest(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> HealthSummary:
        """Build health digest for specified period (defaults to last 24 hours).

        Returns:
            HealthSummary with aggregated health metrics.
        """
        if end is None:
            end = datetime.now()
        if start is None:
            start = end - timedelta(hours=24)

        # Aggregate RunHistory
        runs = self.session.scalars(
            select(RunHistory).where(
                RunHistory.started_at >= start, RunHistory.started_at < end
            )
        ).all()

        total_runs = len(runs)
        successful_runs = sum(1 for r in runs if r.ended_at and r.error_count == 0)
        failed_runs = sum(1 for r in runs if r.error_count > 0 or r.failed_stage)

        # Source failures
        source_failures = []
        for run in runs:
            if run.error_count > 0 or run.failed_stage:
                source_failures.append(
                    {
                        "source_id": run.source_id,
                        "run_id": run.id,
                        "failed_stage": run.failed_stage,
                        "error_code": run.error_code,
                        "started_at": run.started_at.isoformat(),
                    }
                )

        # Alert aggregations
        alerts = self.session.scalars(
            select(AlertEvent).where(
                AlertEvent.first_raised_at >= start, AlertEvent.first_raised_at < end
            )
        ).all()

        parser_mismatches = sum(1 for a in alerts if a.type == "parser_mismatch")
        ai_failures = sum(
            1 for a in alerts if a.type in ("ai_failure", "invalid_ai_output")
        )

        recovery_events = sum(1 for a in alerts if a.state == "recovered")
        new_open_alerts = sum(1 for a in alerts if a.state == "open")

        # Email failures
        email_failures = self.session.scalar(
            select(func.count(NotificationLog.id)).where(
                NotificationLog.created_at >= start,
                NotificationLog.created_at < end,
                NotificationLog.status == "failed",
            )
        )

        # Breaker states (placeholder - would query provider health state)
        breaker_states = []

        return HealthSummary(
            period_start=start,
            period_end=end,
            total_runs=total_runs,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            source_failures=source_failures,
            parser_mismatches=parser_mismatches,
            ai_failures=ai_failures,
            email_failures=email_failures or 0,
            breaker_states=breaker_states,
            recovery_events=recovery_events,
            new_open_alerts=new_open_alerts,
        )

    def format_digest_text(self, summary: HealthSummary) -> str:
        """Format health summary as plain text for email body.

        Returns:
            Plain text digest content (no secrets, no raw document text).
        """
        lines = [
            "Daily Health Digest",
            "=" * 60,
            f"Period: {summary.period_start.strftime('%Y-%m-%d %H:%M')} to "
            f"{summary.period_end.strftime('%Y-%m-%d %H:%M')}",
            "",
            "Run Summary:",
            f"  Total runs: {summary.total_runs}",
            f"  Successful: {summary.successful_runs}",
            f"  Failed: {summary.failed_runs}",
            "",
        ]

        if summary.source_failures:
            lines.append("Source Failures:")
            for failure in summary.source_failures[:10]:  # Limit to first 10
                lines.append(
                    f"  - Source {failure['source_id']}, Run {failure['run_id']}: "
                    f"{failure['failed_stage']} ({failure['error_code']})"
                )
            if len(summary.source_failures) > 10:
                lines.append(f"  ... and {len(summary.source_failures) - 10} more")
            lines.append("")

        if summary.parser_mismatches > 0:
            lines.append(f"Parser Mismatches: {summary.parser_mismatches}")
        if summary.ai_failures > 0:
            lines.append(f"AI Failures: {summary.ai_failures}")
        if summary.email_failures > 0:
            lines.append(f"Email Failures: {summary.email_failures}")

        if summary.recovery_events > 0:
            lines.append(f"\nRecoveries: {summary.recovery_events}")

        if summary.new_open_alerts > 0:
            lines.append(f"New Open Alerts: {summary.new_open_alerts}")

        lines.append("")
        lines.append("End of Digest")

        return "\n".join(lines)

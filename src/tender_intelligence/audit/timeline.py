"""Timeline reconstruction service (Prompt 12 §4–§9).

Read-only query that reconstructs tender lifecycle from persisted events. Never invents,
infers, or synthesizes events that were not actually persisted (Prompt 12 central invariant).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.mail import NotificationAttempt, NotificationLog
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.triage import TriageResult
from tender_intelligence.db.models.verdicts import Verdict


@dataclass(frozen=True)
class TimelineEvent:
    """One reconstructed event from persisted state (Prompt 12 §4).

    Carries only what was actually recorded, never fabricated state.
    """

    timestamp: datetime
    stage: str
    status: str
    correlation_id: str | None = None
    tender_id: int | None = None
    run_id: int | None = None
    source: str | None = None
    document_ids: list[int] | None = None
    processing_info: dict[str, Any] | None = None
    incomplete_inputs: bool = False
    notification_id: int | None = None
    provider_used: str | None = None
    notification_status: str | None = None
    failure_code: str | None = None
    alert_ids: list[int] | None = None
    # Secondary ordering (Prompt 12 §5)
    event_id: str | None = None
    sequence: int = 0


@dataclass(frozen=True)
class TimelineQuery:
    """Timeline reconstruction result (Prompt 12 §4)."""

    correlation_id: str
    events: list[TimelineEvent]
    tender_id: int | None = None
    run_ids: list[int] | None = None


class TimelineService:
    """Read-only timeline reconstruction (Prompt 12 §4–§9).

    Queries persisted records and reconstructs the lifecycle. Does NOT:
    - mutate tender state
    - create events
    - create audit rows
    - create notifications
    - trigger retries
    - trigger alerts
    - update timestamps
    - modify RunHistory
    - modify NotificationLog
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def reconstruct_by_correlation(self, correlation_id: str) -> TimelineQuery:
        """Reconstruct tender lifecycle from persisted facts (Prompt 12 §4).

        Returns:
            Timeline with events in deterministic order (timestamp primary,
            event_id + sequence secondary for deterministic tie-breaking).
        """
        events: list[TimelineEvent] = []
        tender_id: int | None = None
        run_ids: list[int] = []

        # 1. Find Tender (Prompt 06 persisted state)
        tender = self.session.scalars(
            select(Tender).where(Tender.correlation_id == correlation_id)
        ).first()

        if tender:
            tender_id = tender.id
            # Event: seen/discovered
            events.append(
                TimelineEvent(
                    timestamp=tender.first_seen_at,
                    stage="discovery",
                    status="seen",
                    correlation_id=correlation_id,
                    tender_id=tender.id,
                    source=tender.source.name if tender.source else None,
                    event_id=f"tender-{tender.id}",
                    sequence=0,
                )
            )

            # Prompt 12.1 — deadline resolution event (when migration 0010 row exists)
            if tender.deadline_resolution is not None:
                dr = tender.deadline_resolution
                events.append(
                    TimelineEvent(
                        timestamp=dr.deadline_resolved_at,
                        stage="deadline_resolution",
                        status=str(dr.deadline_source or "unresolved"),
                        correlation_id=correlation_id,
                        tender_id=tender.id,
                        processing_info={
                            "deadline_utc": (
                                dr.deadline_resolved.isoformat()
                                if dr.deadline_resolved is not None
                                else None
                            ),
                            "deadline_source": dr.deadline_source,
                            "evidence": dict(dr.deadline_evidence or {}),
                        },
                        event_id=f"deadline-{tender.id}",
                        sequence=0,
                    )
                )

            # 2. Find Documents (Prompt 08 persisted state)
            documents = self.session.scalars(
                select(Document)
                .where(Document.tender_id == tender.id)
                .order_by(Document.created_at)
            ).all()

            for seq, doc in enumerate(documents, start=1):
                # Document discovery event
                events.append(
                    TimelineEvent(
                        timestamp=doc.created_at,
                        stage="document_discovery",
                        status="discovered",
                        correlation_id=correlation_id,
                        tender_id=tender.id,
                        document_ids=[doc.id],
                        processing_info={"filename": doc.filename, "url": doc.source_url},
                        event_id=f"doc-discovery-{doc.id}",
                        sequence=seq,
                    )
                )

                # Document acquisition event (if attempted)
                if doc.download_status != "pending":
                    events.append(
                        TimelineEvent(
                            timestamp=doc.updated_at,
                            stage="document_acquisition",
                            status=doc.download_status,
                            correlation_id=correlation_id,
                            tender_id=tender.id,
                            document_ids=[doc.id],
                            processing_info={
                                "filename": doc.filename,
                                "storage_path": doc.storage_path,
                                "mime_type": doc.mime_type,
                            },
                            event_id=f"doc-acquire-{doc.id}",
                            sequence=seq,
                        )
                    )

                # Document processing event (if attempted)
                if doc.extraction_status != "pending":
                    events.append(
                        TimelineEvent(
                            timestamp=doc.updated_at,
                            stage="document_processing",
                            status=doc.extraction_status,
                            correlation_id=correlation_id,
                            tender_id=tender.id,
                            document_ids=[doc.id],
                            processing_info={
                                "filename": doc.filename,
                                "language": doc.language,
                                "extraction_error": doc.extraction_error_code,
                            },
                            failure_code=doc.extraction_error_code,
                            event_id=f"doc-process-{doc.id}",
                            sequence=seq,
                        )
                    )

            # 3. Find Verdict (Prompt 14 persisted state - future)
            verdict = self.session.scalars(
                select(Verdict).where(Verdict.tender_id == tender.id).order_by(Verdict.generated_at)
            ).first()

            if verdict:
                # Verdict generation event
                events.append(
                    TimelineEvent(
                        timestamp=verdict.generated_at,
                        stage="verdict",
                        status="completed",
                        correlation_id=correlation_id,
                        tender_id=tender.id,
                        processing_info={
                            "recommendation": verdict.recommendation,
                            "confidence": verdict.confidence,
                            "urgency": verdict.urgency_flag,
                            "model": verdict.model,
                        },
                        incomplete_inputs=verdict.incomplete_inputs,
                        event_id=f"verdict-{verdict.id}",
                        sequence=0,
                    )
                )

            triage_results = self.session.scalars(
                select(TriageResult)
                .where(TriageResult.tender_id == tender.id)
                .order_by(TriageResult.created_at)
            ).all()
            for seq, triage in enumerate(triage_results, start=1):
                events.append(
                    TimelineEvent(
                        timestamp=triage.created_at,
                        stage="triage",
                        status=triage.status,
                        correlation_id=triage.correlation_id,
                        tender_id=tender.id,
                        run_id=triage.run_id,
                        processing_info={
                            "score": triage.score,
                            "mode": triage.mode,
                            "reasons": list(triage.reasons or []),
                            "model": triage.model,
                        },
                        failure_code=triage.error_code,
                        event_id=f"triage-{triage.id}",
                        sequence=seq,
                    )
                )

            # 4. Find Notifications (Prompt 11 persisted state)
            notifications = self.session.scalars(
                select(NotificationLog)
                .where(NotificationLog.tender_id == tender.id)
                .order_by(NotificationLog.created_at)
            ).all()

            for seq, notif in enumerate(notifications, start=1):
                # Notification creation event
                events.append(
                    TimelineEvent(
                        timestamp=notif.created_at,
                        stage="notification",
                        status=notif.status,
                        correlation_id=notif.correlation_id or correlation_id,
                        tender_id=tender.id,
                        notification_id=notif.id,
                        provider_used=notif.provider_used,
                        notification_status=notif.status,
                        processing_info={
                            "kind": notif.notification_kind,
                            "recipients_count": len(notif.recipients_snapshot or []),
                            "attachments_count": len(notif.attachments or []),
                            "links_count": len(notif.links or []),
                            "possible_duplicate": notif.possible_duplicate,
                        },
                        failure_code=notif.last_error_code,
                        event_id=f"notif-{notif.id}",
                        sequence=seq,
                    )
                )

                # Find notification attempts
                attempts = self.session.scalars(
                    select(NotificationAttempt)
                    .where(NotificationAttempt.notification_id == notif.id)
                    .order_by(NotificationAttempt.attempted_at)
                ).all()

                for att_seq, attempt in enumerate(attempts, start=1):
                    events.append(
                        TimelineEvent(
                            timestamp=attempt.attempted_at,
                            stage="notification_attempt",
                            status=attempt.status,
                            correlation_id=attempt.correlation_id or correlation_id,
                            tender_id=tender.id,
                            notification_id=notif.id,
                            provider_used=attempt.provider_name,
                            notification_status=attempt.status,
                            processing_info={
                                "attempt_number": attempt.attempt_number,
                                "duration_ms": attempt.duration_ms,
                                "provider_message_id": attempt.provider_message_id,
                            },
                            failure_code=attempt.error_code,
                            event_id=f"notif-attempt-{attempt.id}",
                            sequence=att_seq,
                        )
                    )

        # 5. Find RunHistory records (Prompt 10 persisted state)
        runs = self.session.scalars(
            select(RunHistory)
            .where(RunHistory.correlation_id == correlation_id)
            .order_by(RunHistory.started_at)
        ).all()

        for run in runs:
            run_ids.append(run.id)
            # Run start event
            events.append(
                TimelineEvent(
                    timestamp=run.started_at,
                    stage="run",
                    status="started",
                    correlation_id=run.correlation_id,
                    run_id=run.id,
                    source=f"source-{run.source_id}",
                    processing_info={
                        "config_version": run.config_version,
                    },
                    event_id=f"run-start-{run.id}",
                    sequence=0,
                )
            )

            # Run end event (if completed)
            if run.ended_at:
                run_status = "errored" if run.error_count > 0 or run.failed_stage else "completed"
                events.append(
                    TimelineEvent(
                        timestamp=run.ended_at,
                        stage="run",
                        status=run_status,
                        correlation_id=run.correlation_id,
                        run_id=run.id,
                        source=f"source-{run.source_id}",
                        processing_info={
                            "listings_found": run.listings_found,
                            "new_count": run.new_count,
                            "update_count": run.update_count,
                            "unchanged_count": run.unchanged_count,
                            "error_count": run.error_count,
                            "failed_stage": run.failed_stage,
                            "stages": run.stages,
                        },
                        failure_code=run.error_code,
                        event_id=f"run-end-{run.id}",
                        sequence=0,
                    )
                )

        # Sort events: primary by timestamp, secondary by (event_id, sequence)
        # This ensures deterministic ordering even with identical timestamps (Prompt 12 §5)
        # Handle both timezone-aware and timezone-naive datetimes
        def sort_key(e: TimelineEvent) -> tuple:
            ts = e.timestamp
            # Normalize to UTC if naive
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            return (ts, e.event_id or "", e.sequence)

        events.sort(key=sort_key)

        return TimelineQuery(
            correlation_id=correlation_id,
            events=events,
            tender_id=tender_id,
            run_ids=run_ids if run_ids else None,
        )

    def reconstruct_by_tender(self, tender_id: int) -> TimelineQuery | None:
        """Reconstruct timeline by tender ID (convenience method)."""
        tender = self.session.get(Tender, tender_id)
        if not tender:
            return None
        return self.reconstruct_by_correlation(tender.correlation_id)

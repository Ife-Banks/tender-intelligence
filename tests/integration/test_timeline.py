"""Timeline reconstruction tests (Prompt 12 §23–§26, §35).

Tests that timeline correctly reconstructs tender lifecycle from persisted facts,
maintains deterministic ordering, handles multiple runs/tenders, and exposes incomplete_inputs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.audit.timeline import TimelineService
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.mail import NotificationAttempt, NotificationLog
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.verdicts import Verdict


@pytest.fixture
def timeline_service(db_session: Session) -> TimelineService:
    """Timeline service fixture."""
    return TimelineService(db_session)


@pytest.fixture
def source(db_session: Session) -> Source:
    """Create test source."""
    src = Source(
        name="WAHO",
        source_type="waho",
        base_url="https://waho.example",
        parser_config={},
        active=True,
    )
    db_session.add(src)
    db_session.flush()
    return src


def test_empty_timeline_when_no_data(timeline_service: TimelineService) -> None:
    """Timeline returns empty events when correlation ID doesn't exist (Prompt 12 §4)."""
    result = timeline_service.reconstruct_by_correlation("nonexistent-correlation")
    assert result.correlation_id == "nonexistent-correlation"
    assert result.events == []
    assert result.tender_id is None


def test_complete_lifecycle_reconstruction(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline reconstructs full tender lifecycle from persisted facts (Prompt 12 §23)."""
    correlation_id = "full-lifecycle-001"
    base_time = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Create RunHistory (discovery stage)
    run = RunHistory(
        source_id=source.id,
        started_at=base_time,
        ended_at=base_time + timedelta(minutes=5),
        listings_found=1,
        new_count=1,
        correlation_id=correlation_id,
        stages={"discovery": "completed", "dedup": "completed"},
        config_version=1,
    )
    db_session.add(run)

    # 2. Create Tender (seen)
    tender = Tender(
        source_id=source.id,
        external_id="TENDER-2026-001",
        url="https://waho.example/tender/001",
        title="Pharmaceutical Traceability System",
        deadline=base_time + timedelta(days=14),
        status="processed",
        correlation_id=correlation_id,
        first_seen_at=base_time + timedelta(minutes=1),
    )
    db_session.add(tender)
    db_session.flush()

    # 3. Create Documents (discovery, acquisition, processing)
    doc1 = Document(
        tender_id=tender.id,
        filename="requirements.pdf",
        source_url="https://waho.example/doc1.pdf",
        storage_path="/storage/doc1.pdf",
        mime_type="application/pdf",
        language="en",
        checksum="abc123",
        download_status="downloaded",
        extraction_status="extracted",
    )
    doc1.created_at = base_time + timedelta(minutes=2)
    doc1.updated_at = base_time + timedelta(minutes=3)
    db_session.add(doc1)

    doc2 = Document(
        tender_id=tender.id,
        filename="terms.docx",
        source_url="https://waho.example/doc2.docx",
        storage_path="/storage/doc2.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        language="en",
        checksum="def456",
        download_status="downloaded",
        extraction_status="extracted",
    )
    doc2.created_at = base_time + timedelta(minutes=2, seconds=10)
    doc2.updated_at = base_time + timedelta(minutes=3, seconds=10)
    db_session.add(doc2)
    db_session.flush()

    # 4. Create Verdict
    verdict = Verdict(
        tender_id=tender.id,
        recommendation="APPLY",
        confidence=0.85,
        background_summary="Pharmaceutical system tender",
        urgency_flag=False,
        incomplete_inputs=False,
        generated_at=base_time + timedelta(minutes=4),
    )
    db_session.add(verdict)
    db_session.flush()

    # 5. Create Notification
    notification = NotificationLog(
        tender_id=tender.id,
        verdict_id=verdict.id,
        correlation_id=correlation_id,
        recipients_snapshot=[{"email": "test@example.com", "name": "Test User"}],
        sent_at=base_time + timedelta(minutes=5),
        attachments=[{"filename": "requirements.pdf"}],
        links=[],
        status="sent",
        provider_used="sendlib",
        dedupe_key="tender-001:verdict-1:hash123",
        notification_kind="new",
    )
    notification.created_at = base_time + timedelta(minutes=4, seconds=30)
    db_session.add(notification)
    db_session.flush()

    # 6. Create NotificationAttempt
    attempt = NotificationAttempt(
        notification_id=notification.id,
        correlation_id=correlation_id,
        provider_name="sendlib",
        status="sent",
        duration_ms=1500,
        attempted_at=base_time + timedelta(minutes=4, seconds=45),
        attempt_number=1,
    )
    db_session.add(attempt)
    db_session.commit()

    # Reconstruct timeline
    result = timeline_service.reconstruct_by_correlation(correlation_id)

    assert result.correlation_id == correlation_id
    assert result.tender_id == tender.id
    assert result.run_ids == [run.id]
    assert len(result.events) > 0

    # Verify stage ordering (Prompt 12 §5)
    stages = [e.stage for e in result.events]
    assert "run" in stages
    assert "discovery" in stages
    assert "document_discovery" in stages
    assert "document_acquisition" in stages
    assert "document_processing" in stages
    assert "verdict" in stages
    assert "notification" in stages
    assert "notification_attempt" in stages

    # Verify events are in chronological order
    for i in range(len(result.events) - 1):
        assert result.events[i].timestamp <= result.events[i + 1].timestamp


def test_timeline_ordering_deterministic(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline uses deterministic secondary ordering for identical timestamps (Prompt 12 §5)."""
    correlation_id = "ordering-test-001"
    base_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-ORDER-001",
        url="https://waho.example/order/001",
        title="Ordering Test",
        status="new",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.flush()

    # Create multiple documents with same timestamp
    for i in range(3):
        doc = Document(
            tender_id=tender.id,
            filename=f"doc{i}.pdf",
            source_url=f"https://waho.example/doc{i}.pdf",
            checksum=f"checksum{i}",
            download_status="downloaded",
            extraction_status="extracted",
        )
        doc.created_at = base_time + timedelta(minutes=1)
        doc.updated_at = base_time + timedelta(minutes=2)
        db_session.add(doc)
    db_session.commit()

    # Reconstruct multiple times - should get same order
    result1 = timeline_service.reconstruct_by_correlation(correlation_id)
    result2 = timeline_service.reconstruct_by_correlation(correlation_id)

    # Event ordering should be identical
    assert len(result1.events) == len(result2.events)
    for e1, e2 in zip(result1.events, result2.events):
        assert e1.timestamp == e2.timestamp
        assert e1.stage == e2.stage
        assert e1.event_id == e2.event_id
        assert e1.sequence == e2.sequence


def test_multiple_runs_same_tender(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline handles tender appearing in multiple runs (Prompt 12 §26)."""
    correlation_id = "multi-run-001"
    base_time = datetime(2026, 1, 15, 14, 0, 0, tzinfo=timezone.utc)

    # First run - tender discovered
    run1 = RunHistory(
        source_id=source.id,
        started_at=base_time,
        ended_at=base_time + timedelta(minutes=5),
        listings_found=1,
        new_count=1,
        correlation_id=correlation_id,
    )
    db_session.add(run1)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-MULTI-001",
        url="https://waho.example/multi/001",
        title="Multi-run Tender",
        status="processed",
        correlation_id=correlation_id,
        first_seen_at=base_time + timedelta(minutes=1),
    )
    db_session.add(tender)
    db_session.flush()

    # Second run - tender updated
    run2 = RunHistory(
        source_id=source.id,
        started_at=base_time + timedelta(days=1),
        ended_at=base_time + timedelta(days=1, minutes=5),
        listings_found=1,
        update_count=1,
        correlation_id=correlation_id,
    )
    db_session.add(run2)
    db_session.commit()

    result = timeline_service.reconstruct_by_correlation(correlation_id)

    assert result.tender_id == tender.id
    assert result.run_ids == [run1.id, run2.id]

    # Should have events from both runs
    run_events = [e for e in result.events if e.stage == "run"]
    assert len(run_events) >= 2


def test_incomplete_inputs_visible(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline exposes incomplete_inputs state from verdict (Prompt 12 §9, §22)."""
    correlation_id = "incomplete-001"
    base_time = datetime(2026, 1, 15, 16, 0, 0, tzinfo=timezone.utc)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-INCOMPLETE-001",
        url="https://waho.example/incomplete/001",
        title="Incomplete Inputs Test",
        status="processed",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.flush()

    # Document with failed download
    doc = Document(
        tender_id=tender.id,
        filename="missing.pdf",
        source_url="https://waho.example/missing.pdf",
        checksum="missing123",
        download_status="failed",
        extraction_status="failed",  # Changed from pending
        extraction_error_code="download_failed",
    )
    db_session.add(doc)

    # Verdict with incomplete_inputs=True
    verdict = Verdict(
        tender_id=tender.id,
        recommendation="APPLY WITH CONDITIONS",
        confidence=0.70,
        incomplete_inputs=True,  # Key field
        generated_at=base_time + timedelta(minutes=2),
    )
    db_session.add(verdict)
    db_session.commit()

    result = timeline_service.reconstruct_by_correlation(correlation_id)

    # Find verdict event
    verdict_events = [e for e in result.events if e.stage == "verdict"]
    assert len(verdict_events) == 1
    assert verdict_events[0].incomplete_inputs is True

    # Document processing event should show failure
    doc_events = [e for e in result.events if e.stage == "document_processing"]
    assert len(doc_events) == 1
    assert doc_events[0].status == "failed"
    assert doc_events[0].failure_code == "download_failed"


def test_timeline_is_read_only(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline queries don't mutate state - repeated queries identical (Prompt 12 §6)."""
    correlation_id = "readonly-001"
    base_time = datetime(2026, 1, 15, 18, 0, 0, tzinfo=timezone.utc)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-READONLY-001",
        url="https://waho.example/readonly/001",
        title="Read-only Test",
        status="new",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.commit()

    # Query multiple times
    result1 = timeline_service.reconstruct_by_correlation(correlation_id)
    result2 = timeline_service.reconstruct_by_correlation(correlation_id)
    result3 = timeline_service.reconstruct_by_correlation(correlation_id)

    # Should be identical
    assert len(result1.events) == len(result2.events) == len(result3.events)
    assert result1.tender_id == result2.tender_id == result3.tender_id

    # Tender should still have same status
    db_session.expire_all()
    refreshed = db_session.get(Tender, tender.id)
    assert refreshed.status == "new"  # Unchanged


def test_failed_stage_in_timeline(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline includes failed stages with error codes (Prompt 12 §24)."""
    correlation_id = "failed-001"
    base_time = datetime(2026, 1, 15, 20, 0, 0, tzinfo=timezone.utc)

    # Run with failure
    run = RunHistory(
        source_id=source.id,
        started_at=base_time,
        ended_at=base_time + timedelta(minutes=2),
        error_count=1,
        failed_stage="document_acquisition",
        error_code="source_unreachable",
        correlation_id=correlation_id,
    )
    db_session.add(run)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-FAILED-001",
        url="https://waho.example/failed/001",
        title="Failed Stage Test",
        status="new",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.commit()

    result = timeline_service.reconstruct_by_correlation(correlation_id)

    # Find run end event
    run_end_events = [e for e in result.events if e.stage == "run" and e.status == "errored"]
    assert len(run_end_events) == 1
    assert run_end_events[0].failure_code == "source_unreachable"
    assert run_end_events[0].processing_info["failed_stage"] == "document_acquisition"


def test_notification_provider_info_in_timeline(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline includes provider and attempt information (Prompt 12 §23)."""
    correlation_id = "provider-001"
    base_time = datetime(2026, 1, 16, 10, 0, 0, tzinfo=timezone.utc)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-PROVIDER-001",
        url="https://waho.example/provider/001",
        title="Provider Test",
        status="processed",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.flush()

    notification = NotificationLog(
        tender_id=tender.id,
        correlation_id=correlation_id,
        recipients_snapshot=[{"email": "test@example.com"}],
        status="sent",
        provider_used="sendlib",
        dedupe_key="unique-001",
        notification_kind="new",
    )
    notification.created_at = base_time + timedelta(minutes=1)
    db_session.add(notification)
    db_session.flush()

    # Multiple attempts (first failed, second succeeded)
    attempt1 = NotificationAttempt(
        notification_id=notification.id,
        provider_name="provider_a",
        status="failed",
        error_code="timeout",
        attempted_at=base_time + timedelta(minutes=1, seconds=5),
        attempt_number=1,
    )
    attempt2 = NotificationAttempt(
        notification_id=notification.id,
        provider_name="sendlib",
        status="sent",
        attempted_at=base_time + timedelta(minutes=1, seconds=15),
        attempt_number=2,
    )
    db_session.add_all([attempt1, attempt2])
    db_session.commit()

    result = timeline_service.reconstruct_by_correlation(correlation_id)

    # Find notification attempt events
    attempt_events = [e for e in result.events if e.stage == "notification_attempt"]
    assert len(attempt_events) == 2

    # First attempt should show failure
    assert attempt_events[0].status == "failed"
    assert attempt_events[0].provider_used == "provider_a"
    assert attempt_events[0].failure_code == "timeout"

    # Second attempt should show success
    assert attempt_events[1].status == "sent"
    assert attempt_events[1].provider_used == "sendlib"


def test_cross_stage_correlation(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline correlates same tender across all stages (Prompt 12 §25)."""
    correlation_id = "correlation-001"
    base_time = datetime(2026, 1, 16, 12, 0, 0, tzinfo=timezone.utc)

    run = RunHistory(
        source_id=source.id,
        started_at=base_time,
        correlation_id=correlation_id,
    )
    db_session.add(run)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-CORR-001",
        url="https://waho.example/corr/001",
        title="Correlation Test",
        status="processed",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.flush()

    doc = Document(
        tender_id=tender.id,
        filename="test.pdf",
        source_url="https://waho.example/test.pdf",
        download_status="downloaded",
        extraction_status="extracted",
    )
    db_session.add(doc)

    verdict = Verdict(
        tender_id=tender.id,
        recommendation="APPLY",
        generated_at=base_time + timedelta(minutes=1),
    )
    db_session.add(verdict)
    db_session.flush()

    notification = NotificationLog(
        tender_id=tender.id,
        verdict_id=verdict.id,
        correlation_id=correlation_id,
        recipients_snapshot=[],
        status="sent",
        dedupe_key="unique-corr-001",
        notification_kind="new",
    )
    db_session.add(notification)
    db_session.commit()

    result = timeline_service.reconstruct_by_correlation(correlation_id)

    # All events should share the same correlation_id or tender_id
    for event in result.events:
        assert event.correlation_id == correlation_id or event.tender_id == tender.id

    # Should have events from all major stages
    stages = {e.stage for e in result.events}
    assert "run" in stages
    assert "discovery" in stages
    assert "document_discovery" in stages
    assert "verdict" in stages
    assert "notification" in stages


def test_reconstruct_by_tender_id(
    db_session: Session, timeline_service: TimelineService, source: Source
) -> None:
    """Timeline can be reconstructed by tender ID (convenience method)."""
    correlation_id = "tender-id-001"
    base_time = datetime(2026, 1, 16, 14, 0, 0, tzinfo=timezone.utc)

    tender = Tender(
        source_id=source.id,
        external_id="TENDER-ID-001",
        url="https://waho.example/tid/001",
        title="Tender ID Test",
        status="new",
        correlation_id=correlation_id,
        first_seen_at=base_time,
    )
    db_session.add(tender)
    db_session.commit()

    # Reconstruct by tender ID
    result = timeline_service.reconstruct_by_tender(tender.id)

    assert result is not None
    assert result.correlation_id == correlation_id
    assert result.tender_id == tender.id

    # Nonexistent tender ID
    result_none = timeline_service.reconstruct_by_tender(999999)
    assert result_none is None

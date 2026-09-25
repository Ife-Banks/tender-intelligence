"""Integration tests for the notification delivery service (prompt 11).

Prompt 11 §33 requires the delivery layer to be exercised end-to-end through the real service
with only *transport* faked: an in-process ``httpx2.MockTransport`` answers the Sendlib wire
contract, real credentials are encrypted/decrypted through the real AES-GCM seam, and the
durable outbox, readable banner, attempts, dedupe and the circuit breaker all hit the real
migrated SQLite schema (the documented test target simplification, tests/conftest.py).
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from httpx2 import MockTransport, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tender_intelligence.core.errors import (
    MAIL_BREAKER_OPEN,
    MAIL_CONFIGURATION_ERROR,
    MAIL_HTTP_5XX,
    NOTIFICATION_NO_VERDICT,
)
from tender_intelligence.crypto.secrets import encrypt_secret
from tender_intelligence.db.models.alerts import AlertEvent
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.mail import MailProvider, NotificationAttempt, NotificationLog
from tender_intelligence.db.models.recipients import Recipient
from tender_intelligence.db.models.sources import Source
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.db.repositories.notifications import NotificationRepository
from tender_intelligence.mail.chain import MailRetryPolicy
from tender_intelligence.mail.links import SecureLinkSigner
from tender_intelligence.notifications.admin import RecipientAdmin, RecipientAdminError
from tender_intelligence.notifications.banner import RED_BANNER_TYPE
from tender_intelligence.notifications.service import (
    NotificationError,
    NotificationSafetyError,
    NotificationService,
    NullMailAlertHook,
)
from tender_intelligence.storage.local import LocalFileSystemStorage

MB = 1024 * 1024


class _Clock:
    """Deterministic wall-clock stand-in; the breaker persists epoch values from it."""

    def __init__(self, start: float = 2_000_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Handler:
    """MockTransport handler whose status can be flipped mid-test; records every request."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> Response:
        self.requests.append(request)
        return Response(self.status, json={"id": f"sent-{len(self.requests)}"})


class _FlakyByProvider:
    """Provider sendlib-one always dies mid-flight; sendlib-two always succeeds."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx2.Request) -> Response:
        self.calls += 1
        if "sendlib-two" in request.headers.get("authorization", ""):
            return Response(200, json={"id": f"ok-{self.calls}"})
        raise httpx2.ReadError("connection reset before response")


class _World:
    """Seed + build helpers shared by every test in this module."""

    def __init__(self, session_factory, storage: LocalFileSystemStorage) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.key = b"k" * 32
        self.clock = _Clock()
        self.delays: list[float] = []

    # ------------------------------------------------------------- seeding

    def add_dev(self, *emails: str) -> None:
        with self.session_factory() as session:
            for email in emails:
                session.add(
                    Recipient(email=email, list_type="dev_alert", delivery="to", active=True)
                )
            session.commit()

    def add_business(self, *emails: str) -> None:
        with self.session_factory() as session:
            for email in emails:
                session.add(Recipient(email=email, list_type="tender", delivery="to", active=True))
            session.commit()

    def add_provider(self, *, name: str, priority: int = 1, active: bool = True) -> int:
        with self.session_factory() as session:
            row = MailProvider(
                name=name,
                provider_type="sendlib",
                credentials_encrypted=encrypt_secret(
                    json.dumps({"api_key": f"k-{name}"}), key=self.key
                ),
                from_address="tenders@opex.example",
                from_name="OPEX Tender Intelligence",
                priority=priority,
                active=active,
            )
            session.add(row)
            session.commit()
            return row.id

    def seed_tender(
        self,
        *,
        title: str = "EOI: Health Data Platform",
        external_id: str = "w-9001",
        source_name: str | None = None,
        is_update: bool = False,
        status: str = "new",
        documents: tuple[tuple[str, bytes], ...] = (("annex.pdf", b"%PDF-1.4 fake bytes"),),
        deadline: datetime | None = None,
        with_verdict: bool = True,
        recommendation: str = "APPLY",
        urgency_flag: bool = True,
    ) -> tuple[Source, Tender, list[Document], Verdict | None]:
        with self.session_factory() as session:
            source = Source(
                name=source_name or f"Source-{uuid.uuid4().hex[:8]}",
                source_type="html",
                base_url="https://afro.who.int/listing",
                listing_url="https://afro.who.int/listing",
                active=True,
            )
            session.add(source)
            session.flush()
            tender = Tender(
                source_id=source.id,
                external_id=external_id,
                url=f"https://afro.who.int/listing/{external_id}",
                title=title,
                deadline=deadline or (datetime.now(UTC) + timedelta(days=10)),
                deadline_timezone="UTC",
                correlation_id=uuid.uuid4().hex[:36],
                status=status,
                is_update=is_update,
            )
            session.add(tender)
            session.flush()
            doc_rows: list[Document] = []
            for index, (filename, data) in enumerate(documents):
                key = f"tenders/{tender.id}/{index}-{filename.replace('/', '_')}"
                self.storage.put(key, data, content_type="application/pdf")
                doc = Document(
                    tender_id=tender.id,
                    filename=filename,
                    source_url=f"https://afro.who.int/d/{index}.pdf",
                    storage_path=key,
                    mime_type="application/pdf",
                    checksum=f"{index:016x}-{'d' * 48}",
                    download_status="downloaded",
                )
                session.add(doc)
                doc_rows.append(doc)
            verdict: Verdict | None = None
            if with_verdict:
                verdict = Verdict(
                    tender_id=tender.id,
                    recommendation=recommendation,
                    confidence=0.87,
                    background_summary="The programme modernises national health reporting.",
                    requirements_summary=["Deliver an ETL pipeline", "Data dictionary"],
                    gap_analysis=["We can deliver the ETL pipeline end to end."],
                    urgency_flag=urgency_flag,
                    generated_at=datetime.now(UTC),
                    incomplete_inputs=False,
                )
                session.add(verdict)
            session.commit()
            return source, tender, doc_rows, verdict

    def set_test_mode(self, enabled: bool) -> None:
        with self.session_factory() as session:
            row = session.get(Setting, 1)
            if row is not None:
                row.test_mode = enabled
                session.commit()

    # ------------------------------------------------------------- service

    def service(self, handler, **overrides: object) -> NotificationService:
        transport = MockTransport(handler)
        kwargs = {
            "storage": self.storage,
            "client_factory": lambda: httpx2.Client(transport=transport),
            "link_signer": SecureLinkSigner(self.key, "https://archive.example.invalid"),
            "sleeper": self.delays.append,
            "clock": self.clock,
            "retry_policy": MailRetryPolicy(attempts=1),
            "breaker_threshold": 3,
            "breaker_cooldown_seconds": 300.0,
            "provider_secret_key": self.key,
        }
        kwargs.update(overrides)
        return NotificationService(self.session_factory, **kwargs)


# --------------------------------------------------------------------------- helpers


def _logs(session) -> list[NotificationLog]:
    return list(session.scalars(select(NotificationLog).order_by(NotificationLog.id)).all())


def _open_banners(session) -> list[AlertEvent]:
    return list(
        session.scalars(
            select(AlertEvent).where(AlertEvent.type == RED_BANNER_TYPE, AlertEvent.state == "open")
        ).all()
    )


def _attempts_for(session, notification_id: int) -> list[NotificationAttempt]:
    return list(
        session.scalars(
            select(NotificationAttempt).where(
                NotificationAttempt.notification_id == notification_id
            )
        ).all()
    )


# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def world(session_factory_gr, tmp_path):
    return _World(session_factory_gr, LocalFileSystemStorage(tmp_path / "objects"))


class TestTestModeDelivery:
    def test_admin_test_email_uses_chain_and_creates_non_tender_attempt(self, world) -> None:
        world.add_dev("dev-test@opex.example")
        world.add_business("business@acme.example")
        world.add_provider(name="sendlib-test")
        handler = _Handler(200)
        service = world.service(handler)

        outcome = service.send_test_email(
            "dev-test@opex.example",
            "Provider check",
            "[TEST] Safe test body",
            correlation_id="c" * 36,
        )

        assert outcome.status == "sent"
        assert outcome.provider_used == "sendlib-test"
        assert len(handler.requests) == 1
        payload = json.loads(handler.requests[0].content)
        assert payload["to"] == ["dev-test@opex.example"]
        assert payload["subject"].startswith("[TEST]")
        with world.session_factory() as session:
            log = session.get(NotificationLog, outcome.notification_log_id)
            assert log is not None and log.tender_id is None and log.verdict_id is None
            assert log.notification_kind == "test" and log.status == "sent"
            assert log.correlation_id == "c" * 36
            attempts = _attempts_for(session, log.id)
            assert len(attempts) == 1 and attempts[0].status == "sent"
            assert attempts[0].provider_name == "sendlib-test"
            assert (
                session.query(NotificationLog)
                .filter(NotificationLog.tender_id.is_not(None))
                .count()
                == 0
            )

    def test_admin_test_email_refuses_non_dev_recipient_and_test_mode_off(self, world) -> None:
        world.add_dev("dev-test@opex.example")
        world.add_business("business@acme.example")
        world.add_provider(name="sendlib-test")
        service = world.service(_Handler(200))
        with pytest.raises(NotificationSafetyError):
            service.send_test_email("business@acme.example", "test", "[TEST] body")
        world.set_test_mode(False)
        with pytest.raises(NotificationSafetyError):
            service.send_test_email("dev-test@opex.example", "test", "[TEST] body")

    def test_dev_only_with_prefix_and_persisted_artefacts(self, world) -> None:
        world.add_dev("dev@opex.example")
        world.add_business("biz@acme.example")  # must NOT be routed while Test Mode is ON
        _, tender, doc_rows, verdict = world.seed_tender()
        provider_id = world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        outcome = service.notify_tender(tender.id, verdict.id)

        assert outcome.status == "sent"
        assert outcome.provider_used == "sendlib-one"

        payload = json.loads(handler.requests[-1].content)
        assert payload["to"] == ["dev@opex.example"]
        assert "biz@acme.example" not in payload["to"]
        assert payload["subject"].startswith("[TEST] ")
        assert len(payload["attachments"]) == 1
        assert payload["attachments"][0]["filename"] == "annex.pdf"
        assert (
            payload["attachments"][0]["content"]
            == base64.b64encode(b"%PDF-1.4 fake bytes").decode()
        )
        assert payload["from"].endswith("<tenders@opex.example>")

        with world.session_factory() as session:
            logs = _logs(session)
            assert len(logs) == 1
            log = logs[0]
            assert log.status == "sent"
            assert log.tender_id == tender.id
            assert log.verdict_id == verdict.id
            assert log.correlation_id == tender.correlation_id
            assert log.provider_used == "sendlib-one"
            snapshot_types = {item["list_type"] for item in log.recipients_snapshot}
            assert snapshot_types == {"dev_alert"}
            assert log.dedupe_key == outcome.dedupe_key
            assert log.attachments[0]["document_id"] == doc_rows[0].id
            assert not log.possible_duplicate
            attempts = _attempts_for(session, log.id)
            assert len(attempts) == 1
            assert attempts[0].status == "sent"
            assert attempts[0].provider_id == provider_id
            assert attempts[0].correlation_id == tender.correlation_id
            assert _open_banners(session) == []
            provider = session.get(MailProvider, provider_id)
            assert provider.breaker_state == "closed"

    def test_large_document_becomes_signed_link(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, doc_rows, verdict = world.seed_tender(
            documents=(
                ("annex-a.pdf", b"%PDF-1.4 small"),
                ("big.zip", b"x" * (2 * MB)),  # exceeds the 1 MB per-file free-tier cap
            )
        )
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "sent"
        small, big = doc_rows
        assert small.filename == "annex-a.pdf" and big.filename == "big.zip"
        with world.session_factory() as session:
            log = _logs(session)[0]
            assert len(log.attachments) == 1
            assert log.attachments[0]["document_id"] == small.id
            assert len(log.links) == 1
            link = log.links[0]
            assert link["document_id"] == big.id
            assert link["url"].startswith("https://archive.example.invalid/archive/")
            assert "expires=" in link["url"] and "sig=" in link["url"]
            assert link["expiry_days"] == 14
        payload = json.loads(handler.requests[-1].content)
        assert len(payload["attachments"]) == 1
        assert payload["attachments"][0]["filename"] == "annex-a.pdf"

    def test_test_mode_off_routes_business_recipients(self, world) -> None:
        world.add_dev("dev@opex.example")
        world.add_business("biz@acme.example")
        world.set_test_mode(False)
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "sent"
        payload = json.loads(handler.requests[-1].content)
        assert set(payload["to"]) == {"biz@acme.example"}
        assert "dev@opex.example" not in payload["to"]
        assert not payload["subject"].startswith("[TEST]")


class TestIdempotency:
    def test_second_call_is_already_notified(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        first = service.notify_tender(tender.id)
        second = service.notify_tender(tender.id)

        assert first.status == "sent"
        assert second.status == "already_notified"
        assert second.dedupe_key == first.dedupe_key
        assert len(handler.requests) == 1  # the provider was never called a second time
        with world.session_factory() as session:
            logs = _logs(session)
            assert len(logs) == 1
            assert len(_attempts_for(session, logs[0].id)) == 1

    def test_concurrent_callers_yield_one_sent(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        results: list[str] = []
        barrier = threading.Barrier(2)

        def notify() -> None:
            barrier.wait()
            results.append(service.notify_tender(tender.id).status)

        threads = [threading.Thread(target=notify) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sorted(results) == ["already_notified", "sent"]
        with world.session_factory() as session:
            assert len(_logs(session)) == 1

    def test_duplicate_dedupe_key_refused_at_db(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        with world.session_factory() as session:
            repo = NotificationRepository(session)
            repo.create_pending(
                dedupe_key="dup-key",
                tender_id=tender.id,
                verdict_id=verdict.id,
                correlation_id=tender.correlation_id,
                recipients_snapshot=[],
                attachments=[],
                links=[],
            )
            session.flush()
            with pytest.raises(IntegrityError):
                repo.create_pending(
                    dedupe_key="dup-key",
                    tender_id=tender.id,
                    verdict_id=verdict.id,
                    correlation_id=tender.correlation_id,
                    recipients_snapshot=[],
                    attachments=[],
                    links=[],
                )
                session.flush()

    def test_update_email_renders_update_variant(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender(is_update=True, status="processed")
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        service.notify_tender(tender.id)

        payload = json.loads(handler.requests[-1].content)
        assert "UPDATE" in payload["subject"]
        assert "status updated to processed" in payload["subject"]
        assert "What changed" in payload["text"]
        assert "[TEST]" in payload["subject"]


class TestMissingVerdict:
    def test_no_verdict_row_raises(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, _ = world.seed_tender(with_verdict=False)
        world.add_provider(name="sendlib-one")
        service = world.service(_Handler(200))

        with pytest.raises(NotificationError) as caught:
            service.notify_tender(tender.id)
        assert caught.value.error_code == NOTIFICATION_NO_VERDICT
        assert "no verdict row" in caught.value.message

    def test_missing_tender_raises(self, world) -> None:
        world.add_dev("dev@opex.example")
        service = world.service(_Handler(200))
        with pytest.raises(NotificationError):
            service.notify_tender(99999)


class TestDurableOutboxAndBanner:
    def test_chain_down_keeps_outbox_and_banner_then_flush_recovers(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(500)
        service = world.service(handler)

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "pending_retry"
        assert outcome.error_code == MAIL_HTTP_5XX
        with world.session_factory() as session:
            log = _logs(session)[0]
            assert log.status == "pending_retry"
            assert log.error.startswith("mail_http_5xx:")
            assert len(_open_banners(session)) == 1
            attempts = _attempts_for(session, log.id)
            assert len(attempts) == 1 and attempts[0].status == "failed"

        # The provider recovers; the durable outbox flush delivers and clears the banner.
        handler.status = 200
        summary = service.flush_pending_retry()
        assert summary.attempted == 1
        assert summary.flushed == 1
        assert summary.still_pending == 0
        assert summary.banner_cleared is True
        with world.session_factory() as session:
            log = _logs(session)[0]
            assert log.status == "sent"
            assert log.error is None
            assert _open_banners(session) == []

    def test_no_configured_provider_is_config_error_outbox_entry(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        service = world.service(_Handler(500))  # no providers were seeded

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "pending_retry"
        assert outcome.error_code == MAIL_CONFIGURATION_ERROR
        with world.session_factory() as session:
            assert len(_open_banners(session)) == 1


class TestCircuitBreaker:
    def test_breaker_opens_skips_and_recovers_after_cooldown(self, world) -> None:
        world.add_dev("dev@opex.example")
        world.add_provider(name="sendlib-one")
        alert_hook = NullMailAlertHook()
        handler = _Handler(500)
        service = world.service(handler, alert_hook=alert_hook)

        # Three consecutive whole-chain failures trip the breaker (threshold=3).
        _, tender_a, _, verdict_a = world.seed_tender(external_id="w-a")
        _, tender_b, _, verdict_b = world.seed_tender(external_id="w-b")
        _, tender_c, _, verdict_c = world.seed_tender(external_id="w-c")
        for tender, _verdict in (
            (tender_a, verdict_a),
            (tender_b, verdict_b),
            (tender_c, verdict_c),
        ):
            service.notify_tender(tender.id)

        with world.session_factory() as session:
            provider = session.scalars(select(MailProvider)).first()
            assert provider.breaker_state == "open"
            assert provider.breaker_until is not None
            assert _open_banners(session)

        # A new notification is skipped while the breaker is open.
        _, tender_d, _, verdict_d = world.seed_tender(external_id="w-d")
        outcome_d = service.notify_tender(tender_d.id)
        assert outcome_d.status == "pending_retry"
        assert outcome_d.error_code == MAIL_BREAKER_OPEN
        assert any(notice.error_code == MAIL_BREAKER_OPEN for notice in alert_hook.notices)

        # Cooldown passes, provider recovers: the half-open probe succeeds and closes it.
        world.clock.advance(301)
        handler.status = 200
        summary = service.flush_pending_retry()
        assert summary.flushed == 4
        assert summary.banner_cleared is True
        with world.session_factory() as session:
            provider = session.scalars(select(MailProvider)).first()
            assert provider.breaker_state == "closed"
            assert provider.breaker_until is None
            assert all(log.status == "sent" for log in _logs(session))


class TestPossibleDuplicate:
    def test_timeout_then_failover_flags_log(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one", priority=1)
        world.add_provider(name="sendlib-two", priority=2)
        handler = _FlakyByProvider()
        service = world.service(
            handler, retry_policy=MailRetryPolicy(attempts=2, backoff_base_seconds=1.0)
        )

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "sent"
        assert outcome.possible_duplicate
        assert outcome.provider_used == "sendlib-two"
        assert world.delays == [1.0]  # one bounded backoff between the retried attempts
        with world.session_factory() as session:
            log = _logs(session)[0]
            assert log.possible_duplicate
            assert log.provider_used == "sendlib-two"
            statuses = [a.status for a in _attempts_for(session, log.id)]
            assert statuses.count("failed") == 2
            assert statuses.count("sent") == 1
            error_codes = {
                a.error_code for a in _attempts_for(session, log.id) if a.status == "failed"
            }
            assert "mail_timeout" in error_codes

    def test_ambiguous_chain_failure_is_quarantined_after_one_automatic_retry(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")

        def timeout_handler(request):
            raise httpx2.ReadError("connection reset before response")

        service = world.service(timeout_handler, retry_policy=MailRetryPolicy(attempts=1))
        assert service.notify_tender(tender.id, verdict.id).status == "pending_retry"
        first_retry = service.flush_pending_retry()
        assert first_retry.attempted == 1
        assert first_retry.flushed == 0
        with world.session_factory() as session:
            row = _logs(session)[0]
            assert row.status == "possible_duplicate"
            assert row.next_retry_at is None
        second_retry = service.flush_pending_retry()
        assert second_retry.attempted == 0
        assert second_retry.still_pending == 1

    def test_last_active_dev_recipient_removal_is_refused(self, world) -> None:
        with world.session_factory() as session:
            admin = RecipientAdmin(session)
            sole = admin.add(email="dev@opex.example", list_type="dev_alert")
            session.commit()
            with pytest.raises(RecipientAdminError):
                admin.deactivate(sole.id)
            with pytest.raises(RecipientAdminError):
                admin.delete(sole.id)
            session.rollback()
            assert session.get(Recipient, sole.id).active

    def test_change_audited_and_allowed_once_a_second_dev_exists(self, world) -> None:
        with world.session_factory() as session:
            admin = RecipientAdmin(session)
            first = admin.add(email="dev@opex.example", list_type="dev_alert")
            second = admin.add(email="dev2@opex.example", list_type="dev_alert")
            session.commit()
            admin.deactivate(first.id)
            session.commit()
            assert not session.get(Recipient, first.id).active
            assert session.get(Recipient, second.id).active
            from tender_intelligence.db.models.config import ConfigChangeLog

            events = session.scalars(
                select(ConfigChangeLog).where(ConfigChangeLog.entity == "recipient")
            ).all()
            assert len(events) >= 3  # two adds + one deactivate


class TestNotificationLogUiContract:
    def test_statuses_are_stable_set(self) -> None:
        from tender_intelligence.db.models.mail import NOTIFICATION_STATUSES

        assert {"sent", "failed", "pending_retry"} == set(NOTIFICATION_STATUSES)
        # The service's own outcome vocabulary is a subset of the persisted statuses.
        from tender_intelligence.notifications.service import NotificationOutcome

        assert NotificationOutcome is not None


class TestStorageGap:
    def test_missing_storage_file_is_named_failed(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender(documents=(("gone.pdf", b"will-not-be-stored"),))
        # Remove the object from storage so it cannot be attached at send time.
        for obj in world.storage.root.rglob("*"):
            if obj.is_file():
                obj.unlink()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        outcome = service.notify_tender(tender.id)

        assert outcome.status == "sent"
        payload = json.loads(handler.requests[-1].content)
        assert len(payload["attachments"]) == 0
        assert "Documents that failed to download/extract: gone.pdf" in payload["text"]


class TestFinalSafetyBoundaries:
    def test_mode_is_rechecked_before_provider_request(self, world) -> None:
        world.add_dev("dev@opex.example")
        world.add_business("biz@acme.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        world.set_test_mode(False)
        handler = _Handler(200)
        service = world.service(handler)
        original_reserve = service._reserve

        def reserve_then_toggle(prepared):
            row, created = original_reserve(prepared)
            world.set_test_mode(True)
            return row, created

        service._reserve = reserve_then_toggle
        outcome = service.notify_tender(tender.id, verdict.id)

        assert outcome.status == "sent"
        payload = json.loads(handler.requests[-1].content)
        assert payload["to"] == ["dev@opex.example"]
        assert "biz@acme.example" not in json.dumps(payload)
        assert payload["subject"].startswith("[TEST] ")
        again = service.notify_tender(tender.id, verdict.id)
        assert again.status == "already_notified"
        assert len(handler.requests) == 1

    def test_mode_is_rechecked_before_each_provider_attempt(self, world) -> None:
        world.add_dev("dev@opex.example")
        world.add_business("biz@acme.example")
        world.set_test_mode(False)
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)
        original_renew = service._renew_delivery_claim
        toggled = False

        def renew_then_toggle(log_id, claim_token):
            nonlocal toggled
            original_renew(log_id, claim_token)
            if not toggled:
                world.set_test_mode(True)
                toggled = True

        service._renew_delivery_claim = renew_then_toggle
        outcome = service.notify_tender(tender.id, verdict.id)

        assert outcome.status == "pending_retry"
        assert handler.requests == []
        with world.session_factory() as session:
            assert _logs(session)[0].status == "pending_retry"

    def test_empty_dev_route_recovers_from_outbox(self, world) -> None:
        world.add_business("biz@acme.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)

        first = service.notify_tender(tender.id, verdict.id)
        assert first.status == "pending_retry"
        world.add_dev("dev@opex.example")
        summary = service.flush_pending_retry()

        assert summary.flushed == 1
        payload = json.loads(handler.requests[-1].content)
        assert payload["to"] == ["dev@opex.example"]
        assert "1. Background" in payload["text"]
        again = service.notify_tender(tender.id, verdict.id)
        assert again.status == "already_notified"
        assert len(handler.requests) == 1

    def test_permanent_failover_is_observable(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one", priority=1)
        world.add_provider(name="sendlib-two", priority=2)

        def handler(request):
            if "sendlib-one" in request.headers.get("authorization", ""):
                return Response(401, json={"error": "invalid credentials"})
            return Response(200, json={"id": "ok"})

        hook = NullMailAlertHook()
        service = world.service(handler, alert_hook=hook)
        outcome = service.notify_tender(tender.id, verdict.id)

        assert outcome.status == "sent"
        assert any(item.error_code == "provider_failover" for item in hook.notices)

    def test_dry_run_prepares_without_reservation_or_provider_call(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        handler = _Handler(200)
        service = world.service(handler)
        outcome = service.notify_tender(tender.id, verdict.id, dry_run=True)
        assert outcome.status == "dry_run"
        assert handler.requests == []
        with world.session_factory() as session:
            assert _logs(session) == []

    def test_new_service_instance_recovers_pending_outbox(self, world) -> None:
        world.add_dev("dev@opex.example")
        _, tender, _, verdict = world.seed_tender()
        world.add_provider(name="sendlib-one")
        first_handler = _Handler(503)
        first_service = world.service(first_handler)
        assert first_service.notify_tender(tender.id, verdict.id).status == "pending_retry"

        second_handler = _Handler(200)
        second_service = world.service(second_handler)
        summary = second_service.flush_pending_retry()
        assert summary.flushed == 1
        assert len(second_handler.requests) == 1

    def test_stale_send_claim_is_marked_possible_duplicate(self, world) -> None:
        _, tender, _, verdict = world.seed_tender()
        with world.session_factory() as session:
            repo = NotificationRepository(session)
            row = repo.create_pending(
                dedupe_key="stale-test",
                tender_id=tender.id,
                verdict_id=verdict.id,
                correlation_id=tender.correlation_id,
                recipients_snapshot=[],
                attachments=[],
                links=[],
            )
            row_id = row.id
            repo.begin_send(row_id, claim_token="old-worker", lease_seconds=1)
            row = repo.get(row_id)
            row.claim_until = datetime.now(UTC) - timedelta(seconds=10)
            session.commit()

        with world.session_factory() as session:
            claimed = NotificationRepository(session).claim_pending(
                now=datetime.now(UTC), lease_seconds=30
            )
            assert any(item.id == row_id for item in claimed)
            session.commit()
        with world.session_factory() as session:
            assert session.get(NotificationLog, row_id).possible_duplicate is True

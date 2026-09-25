"""Durable, Test-Mode-safe notification preparation and delivery.

The service consumes persisted ``Tender``/``Document``/``Verdict`` rows; it never calls an
LLM or repeats discovery, acquisition, or processing.  A notification is reserved in the
database before the first provider request, claimed with a short lease, and finalised with
all provider attempts.  A crash therefore leaves a recoverable outbox row rather than an
in-memory promise.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.core.errors import (
    EMAIL_SEND_FAILED,
    MAIL_ALL_PROVIDERS_FAILED,
    MAIL_BREAKER_OPEN,
    MAIL_CONFIGURATION_ERROR,
    NOTIFICATION_INVALID_VERDICT,
    NOTIFICATION_NO_VERDICT,
    NOTIFICATION_ROUTING_FAILED,
    PERSISTENCE_NOT_FOUND,
    PROVIDER_FAILOVER,
)
from tender_intelligence.crypto.secrets import SecretError, decrypt_secret, get_master_key
from tender_intelligence.db.models.config import Setting
from tender_intelligence.db.models.documents import Document
from tender_intelligence.db.models.mail import MailProvider as MailProviderModel
from tender_intelligence.db.models.mail import NotificationLog
from tender_intelligence.db.models.recipients import Recipient
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.models.verdicts import Verdict
from tender_intelligence.db.repositories.base import PersistenceError
from tender_intelligence.db.repositories.notifications import (
    MailProviderRepository,
    NotificationRepository,
    ProviderUsageRepository,
)
from tender_intelligence.mail import templates
from tender_intelligence.mail.breaker import BreakerListener, BreakerState, CircuitBreaker
from tender_intelligence.mail.chain import (
    ChainResult,
    MailRetryPolicy,
    ProviderChain,
    ProviderEntry,
    ProviderUsageLimiter,
)
from tender_intelligence.mail.errors import MailError, configuration_error
from tender_intelligence.mail.factory import ProviderAdapterRegistry, ProviderBuildContext
from tender_intelligence.mail.links import SecureLinkSigner, signer_from_secret
from tender_intelligence.mail.message import EmailMessage, MailAttachment
from tender_intelligence.mail.planner import AttachmentPlanner
from tender_intelligence.mail.provider import (
    Capabilities,
    SendResult,
)
from tender_intelligence.mail.provider import (
    MailProvider as MailProviderAdapter,
)
from tender_intelligence.mail.sendlib import SENDLIB_FREE_CAPABILITIES, build_from_context
from tender_intelligence.notifications import banner
from tender_intelligence.notifications.contracts import NotificationEvent
from tender_intelligence.notifications.router import (
    RecipientRouter,
    RecipientSelection,
    RoutingError,
)
from tender_intelligence.notifications.test_mode import TestModePolicy
from tender_intelligence.storage.interface import ObjectStorage

log = logging.getLogger("tender_intelligence.notifications.service")

_CAPABILITY_KEYS = (
    "max_attachments",
    "max_attachment_mb",
    "max_message_mb",
    "daily_limit",
    "rate_limit_per_min",
    "needs_verified_domain",
)


class NotificationError(Exception):
    """A notification could not be prepared or delivered, with a machine-readable code."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class NotificationSafetyError(NotificationError):
    """Defence-in-depth: an unsafe business route was detected while Test Mode was ON."""


@dataclass(frozen=True)
class NotificationOutcome:
    """Result of one notification call or dry-run preparation."""

    status: str  # sent | already_notified | pending_retry | dry_run
    notification_log_id: int | None = None
    provider_used: str | None = None
    possible_duplicate: bool = False
    error_code: str | None = None
    dedupe_key: str = ""


@dataclass(frozen=True)
class FlushSummary:
    """Result of one durable-outbox flush."""

    attempted: int
    flushed: int
    still_pending: int
    banner_cleared: bool = False


@dataclass(frozen=True)
class MailAlertNotice:
    """A safe breaker/chain event forwarded to the later alert transport."""

    error_code: str
    provider_name: str | None
    correlation_id: str | None
    message: str = ""


class MailAlertHook(Protocol):
    """Receives mail-layer alert events; transport belongs to the caller."""

    def notify(self, notice: MailAlertNotice) -> None: ...


class NullMailAlertHook:
    """Default hook that records notices for tests and operators."""

    def __init__(self) -> None:
        self.notices: list[MailAlertNotice] = []

    def notify(self, notice: MailAlertNotice) -> None:
        self.notices.append(notice)


@dataclass(frozen=True)
class _DeliveryPlan:
    provider_id: int | None
    attach_pairs: tuple[tuple[Document, MailAttachment], ...]
    link_docs: tuple[tuple[int, str], ...]


class _UnavailableAdapter(MailProviderAdapter):
    """Provider placeholder that records a configuration failure as a real chain attempt."""

    def __init__(self, name: str, capabilities: Capabilities) -> None:
        self.name = name
        self._capabilities = capabilities

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    def send(self, message: EmailMessage) -> SendResult:
        raise configuration_error("provider adapter could not be constructed")


@dataclass(frozen=True)
class _PreparedNotification:
    tender: Tender
    verdict: Verdict
    content: templates.NotificationContent
    selection: RecipientSelection
    plan: _DeliveryPlan
    links: tuple[templates.LinkedDocument, ...]
    message: EmailMessage | None
    dedupe_key: str
    attachment_metadata: list[dict[str, Any]]
    link_metadata: list[dict[str, Any]]
    payload: dict[str, Any]
    priority: int
    kind: templates.NotificationKind


class _AlertForwarder:
    """Forward breaker transitions without importing Prompt 12's alert manager."""

    def __init__(self, hook: MailAlertHook, correlation_id: str | None) -> None:
        self._hook = hook
        self._correlation_id = correlation_id

    def on_breaker_event(self, event: Any) -> None:
        if event.state == BreakerState.OPEN:
            code = MAIL_BREAKER_OPEN
        elif event.state in {BreakerState.HALF_OPEN, BreakerState.CLOSED}:
            code = PROVIDER_FAILOVER
        else:
            return
        self._hook.notify(
            MailAlertNotice(
                error_code=code,
                provider_name=event.provider_name,
                correlation_id=self._correlation_id,
                message=event.message,
            )
        )


class NotificationService:
    """Prepare and deliver notifications from already-produced verdict artefacts."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        storage: ObjectStorage,
        client_factory: Callable[[], Any] | None = None,
        link_signer: SecureLinkSigner | None = None,
        link_base_url: str | None = None,
        alert_hook: MailAlertHook | None = None,
        breaker_listener: BreakerListener | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        retry_policy: MailRetryPolicy | None = None,
        breaker_threshold: int = 3,
        breaker_cooldown_seconds: float = 300.0,
        provider_secret_key: bytes | None = None,
        provider_registry: ProviderAdapterRegistry | None = None,
        verified_domain_ready: Callable[[MailProviderModel], bool] | None = None,
        lease_seconds: float = 300.0,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._client_factory = client_factory
        self._sleeper = sleeper
        self._clock = clock
        self._retry_policy = retry_policy or MailRetryPolicy()
        self._breaker_threshold = breaker_threshold
        self._breaker_cooldown = float(breaker_cooldown_seconds)
        self._alert_hook = alert_hook
        self._breaker_listener = breaker_listener
        self._provider_key = provider_secret_key
        self._provider_registry = provider_registry or ProviderAdapterRegistry()
        self._verified_domain_ready = verified_domain_ready or (lambda _row: False)
        if not self._provider_registry.contains("sendlib"):
            self._provider_registry.register("sendlib", build_from_context)
        self._usage_limiter = ProviderUsageLimiter(clock=clock)
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._link_signer = link_signer or self._build_default_signer(link_base_url)
        # SQLite's shared in-memory test connection cannot execute two explicit transactions at
        # once.  This lock protects one service instance; the unique dedupe constraint and lease
        # columns protect separate workers/databases.
        self._delivery_lock = threading.RLock()

    # ------------------------------------------------------------------ public API

    def notify_tender(
        self,
        tender_id: int,
        verdict_id: int | None = None,
        *,
        event: NotificationEvent | None = None,
        dry_run: bool = False,
    ) -> NotificationOutcome:
        """Prepare and deliver one assessment/update notification.

        ``event`` is optional for compatibility with the current pipeline.  Future Prompt
        13/14 wiring should pass its persisted verdict/event identity here, especially for an
        update so the sticky ``Tender.is_update`` flag is not treated as a fresh event.
        """

        with self._delivery_lock:
            prepared = self._prepare(tender_id, verdict_id, event)
            if dry_run:
                return NotificationOutcome(status="dry_run", dedupe_key=prepared.dedupe_key)
            row, created = self._reserve(prepared)
            if not created:
                return NotificationOutcome(
                    status="already_notified",
                    notification_log_id=row.id,
                    provider_used=row.provider_used,
                    possible_duplicate=row.possible_duplicate,
                    error_code=row.last_error_code,
                    dedupe_key=row.dedupe_key,
                )
            return self._send_reserved(prepared, int(row.id))

    def send_test_email(
        self,
        recipient_email: str,
        subject: str,
        text_body: str,
        *,
        correlation_id: str | None = None,
        preferred_provider_id: int | None = None,
    ) -> NotificationOutcome:
        """Send an operator test through the configured chain to one active dev recipient.

        This uses the existing chain, breaker, rate-limit and attempt repositories. It creates
        a notification audit row with no tender/verdict association and never enters the
        tender-notification reservation/retry path.
        """
        correlation_id = correlation_id or str(uuid.uuid4())
        with self._delivery_lock:
            with self._session() as session:
                setting = session.get(Setting, 1)
                recipient = session.scalar(
                    select(Recipient).where(
                        Recipient.email == recipient_email,
                        Recipient.list_type == "dev_alert",
                        Recipient.active.is_(True),
                    )
                )
                if setting is None or not setting.test_mode:
                    raise NotificationSafetyError(
                        NOTIFICATION_ROUTING_FAILED, "Test Mode is required for test email"
                    )
                if recipient is None:
                    raise NotificationSafetyError(
                        NOTIFICATION_ROUTING_FAILED,
                        "recipient is not an active dev-alert recipient",
                    )
                recipient_snapshot = [
                    {"email": recipient.email, "delivery": "to", "list_type": "dev_alert"}
                ]
                dedupe_key = f"admin-test:{uuid.uuid4().hex}"
                row = NotificationRepository(session).create_pending(
                    dedupe_key=dedupe_key,
                    tender_id=None,
                    verdict_id=None,
                    correlation_id=correlation_id,
                    recipients_snapshot=recipient_snapshot,
                    attachments=[],
                    links=[],
                    notification_kind="test",
                    message_payload={"subject": subject, "text_body": text_body, "test_mode": True},
                )
                row.status = "sending"
                log_id = int(row.id)
                provider_rows = MailProviderRepository(session).list_active_ordered()

            policy = TestModePolicy(True)
            message = EmailMessage(
                to=(recipient_email,),
                subject=policy.apply_subject_prefix(subject),
                text_body=text_body,
                dedupe_key=dedupe_key,
                correlation_id=correlation_id,
            )
            message.validate()
            chain = self._build_chain(
                provider_rows, preferred=preferred_provider_id, correlation_id=correlation_id
            )

            def recheck_test_route() -> None:
                with self._session() as session:
                    current_setting = session.get(Setting, 1)
                    current_recipient = session.scalar(
                        select(Recipient).where(
                            Recipient.email == recipient_email,
                            Recipient.list_type == "dev_alert",
                            Recipient.active.is_(True),
                        )
                    )
                    if (
                        current_setting is None
                        or not current_setting.test_mode
                        or current_recipient is None
                    ):
                        raise NotificationSafetyError(
                            NOTIFICATION_ROUTING_FAILED,
                            "test recipient route changed before delivery",
                        )

            result = (
                chain.deliver(message, before_attempt=recheck_test_route)
                if chain.providers
                else ChainResult(
                    delivered=False,
                    error_code=MAIL_CONFIGURATION_ERROR,
                    notification_status="failed",
                )
            )
            with self._session() as session:
                self._persist_attempts(session, log_id, correlation_id, result)
                self._persist_breakers(session, chain)
                row = NotificationRepository(session).get_for_update(log_id)
                if row is not None:
                    row.status = "sent" if result.delivered else "failed"
                    row.provider_used = result.provider_used
                    row.sent_at = datetime.now(UTC) if result.delivered else None
                    row.possible_duplicate = result.possible_duplicate
                    row.last_error_code = result.error_code
                    row.error = result.error_code if not result.delivered else None
                    row.claim_token = None
                    row.claim_until = None
            return NotificationOutcome(
                status="sent" if result.delivered else "failed",
                notification_log_id=log_id,
                provider_used=result.provider_used,
                possible_duplicate=result.possible_duplicate,
                error_code=result.error_code,
                dedupe_key=dedupe_key,
            )

    def flush_pending_retry(self, limit: int = 10) -> FlushSummary:
        """Claim and retry durable outbox entries after a worker/provider restart."""

        with self._delivery_lock:
            claimed: list[tuple[int, str]] = []
            with self._session() as session:
                rows = NotificationRepository(session).claim_pending(
                    limit=max(0, limit), lease_seconds=self._lease_seconds
                )
                claimed = [(int(row.id), str(row.claim_token)) for row in rows if row.claim_token]
            flushed = 0
            for log_id, claim_token in claimed:
                try:
                    if self._flush_claimed(log_id, claim_token):
                        flushed += 1
                except Exception:  # noqa: BLE001 - isolate one malformed outbox row
                    log.exception(
                        "outbox row could not be processed",
                        extra={
                            "stage": "email",
                            "status": "pending_retry",
                            "error_code": EMAIL_SEND_FAILED,
                            "notification_id": log_id,
                        },
                    )
            with self._session() as session:
                repo = NotificationRepository(session)
                still_pending = repo.count_recoverable()
                cleared = False
                if flushed > 0 and still_pending == 0 and banner.email_chain_banner_active(session):
                    banner.clear_email_chain_down_banner(session)
                    cleared = True
            return FlushSummary(
                attempted=len(claimed),
                flushed=flushed,
                still_pending=still_pending,
                banner_cleared=cleared,
            )

    # ------------------------------------------------------------------ transaction helpers

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------ preparation

    def _prepare(
        self,
        tender_id: int,
        verdict_id: int | None,
        event: NotificationEvent | None,
    ) -> _PreparedNotification:
        with self._session() as session:
            tender = session.get(Tender, tender_id)
            if tender is None:
                raise NotificationError(PERSISTENCE_NOT_FOUND, f"tender {tender_id} not found")
            resolved_verdict = self._resolve_verdict(session, tender, verdict_id)
            test_mode, expiry_days = self._load_settings(session)
            kind = (
                templates.NotificationKind(event.kind)
                if event is not None
                else (
                    templates.NotificationKind.UPDATE
                    if tender.is_update
                    else templates.NotificationKind.NEW
                )
            )
            if event is not None:
                if event.tender_id != tender.id:
                    raise NotificationError(NOTIFICATION_ROUTING_FAILED, "event/tender mismatch")
                if event.verdict_id != resolved_verdict.id:
                    raise NotificationError(NOTIFICATION_ROUTING_FAILED, "event/verdict mismatch")
                if event.correlation_id and event.correlation_id != tender.correlation_id:
                    raise NotificationError(
                        NOTIFICATION_ROUTING_FAILED, "event/correlation mismatch"
                    )
            content, pairs = self._prepare_payload(
                session, tender, resolved_verdict, test_mode, kind, event
            )
            candidate_pairs = self._candidate_pairs(pairs, kind, event)
            provider_rows = MailProviderRepository(session).list_active_ordered()
            overhead = self._message_overhead(content, candidate_pairs)
            plan = self._delivery_plan(provider_rows, candidate_pairs, overhead)
            links = self._make_links(tender, plan.link_docs, expiry_days)
            content = replace(
                content,
                test_mode=test_mode,
                attached_filenames=tuple(att.filename for _, att in plan.attach_pairs),
                linked_documents=links,
            )
            try:
                selection = RecipientRouter(session).resolve(
                    source_name=tender.source.name,
                    recommendation=resolved_verdict.recommendation,
                    urgency_flag=resolved_verdict.urgency_flag,
                    test_mode=test_mode,
                    source_scope=tender.source.recipient_scope,
                )
            except RoutingError as exc:
                # Keep the safety boundary fail-closed.  A durable row with no recipients is
                # recoverable after an operator seeds the dev list; it must never fall back to
                # business recipients.
                log.warning(
                    "notification has no safe recipient set",
                    extra={
                        "stage": "email",
                        "status": "pending_retry",
                        "correlation_id": tender.correlation_id,
                        "error_code": NOTIFICATION_ROUTING_FAILED,
                    },
                )
                selection = RecipientSelection()
                del exc
            self._assert_test_mode_safety(test_mode, selection)
            dedupe_key = self._dedupe_key(tender, resolved_verdict, selection)
            attachment_metadata = self._attachment_metadata(plan.attach_pairs)
            link_metadata = self._link_metadata(plan.link_docs, links)
            message = (
                self._compose_message(
                    tender, content, selection, plan.attach_pairs, links, dedupe_key
                )
                if selection.addresses
                else None
            )
            payload = self._message_payload(message, selection, test_mode, kind)
            return _PreparedNotification(
                tender=tender,
                verdict=resolved_verdict,
                content=content,
                selection=selection,
                plan=plan,
                links=links,
                message=message,
                dedupe_key=dedupe_key,
                attachment_metadata=attachment_metadata,
                link_metadata=link_metadata,
                payload=payload,
                priority=200 if kind == templates.NotificationKind.UPDATE else 100,
                kind=kind,
            )

    def _resolve_verdict(self, session: Session, tender: Tender, verdict_id: int | None) -> Verdict:
        if verdict_id is not None:
            verdict = session.get(Verdict, verdict_id)
            if verdict is None:
                raise NotificationError(PERSISTENCE_NOT_FOUND, f"verdict {verdict_id} not found")
            if verdict.tender_id != tender.id:
                raise NotificationError(
                    NOTIFICATION_NO_VERDICT, "verdict belongs to a different tender"
                )
            if verdict.recommendation not in {
                "APPLY",
                "DO NOT APPLY",
                "APPLY WITH CONDITIONS",
            }:
                raise NotificationError(
                    NOTIFICATION_INVALID_VERDICT,
                    "verdict recommendation is outside the confirmed enum",
                )
            return verdict
        verdict = session.scalars(
            select(Verdict)
            .where(Verdict.tender_id == tender.id)
            .order_by(Verdict.generated_at.desc(), Verdict.id.desc())
            .limit(1)
        ).first()
        if verdict is None:
            raise NotificationError(
                NOTIFICATION_NO_VERDICT,
                f"tender {tender.id} has no verdict row; prompts 13/14 are not implemented",
            )
        if verdict.recommendation not in {"APPLY", "DO NOT APPLY", "APPLY WITH CONDITIONS"}:
            raise NotificationError(
                NOTIFICATION_INVALID_VERDICT,
                "verdict recommendation is outside the confirmed enum",
            )
        return verdict

    @staticmethod
    def _load_settings(session: Session) -> tuple[bool, int]:
        row = session.get(Setting, 1)
        if row is None:
            return True, 14
        expiry = int(row.link_expiry_days or 14)
        return bool(row.test_mode), max(1, expiry)

    def _prepare_payload(
        self,
        session: Session,
        tender: Tender,
        verdict: Verdict,
        test_mode: bool,
        kind: templates.NotificationKind,
        event: NotificationEvent | None,
    ) -> tuple[templates.NotificationContent, list[tuple[Document, MailAttachment]]]:
        pairs: list[tuple[Document, MailAttachment]] = []
        failed: list[str] = []
        skipped: list[str] = []
        changed_ids = set(event.changed_document_ids) if event is not None else set()
        documents = [
            document
            for document in sorted(tender.documents, key=lambda row: row.id)
            if kind != templates.NotificationKind.UPDATE
            or not changed_ids
            or document.id in changed_ids
        ]
        for document in documents:
            safe_name = _safe_filename(document.filename)
            available = False
            if document.download_status == "downloaded" and document.storage_path:
                try:
                    available = self._storage.exists(document.storage_path)
                    data = self._storage.get(document.storage_path) if available else b""
                except (OSError, KeyError, ValueError):
                    available = False
                    data = b""
                if available:
                    pairs.append(
                        (
                            document,
                            MailAttachment(
                                filename=safe_name,
                                content=data,
                                content_type=document.mime_type,
                            ),
                        )
                    )
            if not available or document.extraction_status == "failed":
                failed.append(safe_name)
            elif document.extraction_status == "skipped":
                skipped.append(safe_name)

        update_summary = ""
        previous_date = ""
        verdict_changed: bool | None = None
        if kind == templates.NotificationKind.UPDATE:
            if event is not None:
                update_summary = ", ".join(event.change_types) or "tender listing changed"
                details = "; ".join(f"{key}: {value}" for key, value in event.change_details)
                if details:
                    update_summary = f"{update_summary} ({details})"
                verdict_changed = event.material_change
            else:
                update_summary = (
                    f"status updated to {tender.status}"
                    if tender.status
                    else "tender listing changed"
                )
            previous = self._previous_sent(session, tender.id)
            if previous is not None and previous.sent_at is not None:
                previous_date = previous.sent_at.date().isoformat()

        return (
            templates.NotificationContent(
                kind=kind,
                test_mode=test_mode,
                source_name=tender.source.name,
                title=tender.title,
                urgent=verdict.urgency_flag,
                verdict=verdict.recommendation,
                confidence=verdict.confidence,
                background_summary=verdict.background_summary or "",
                requirements_summary=_as_items(verdict.requirements_summary),
                gap_analysis=_as_items(verdict.gap_analysis),
                deadline=tender.deadline,
                deadline_timezone=tender.deadline_timezone,
                incomplete_inputs=bool(verdict.incomplete_inputs or failed),
                attached_filenames=(),
                linked_documents=(),
                failed_filenames=tuple(dict.fromkeys(failed)),
                skipped_filenames=tuple(dict.fromkeys(skipped)),
                update_summary=update_summary,
                previous_assessment_date=previous_date,
                verdict_changed=verdict_changed,
            ),
            pairs,
        )

    @staticmethod
    def _candidate_pairs(
        pairs: list[tuple[Document, MailAttachment]],
        kind: templates.NotificationKind,
        event: NotificationEvent | None,
    ) -> list[tuple[Document, MailAttachment]]:
        if (
            kind != templates.NotificationKind.UPDATE
            or event is None
            or not event.changed_document_ids
        ):
            return pairs
        wanted = set(event.changed_document_ids)
        selected = [pair for pair in pairs if pair[0].id in wanted]
        if not selected:
            raise NotificationError(
                NOTIFICATION_ROUTING_FAILED,
                "update event references no available changed documents",
            )
        return selected

    def _previous_sent(self, session: Session, tender_id: int) -> NotificationLog | None:
        return session.scalars(
            select(NotificationLog)
            .where(NotificationLog.tender_id == tender_id, NotificationLog.status == "sent")
            .order_by(NotificationLog.sent_at.desc(), NotificationLog.id.desc())
            .limit(1)
        ).first()

    # ------------------------------------------------------------------ attachment planning

    @staticmethod
    def _message_overhead(
        content: templates.NotificationContent, pairs: list[tuple[Document, MailAttachment]]
    ) -> int:
        # Body rendering is deterministic.  Reserve a conservative amount for recipients,
        # headers, MIME boundaries, and link/filename metadata not present in the first pass.
        plain = templates.render_text(content).encode("utf-8")
        html = templates.render_html(content).encode("utf-8")
        return (
            len(plain)
            + len(html)
            + 4096
            + sum(len(doc.filename.encode("utf-8")) + 256 for doc, _ in pairs)
        )

    def _delivery_plan(
        self,
        provider_rows: list[MailProviderModel],
        pairs: list[tuple[Document, MailAttachment]],
        message_overhead: int,
    ) -> _DeliveryPlan:
        """Choose the first whole-set fit, otherwise the provider carrying most documents."""

        attachments = [attachment for _, attachment in pairs]
        best: tuple[MailProviderModel, Any, tuple[int, int]] | None = None
        for row in provider_rows:
            if not row.active or not self._provider_domain_ready(row):
                continue
            try:
                capabilities = _capabilities_for(row)
            except (TypeError, ValueError, KeyError):
                # A malformed provider row must not prevent other providers (or link-only
                # delivery) from being considered.
                continue
            plan = AttachmentPlanner(capabilities).plan(
                attachments, message_overhead_bytes=message_overhead
            )
            if plan.fits_whole_set:
                return _plan_for_row(row, plan, pairs)
            candidate = (len(plan.attach), -plan.total_encoded_bytes)
            if best is None or candidate > (best[2][0], best[2][1]):
                best = (row, plan, candidate)
        if best is None:
            return _DeliveryPlan(
                provider_id=None,
                attach_pairs=(),
                link_docs=tuple((doc.id, att.filename) for doc, att in pairs),
            )
        return _plan_for_row(best[0], best[1], pairs)

    def _make_links(
        self, tender: Tender, link_docs: tuple[tuple[int, str], ...], expiry_days: int
    ) -> tuple[templates.LinkedDocument, ...]:
        links: list[templates.LinkedDocument] = []
        now = datetime.fromtimestamp(self._clock(), UTC)
        for document_id, filename in link_docs:
            link = self._link_signer.sign(
                tender_id=tender.id,
                document_id=document_id,
                filename=filename,
                expiry_days=expiry_days,
                now=now,
            )
            links.append(
                templates.LinkedDocument(
                    filename=link.filename,
                    url=link.url,
                    expires_at=link.expires_at,
                    expiry_days=link.expiry_days,
                )
            )
        return tuple(links)

    @staticmethod
    def _attachment_metadata(
        pairs: Sequence[tuple[Document, MailAttachment]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "document_id": int(doc.id),
                "filename": att.filename,
                "checksum": doc.checksum,
            }
            for doc, att in pairs
        ]

    @staticmethod
    def _link_metadata(
        link_docs: tuple[tuple[int, str], ...], links: tuple[templates.LinkedDocument, ...]
    ) -> list[dict[str, Any]]:
        return [
            {
                "document_id": int(document_id),
                "filename": link.filename,
                "url": link.url,
                "expires_at": link.expires_at.isoformat(),
                "expiry_days": link.expiry_days,
            }
            for (document_id, _), link in zip(link_docs, links, strict=True)
        ]

    def _compose_message(
        self,
        tender: Tender,
        content: templates.NotificationContent,
        selection: RecipientSelection,
        pairs: list[tuple[Document, MailAttachment]] | tuple[tuple[Document, MailAttachment], ...],
        links: tuple[templates.LinkedDocument, ...],
        dedupe_key: str,
    ) -> EmailMessage:
        message = EmailMessage(
            to=selection.to,
            cc=selection.cc,
            bcc=selection.bcc,
            subject=TestModePolicy(content.test_mode).apply_subject_prefix(
                templates.subject(content)
            ),
            text_body=templates.render_text(content),
            html_body=templates.render_html(content),
            attachments=tuple(attachment for _, attachment in pairs),
            secure_links=tuple(link.url for link in links),
            dedupe_key=dedupe_key,
            correlation_id=tender.correlation_id,
        )
        message.validate()
        return message

    def _attachments_from_json(
        self, session: Session, stored: list[dict]
    ) -> tuple[tuple[MailAttachment, ...], list[tuple[Document, MailAttachment]]]:
        attachments: list[MailAttachment] = []
        pairs: list[tuple[Document, MailAttachment]] = []
        for item in stored:
            try:
                document_id = int(item["document_id"])
            except (KeyError, TypeError, ValueError):
                continue
            document = session.get(Document, document_id)
            if document is None or not document.storage_path:
                continue
            stored_checksum = item.get("checksum")
            if stored_checksum and document.checksum and stored_checksum != document.checksum:
                continue
            try:
                if not self._storage.exists(document.storage_path):
                    continue
                data = self._storage.get(document.storage_path)
            except (OSError, KeyError, ValueError):
                continue
            attachment = MailAttachment(
                filename=_safe_filename(str(item.get("filename") or document.filename)),
                content=data,
                content_type=document.mime_type,
            )
            attachments.append(attachment)
            pairs.append((document, attachment))
        return tuple(attachments), pairs

    def _links_from_json(self, stored: list[dict]) -> tuple[templates.LinkedDocument, ...]:
        return _links_from_json(stored)

    # ------------------------------------------------------------------ reservation and send

    def _reserve(self, prepared: _PreparedNotification) -> tuple[NotificationLog, bool]:
        with self._session() as session:
            repo = NotificationRepository(session)
            existing = repo.get_by_dedupe_key(prepared.dedupe_key)
            if existing is not None:
                return existing, False
            try:
                row = repo.create_pending(
                    dedupe_key=prepared.dedupe_key,
                    tender_id=prepared.tender.id,
                    verdict_id=prepared.verdict.id,
                    correlation_id=prepared.tender.correlation_id,
                    recipients_snapshot=list(prepared.selection.snapshot),
                    attachments=prepared.attachment_metadata,
                    links=prepared.link_metadata,
                    message_payload=prepared.payload,
                    notification_kind=prepared.kind.value,
                    priority=prepared.priority,
                )
                return row, True
            except IntegrityError:
                session.rollback()
                winner = repo.get_by_dedupe_key(prepared.dedupe_key)
                if winner is None:
                    raise
                return winner, False

    def _send_reserved(self, prepared: _PreparedNotification, log_id: int) -> NotificationOutcome:
        claim_token = uuid.uuid4().hex
        with self._session() as session:
            NotificationRepository(session).begin_send(
                log_id, claim_token=claim_token, lease_seconds=self._lease_seconds
            )
        if prepared.message is None:
            return self._finalize_failure(
                log_id,
                claim_token,
                prepared,
                NOTIFICATION_ROUTING_FAILED,
                "no safe active recipient is configured",
            )
        return self._deliver_and_finalize(prepared, log_id, claim_token)

    def _renew_delivery_claim(self, log_id: int, claim_token: str) -> None:
        with self._session() as session:
            NotificationRepository(session).renew_claim(
                log_id, claim_token=claim_token, lease_seconds=self._lease_seconds
            )

    def _before_provider_attempt(
        self,
        prepared: _PreparedNotification,
        log_id: int,
        claim_token: str,
    ) -> None:
        """Renew the lease and re-check the global safety switch at the wire boundary."""

        self._renew_delivery_claim(log_id, claim_token)
        with self._session() as session:
            test_mode, _ = self._load_settings(session)
        self._assert_test_mode_safety(test_mode, prepared.selection)

    def _revalidate_for_send(
        self,
        prepared: _PreparedNotification,
        log_id: int,
        claim_token: str,
    ) -> _PreparedNotification | None:
        """Re-read Test Mode and routing immediately before crossing the provider boundary."""

        with self._session() as session:
            row = NotificationRepository(session).get_for_update(log_id)
            if row is None or row.claim_token != claim_token:
                raise PersistenceError("notification claim is no longer owned by this worker")
            tender = session.get(Tender, prepared.tender.id)
            verdict = session.get(Verdict, prepared.verdict.id)
            if tender is None or verdict is None:
                raise PersistenceError("notification tender/verdict disappeared before send")
            test_mode, _ = self._load_settings(session)
            try:
                # Resolve the active route again at the send boundary.  This keeps a stale
                # reservation from sending to a removed recipient and makes mode transitions
                # explicit; the resulting recipient set is also folded into the dedupe key below.
                selection = RecipientRouter(session).resolve(
                    source_name=tender.source.name,
                    recommendation=verdict.recommendation,
                    urgency_flag=verdict.urgency_flag,
                    test_mode=test_mode,
                    source_scope=tender.source.recipient_scope,
                )
            except RoutingError:
                return None
            self._assert_test_mode_safety(test_mode, selection)
            # The reservation may have been made with a different active route (for example,
            # while Test Mode was changing).  The idempotency identity must describe the actual
            # recipients used for this send, not the stale pre-reservation snapshot.
            send_dedupe_key = self._dedupe_key(tender, verdict, selection)
            content = replace(prepared.content, test_mode=test_mode)
            if selection.addresses:
                if prepared.message is not None and not prepared.content.background_summary:
                    base = prepared.message
                    message = EmailMessage(
                        to=selection.to,
                        cc=selection.cc,
                        bcc=selection.bcc,
                        subject=TestModePolicy(test_mode).apply_subject_prefix(base.subject),
                        text_body=base.text_body,
                        html_body=base.html_body,
                        attachments=tuple(att for _, att in prepared.plan.attach_pairs),
                        secure_links=tuple(link.url for link in prepared.links),
                        dedupe_key=send_dedupe_key,
                        correlation_id=tender.correlation_id,
                    )
                else:
                    message = self._compose_message(
                        tender,
                        content,
                        selection,
                        prepared.plan.attach_pairs,
                        prepared.links,
                        send_dedupe_key,
                    )
            else:
                message = None
            row.dedupe_key = send_dedupe_key
            row.recipients_snapshot = list(selection.snapshot)
            row.message_payload = self._message_payload(
                message, selection, test_mode, prepared.kind
            )
            return replace(
                prepared,
                tender=tender,
                verdict=verdict,
                content=content,
                selection=selection,
                message=message,
                dedupe_key=send_dedupe_key,
                payload=dict(row.message_payload or {}),
            )

    def _deliver_and_finalize(
        self,
        prepared: _PreparedNotification,
        log_id: int,
        claim_token: str,
    ) -> NotificationOutcome:
        chain: ProviderChain | None = None
        try:
            revalidated = self._revalidate_for_send(prepared, log_id, claim_token)
            if revalidated is None:
                return self._finalize_failure(
                    log_id,
                    claim_token,
                    prepared,
                    NOTIFICATION_ROUTING_FAILED,
                    "no safe active recipient is configured at send time",
                )
            prepared = revalidated
            with self._session() as session:
                NotificationRepository(session).renew_claim(
                    log_id, claim_token=claim_token, lease_seconds=self._lease_seconds
                )
                provider_rows = MailProviderRepository(session).list_active_ordered()
            chain = self._build_chain(
                provider_rows,
                preferred=prepared.plan.provider_id,
                correlation_id=prepared.tender.correlation_id,
            )
            if not chain.providers:
                result = ChainResult(
                    delivered=False,
                    error_code=MAIL_CONFIGURATION_ERROR,
                    notification_status="pending_retry",
                )
            else:
                result = chain.deliver(
                    prepared.message,  # type: ignore[arg-type]
                    before_attempt=lambda: self._before_provider_attempt(
                        prepared, log_id, claim_token
                    ),
                )
        except NotificationSafetyError:
            log.warning(
                "notification safety check refused a provider attempt",
                extra={
                    "stage": "email",
                    "status": "pending_retry",
                    "correlation_id": prepared.tender.correlation_id,
                    "error_code": NOTIFICATION_ROUTING_FAILED,
                },
            )
            return self._finalize_failure(
                log_id,
                claim_token,
                prepared,
                NOTIFICATION_ROUTING_FAILED,
                "Test Mode safety check failed immediately before provider delivery",
            )
        except PersistenceError:
            log.warning(
                "notification claim was lost before provider delivery",
                extra={
                    "stage": "email",
                    "status": "pending_retry",
                    "correlation_id": prepared.tender.correlation_id,
                    "error_code": EMAIL_SEND_FAILED,
                },
            )
            return NotificationOutcome(
                status="pending_retry",
                notification_log_id=log_id,
                error_code=EMAIL_SEND_FAILED,
                dedupe_key=prepared.dedupe_key,
            )
        except (OperationalError, IntegrityError):
            log.exception(
                "notification provider preparation failed",
                extra={
                    "stage": "email",
                    "status": "pending_retry",
                    "correlation_id": prepared.tender.correlation_id,
                    "error_code": EMAIL_SEND_FAILED,
                },
            )
            return self._finalize_failure(
                log_id, claim_token, prepared, EMAIL_SEND_FAILED, "provider preparation failed"
            )
        except Exception:  # noqa: BLE001 - external provider boundary
            log.exception(
                "notification provider chain failed",
                extra={
                    "stage": "email",
                    "status": "pending_retry",
                    "correlation_id": prepared.tender.correlation_id,
                    "error_code": EMAIL_SEND_FAILED,
                },
            )
            return self._finalize_failure(
                log_id, claim_token, prepared, EMAIL_SEND_FAILED, "provider chain failed"
            )
        return self._finalize_result(prepared, log_id, claim_token, result, chain=chain)

    def _emit_failover_alerts(self, result: ChainResult, correlation_id: str | None) -> None:
        if self._alert_hook is None:
            return
        for provider_name, error_code, _ in result.failovers:
            try:
                self._alert_hook.notify(
                    MailAlertNotice(
                        error_code=PROVIDER_FAILOVER,
                        provider_name=provider_name,
                        correlation_id=correlation_id,
                        message=f"provider failed with {error_code}; failover attempted",
                    )
                )
            except Exception:  # noqa: BLE001 - observability cannot break delivery
                log.warning(
                    "provider failover alert hook failed",
                    extra={
                        "stage": "email",
                        "status": "warning",
                        "correlation_id": correlation_id,
                        "error_code": PROVIDER_FAILOVER,
                    },
                )

    def _finalize_result(
        self,
        prepared: _PreparedNotification,
        log_id: int,
        claim_token: str,
        result: ChainResult,
        *,
        chain: ProviderChain | None,
    ) -> NotificationOutcome:
        self._emit_failover_alerts(result, prepared.tender.correlation_id)
        already_open = True
        with self._session() as session:
            self._persist_attempts(session, log_id, prepared.tender.correlation_id, result)
            if chain is not None:
                self._persist_breakers(session, chain)
            repo = NotificationRepository(session)
            if result.delivered:
                row = repo.mark_sent(
                    log_id,
                    provider_used=result.provider_used or "",
                    provider_id=result.provider_id,
                    attachments=prepared.attachment_metadata,
                    links=prepared.link_metadata,
                    possible_duplicate=result.possible_duplicate,
                    claim_token=claim_token,
                )
                outcome = NotificationOutcome(
                    status="sent",
                    notification_log_id=row.id,
                    provider_used=result.provider_used,
                    possible_duplicate=result.possible_duplicate,
                    dedupe_key=row.dedupe_key,
                )
            else:
                error_code = result.error_code or MAIL_ALL_PROVIDERS_FAILED
                row = repo.keep_pending(
                    log_id,
                    error_code=error_code,
                    error_message=_result_message(result),
                    possible_duplicate=result.possible_duplicate,
                    claim_token=claim_token,
                )
                already_open = banner.email_chain_banner_active(session)
                banner.raise_email_chain_down_banner(
                    session,
                    correlation_id=prepared.tender.correlation_id,
                    source_id=prepared.tender.source_id,
                    message=_result_message(result),
                )
                outcome = NotificationOutcome(
                    status="pending_retry",
                    notification_log_id=row.id,
                    error_code=error_code,
                    possible_duplicate=result.possible_duplicate,
                    dedupe_key=row.dedupe_key,
                )
        if not result.delivered and not already_open:
            self._try_system_alert(
                correlation_id=prepared.tender.correlation_id,
                source_name=prepared.tender.source.name,
                error_code=outcome.error_code or MAIL_ALL_PROVIDERS_FAILED,
                message=_result_message(result),
            )
        return outcome

    def _finalize_failure(
        self,
        log_id: int,
        claim_token: str,
        prepared: _PreparedNotification,
        error_code: str,
        message: str,
    ) -> NotificationOutcome:
        with self._session() as session:
            row = NotificationRepository(session).keep_pending(
                log_id,
                error_code=error_code,
                error_message=message,
                claim_token=claim_token,
            )
            banner.raise_email_chain_down_banner(
                session,
                correlation_id=prepared.tender.correlation_id,
                source_id=prepared.tender.source_id,
                message=message,
            )
        return NotificationOutcome(
            status="pending_retry",
            notification_log_id=row.id,
            error_code=error_code,
            dedupe_key=row.dedupe_key,
        )

    # ------------------------------------------------------------------ outbox retry

    def _flush_claimed(self, log_id: int, claim_token: str) -> bool:
        with self._session() as session:
            row = NotificationRepository(session).get_for_update(log_id)
            if row is None or row.claim_token != claim_token:
                return False
            if row.tender_id is None or row.verdict_id is None:
                NotificationRepository(session).keep_pending(
                    log_id,
                    error_code=NOTIFICATION_ROUTING_FAILED,
                    error_message="outbox row has no tender/verdict identity",
                    claim_token=claim_token,
                )
                return False
            tender = session.get(Tender, row.tender_id)
            verdict = session.get(Verdict, row.verdict_id)
            if tender is None or verdict is None:
                NotificationRepository(session).keep_pending(
                    log_id,
                    error_code=PERSISTENCE_NOT_FOUND,
                    error_message="tender or verdict no longer exists",
                    claim_token=claim_token,
                )
                return False
            test_mode, expiry_days = self._load_settings(session)
            self._refresh_expired_links(session, row, tender, expiry_days)
            selection = _selection_from_snapshot(row.recipients_snapshot or [])
            if test_mode and "tender" in selection.list_types:
                try:
                    selection = RecipientRouter(session).resolve(
                        source_name=tender.source.name,
                        recommendation=verdict.recommendation,
                        urgency_flag=verdict.urgency_flag,
                        test_mode=True,
                        source_scope=tender.source.recipient_scope,
                    )
                    row.recipients_snapshot = list(selection.snapshot)
                except RoutingError:
                    NotificationRepository(session).keep_pending(
                        log_id,
                        error_code=NOTIFICATION_ROUTING_FAILED,
                        error_message="Test Mode is ON but no active development recipient exists",
                        claim_token=claim_token,
                    )
                    return False
            if not selection.addresses:
                try:
                    selection = RecipientRouter(session).resolve(
                        source_name=tender.source.name,
                        recommendation=verdict.recommendation,
                        urgency_flag=verdict.urgency_flag,
                        test_mode=test_mode,
                        source_scope=tender.source.recipient_scope,
                    )
                    row.recipients_snapshot = list(selection.snapshot)
                except RoutingError:
                    selection = RecipientSelection()
            message = self._message_from_outbox(session, row, tender, verdict, selection, test_mode)
            _, attach_pairs = self._attachments_from_json(session, list(row.attachments or []))
            prepared = _PreparedNotification(
                tender=tender,
                verdict=verdict,
                content=templates.NotificationContent(),
                selection=selection,
                plan=_DeliveryPlan(None, tuple(attach_pairs), ()),
                links=self._links_from_json(row.links or []),
                message=message,
                dedupe_key=row.dedupe_key,
                attachment_metadata=list(row.attachments or []),
                link_metadata=list(row.links or []),
                payload=dict(row.message_payload or {}),
                priority=row.priority,
                kind=templates.NotificationKind(row.notification_kind),
            )
            provider_rows = MailProviderRepository(session).list_active_ordered()
        chain: ProviderChain | None = None
        if message is None:
            result = ChainResult(
                delivered=False,
                error_code=NOTIFICATION_ROUTING_FAILED,
                notification_status="pending_retry",
            )
        else:
            try:
                revalidated = self._revalidate_for_send(prepared, log_id, claim_token)
                if revalidated is None:
                    result = ChainResult(
                        delivered=False,
                        error_code=NOTIFICATION_ROUTING_FAILED,
                        notification_status="pending_retry",
                    )
                    prepared = replace(prepared, message=None, selection=RecipientSelection())
                else:
                    prepared = revalidated
                    message = prepared.message
            except PersistenceError:
                return False
            try:
                chain = self._build_chain(
                    provider_rows,
                    preferred=None,
                    correlation_id=tender.correlation_id,
                )
                result = (
                    chain.deliver(
                        message,
                        before_attempt=lambda: self._before_provider_attempt(
                            prepared, log_id, claim_token
                        ),
                    )
                    if chain.providers and message is not None
                    else ChainResult(
                        delivered=False,
                        error_code=MAIL_CONFIGURATION_ERROR,
                        notification_status="pending_retry",
                    )
                )
            except Exception:  # noqa: BLE001 - retry boundary
                log.exception(
                    "outbox provider retry failed",
                    extra={
                        "stage": "email",
                        "status": "pending_retry",
                        "correlation_id": tender.correlation_id,
                        "error_code": EMAIL_SEND_FAILED,
                    },
                )
                result = ChainResult(
                    delivered=False,
                    error_code=EMAIL_SEND_FAILED,
                    notification_status="pending_retry",
                )
        return self._finalize_outbox_result(prepared, log_id, claim_token, result, chain)

    def _finalize_outbox_result(
        self,
        prepared: _PreparedNotification,
        log_id: int,
        claim_token: str,
        result: ChainResult,
        chain: ProviderChain | None,
    ) -> bool:
        self._emit_failover_alerts(result, prepared.tender.correlation_id)
        with self._session() as session:
            self._persist_attempts(session, log_id, prepared.tender.correlation_id, result)
            if chain is not None:
                self._persist_breakers(session, chain)
            repo = NotificationRepository(session)
            if result.delivered:
                repo.mark_sent(
                    log_id,
                    provider_used=result.provider_used or "",
                    provider_id=result.provider_id,
                    attachments=prepared.attachment_metadata,
                    links=prepared.link_metadata,
                    possible_duplicate=result.possible_duplicate,
                    claim_token=claim_token,
                )
                return True
            repo.keep_pending(
                log_id,
                error_code=result.error_code or MAIL_ALL_PROVIDERS_FAILED,
                error_message=_result_message(result),
                possible_duplicate=result.possible_duplicate,
                claim_token=claim_token,
            )
            banner.raise_email_chain_down_banner(
                session,
                correlation_id=prepared.tender.correlation_id,
                source_id=prepared.tender.source_id,
                message=_result_message(result),
            )
        return False

    def _refresh_expired_links(
        self,
        session: Session,
        row: NotificationLog,
        tender: Tender,
        expiry_days: int,
    ) -> None:
        """Re-sign only expired links; a retry must not send a dead bearer URL."""

        stored = list(row.links or [])
        if not stored:
            return
        changed = False
        now = datetime.fromtimestamp(self._clock(), UTC)
        refreshed: list[dict[str, Any]] = []
        for item in stored:
            try:
                expiry = datetime.fromisoformat(str(item["expires_at"]))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                expires = int(expiry.timestamp())
                document_id = int(item["document_id"])
                filename = str(item["filename"])
            except (KeyError, TypeError, ValueError):
                refreshed.append(item)
                continue
            if self._link_signer.is_expired(expires, now=now):
                link = self._link_signer.sign(
                    tender_id=tender.id,
                    document_id=document_id,
                    filename=filename,
                    expiry_days=expiry_days,
                    now=now,
                )
                item = {
                    "document_id": document_id,
                    "filename": link.filename,
                    "url": link.url,
                    "expires_at": link.expires_at.isoformat(),
                    "expiry_days": link.expiry_days,
                }
                changed = True
            refreshed.append(item)
        if changed:
            row.links = refreshed
            payload = dict(row.message_payload or {})
            payload["secure_links"] = [str(item.get("url", "")) for item in refreshed]
            row.message_payload = payload
            session.flush()

    def _message_from_outbox(
        self,
        session: Session,
        row: NotificationLog,
        tender: Tender,
        verdict: Verdict,
        selection: RecipientSelection,
        test_mode: bool,
    ) -> EmailMessage | None:
        payload = dict(row.message_payload or {})
        if not selection.addresses:
            return None
        if payload.get("subject") and payload.get("text_body"):
            subject = TestModePolicy(test_mode).apply_subject_prefix(
                str(payload.get("subject", ""))
            )
            message = EmailMessage(
                to=selection.to,
                cc=selection.cc,
                bcc=selection.bcc,
                subject=subject,
                text_body=str(payload.get("text_body", "")),
                html_body=str(payload.get("html_body")) if payload.get("html_body") else None,
                attachments=self._attachments_from_json(session, list(row.attachments or []))[0],
                secure_links=tuple(str(value) for value in payload.get("secure_links", [])),
                dedupe_key=row.dedupe_key,
                correlation_id=row.correlation_id,
            )
        else:
            content, _ = self._prepare_payload(
                session,
                tender,
                verdict,
                test_mode,
                templates.NotificationKind(row.notification_kind),
                None,
            )
            links = self._links_from_json(row.links or [])
            content = replace(
                content,
                test_mode=test_mode,
                attached_filenames=tuple(
                    str(item.get("filename", "")) for item in (row.attachments or [])
                ),
                linked_documents=links,
            )
            message = self._compose_message(
                tender,
                content,
                selection,
                self._attachments_from_json(session, list(row.attachments or []))[1],
                links,
                row.dedupe_key,
            )
        try:
            message.validate()
        except ValueError:
            return None
        return message

    # ------------------------------------------------------------------ provider chain

    def _claim_breaker_probe(self, entry: ProviderEntry) -> bool:
        try:
            with self._session() as session:
                return MailProviderRepository(session).claim_breaker_probe(
                    entry.provider_id,
                    now_epoch=self._clock(),
                    cooldown_seconds=self._breaker_cooldown,
                )
        except (OperationalError, IntegrityError):
            log.warning(
                "mail provider breaker lease could not be reserved",
                extra={
                    "stage": "email",
                    "status": "warning",
                    "provider": entry.adapter.name,
                    "error_code": MAIL_BREAKER_OPEN,
                },
            )
            return False

    def _build_chain(
        self,
        provider_rows: list[MailProviderModel],
        *,
        preferred: int | None,
        correlation_id: str | None,
    ) -> ProviderChain:
        entries: list[ProviderEntry] = []
        listener = self._breaker_listener
        if listener is None and self._alert_hook is not None:
            listener = _AlertForwarder(self._alert_hook, correlation_id)
        for row in provider_rows:
            if not self._provider_domain_ready(row):
                continue
            try:
                adapter = self._adapter_for(row)
            except (MailError, ValueError, TypeError, SecretError, json.JSONDecodeError):
                adapter = None
            if adapter is None:
                try:
                    capabilities = _capabilities_for(row)
                except (TypeError, ValueError, KeyError):
                    capabilities = Capabilities()
                adapter = _UnavailableAdapter(row.name, capabilities)
            adapter.name = row.name
            breaker = CircuitBreaker.from_persisted(
                provider_name=row.name,
                state=row.breaker_state or "closed",
                breaker_until_epoch=_dt_epoch(row.breaker_until),
                consecutive_failures=row.breaker_failures or 0,
                failure_threshold=self._breaker_threshold,
                cooldown_seconds=self._breaker_cooldown,
                clock=self._clock,
                listener=listener,
            )
            entries.append(
                ProviderEntry(
                    provider_id=int(row.id),
                    adapter=adapter,
                    breaker=breaker,
                    active=bool(row.active),
                    priority=int(row.priority),
                )
            )
        if preferred is not None:
            first = next((entry for entry in entries if entry.provider_id == preferred), None)
            if first is not None:
                entries = [first, *(entry for entry in entries if entry is not first)]
        return ProviderChain(
            entries,
            retry_policy=self._retry_policy,
            sleeper=self._sleeper,
            clock=self._clock,
            usage_limiter=self._usage_limiter,
            usage_reserver=self._reserve_provider_usage,
            probe_claimer=self._claim_breaker_probe,
        )

    def _reserve_provider_usage(self, provider_id: int, capabilities: Capabilities) -> bool:
        try:
            with self._session() as session:
                return ProviderUsageRepository(session).try_consume(
                    provider_id, capabilities, now=datetime.fromtimestamp(self._clock(), UTC)
                )
        except (OperationalError, IntegrityError):
            # Fail closed across workers.  A missing/malformed usage table must not turn into
            # an unbounded provider send; the local limiter is only a second line of defence.
            log.warning(
                "durable provider usage reservation unavailable",
                extra={
                    "stage": "email",
                    "status": "warning",
                    "error_code": EMAIL_SEND_FAILED,
                },
            )
            return False

    def _provider_domain_ready(self, row: MailProviderModel) -> bool:
        try:
            capabilities = _capabilities_for(row)
        except (TypeError, ValueError, KeyError):
            return False
        return not capabilities.needs_verified_domain or self._verified_domain_ready(row)

    def _adapter_for(self, row: MailProviderModel) -> Any | None:
        """Build a configured adapter, decrypting credentials only at the last moment."""

        try:
            if not row.credentials_encrypted:
                raise configuration_error(f"provider {row.name} has no stored credentials")
            key = self._provider_key if self._provider_key is not None else get_master_key()
            plaintext = decrypt_secret(row.credentials_encrypted, key=key)
            credentials = json.loads(plaintext)
            if not isinstance(credentials, dict):
                raise configuration_error(f"provider {row.name} credentials are invalid")
            return self._provider_registry.build(
                row.provider_type,
                ProviderBuildContext(
                    credentials=credentials,
                    from_address=row.from_address,
                    from_name=row.from_name,
                    reply_to=row.reply_to,
                    capabilities=_capabilities_for(row),
                    client_factory=self._client_factory,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - provider construction is failure-contained
            log.warning(
                "mail provider %s cannot be used (%s)",
                row.name,
                type(exc).__name__,
                extra={
                    "stage": "email",
                    "status": "warning",
                    "provider": row.name,
                    "error_code": getattr(exc, "error_code", MAIL_CONFIGURATION_ERROR),
                },
            )
            return None

    def _persist_attempts(
        self,
        session: Session,
        notification_id: int,
        correlation_id: str | None,
        result: ChainResult,
    ) -> None:
        repo = NotificationRepository(session)
        for attempt in result.attempts:
            repo.record_attempt(
                notification_id=notification_id,
                provider_id=attempt.provider_id,
                provider_name=attempt.provider_name,
                correlation_id=correlation_id,
                chain_attempt=attempt,
            )

    def _persist_breakers(self, session: Session, chain: ProviderChain) -> None:
        repo = MailProviderRepository(session)
        dirty_ids = chain.breaker_dirty_ids
        for entry in chain.providers:
            if entry.provider_id not in dirty_ids:
                # Another worker may have atomically claimed a half-open probe after this
                # chain was built; never overwrite that newer lease with stale local state.
                continue
            row = repo.get(entry.provider_id)
            if row is None:
                continue
            row.breaker_state = entry.breaker.state.value
            row.breaker_until = (
                datetime.fromtimestamp(entry.breaker.open_until, tz=UTC)
                if entry.breaker.open_until is not None
                else None
            )
            row.breaker_failures = entry.breaker.consecutive_failures

    # ------------------------------------------------------------------ alerts/safety

    def _try_system_alert(
        self,
        *,
        correlation_id: str | None,
        source_name: str,
        error_code: str,
        message: str,
    ) -> bool:
        """Best-effort dev-list alert; the persisted banner is the reliable fallback."""

        try:
            with self._session() as session:
                selection = RecipientRouter(session).resolve_alert(
                    alert_type="email_send_failure", severity="critical", source_name=source_name
                )
                alert = EmailMessage(
                    to=selection.to,
                    cc=selection.cc,
                    bcc=selection.bcc,
                    subject=f"[ALERT][critical] email delivery: {error_code}",
                    text_body=(
                        "What broke: email provider chain\n"
                        f"Source: {source_name}\nError: {error_code}\n"
                        f"Correlation ID: {correlation_id or 'unavailable'}\n"
                        f"Detail: {message}"
                    ),
                    html_body=None,
                    dedupe_key=f"alert:{error_code}:{correlation_id or 'none'}",
                    correlation_id=correlation_id,
                )
                alert.validate()
                rows = MailProviderRepository(session).list_active_ordered()
            chain = self._build_chain(rows, preferred=None, correlation_id=correlation_id)
            if not chain.providers:
                return False
            result = chain.deliver(alert)
            with self._session() as session:
                self._persist_breakers(session, chain)
            return result.delivered
        except Exception:  # noqa: BLE001 - an alert must never mask the original failure
            log.warning(
                "could not attempt mail-layer alert",
                extra={
                    "stage": "email",
                    "status": "warning",
                    "correlation_id": correlation_id,
                    "error_code": error_code,
                },
            )
            return False

    @staticmethod
    def _assert_test_mode_safety(test_mode: bool, selection: RecipientSelection) -> None:
        if not test_mode or not selection.addresses:
            return
        if not TestModePolicy(True).guard_tender_recipients(list_types=list(selection.list_types)):
            raise NotificationSafetyError(
                NOTIFICATION_ROUTING_FAILED,
                "refusing send: business recipients selected while Test Mode is ON",
            )

    @staticmethod
    def _dedupe_key(tender: Tender, verdict: Verdict, selection: RecipientSelection) -> str:
        # Exactly the documented identity: tender + verdict + recipient-set hash.  Do not add
        # timestamps, provider names, or delivery attempts; those would defeat deduplication.
        raw = f"{tender.id}|{verdict.id}|{selection.recipient_set_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _message_payload(
        message: EmailMessage | None,
        selection: RecipientSelection,
        test_mode: bool,
        kind: templates.NotificationKind,
    ) -> dict[str, Any]:
        if message is None:
            return {
                "to": [],
                "cc": [],
                "bcc": [],
                "test_mode": test_mode,
                "kind": kind.value,
            }
        return {
            "to": list(selection.to),
            "cc": list(selection.cc),
            "bcc": list(selection.bcc),
            "subject": message.subject,
            "text_body": message.text_body,
            "html_body": message.html_body,
            "secure_links": list(message.secure_links),
            "test_mode": test_mode,
            "kind": kind.value,
        }

    def _build_default_signer(self, link_base_url: str | None) -> SecureLinkSigner:
        env = get_env_settings()
        secret = env.link_signing_secret or env.master_key
        if not secret:
            raise NotificationError(
                MAIL_CONFIGURATION_ERROR,
                "link signing secret is not configured "
                "(set TI_LINK_SIGNING_SECRET or TI_MASTER_KEY)",
            )
        return signer_from_secret(secret, link_base_url or env.link_base_url)


# ---------------------------------------------------------------------- module helpers


def _plan_for_row(
    row: MailProviderModel,
    plan: Any,
    pairs: list[tuple[Document, MailAttachment]],
) -> _DeliveryPlan:
    # Planner receives the same MailAttachment objects, so identity is safe even when two
    # documents share a basename.
    attached_ids = {id(attachment) for attachment in plan.attach}
    attach_pairs = tuple(pair for pair in pairs if id(pair[1]) in attached_ids)
    attached_names = {id(pair[1]) for pair in attach_pairs}
    link_docs = tuple((doc.id, att.filename) for doc, att in pairs if id(att) not in attached_names)
    return _DeliveryPlan(provider_id=int(row.id), attach_pairs=attach_pairs, link_docs=link_docs)


def _capabilities_for(row: MailProviderModel) -> Capabilities:
    raw = row.capabilities or {}
    if row.provider_type.lower() == "sendlib":
        values: dict[str, Any] = {
            "max_attachments": SENDLIB_FREE_CAPABILITIES.max_attachments,
            "max_attachment_mb": SENDLIB_FREE_CAPABILITIES.max_attachment_mb,
            "max_message_mb": SENDLIB_FREE_CAPABILITIES.max_message_mb,
            "daily_limit": SENDLIB_FREE_CAPABILITIES.daily_limit,
            "rate_limit_per_min": SENDLIB_FREE_CAPABILITIES.rate_limit_per_min,
            "needs_verified_domain": SENDLIB_FREE_CAPABILITIES.needs_verified_domain,
        }
        values.update(
            {key: raw[key] for key in _CAPABILITY_KEYS if key in raw and raw[key] is not None}
        )
    else:
        values = {key: raw[key] for key in _CAPABILITY_KEYS if key in raw}
    return Capabilities(**values)


def _as_items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, dict)):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


def _safe_filename(name: str) -> str:
    value = (name or "document").replace("\\", "_").replace("/", "_").replace("\x00", "_")
    value = "".join(char for char in value if char.isprintable()).strip()
    if value in {"", ".", ".."}:
        return "document"
    return value


def _dt_epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _selection_from_snapshot(snapshot: list[dict]) -> RecipientSelection:
    by_delivery: dict[str, list[str]] = {"to": [], "cc": [], "bcc": []}
    for item in snapshot:
        target = by_delivery.get(str(item.get("delivery", "to")))
        email = str(item.get("email", ""))
        if target is not None and email:
            target.append(email)
    return RecipientSelection(
        to=tuple(by_delivery["to"]),
        cc=tuple(by_delivery["cc"]),
        bcc=tuple(by_delivery["bcc"]),
        snapshot=tuple(snapshot),
    )


def _links_from_json(stored: list[dict]) -> tuple[templates.LinkedDocument, ...]:
    links: list[templates.LinkedDocument] = []
    for item in stored:
        try:
            expires = datetime.fromisoformat(str(item["expires_at"]))
            url = str(item["url"])
            filename = str(item["filename"])
            expiry_days = int(item.get("expiry_days", 14))
        except (KeyError, TypeError, ValueError):
            continue
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        links.append(
            templates.LinkedDocument(
                filename=filename,
                url=url,
                expires_at=expires,
                expiry_days=expiry_days,
            )
        )
    return tuple(links)


def _result_message(result: ChainResult) -> str:
    codes = ", ".join(attempt.error_code or "-" for attempt in result.attempts)
    return f"all providers failed ({codes or result.error_code or 'unknown'})"


__all__ = [
    "FlushSummary",
    "MailAlertHook",
    "MailAlertNotice",
    "NotificationError",
    "NotificationOutcome",
    "NotificationSafetyError",
    "NotificationService",
    "NullMailAlertHook",
]

"""Persistence for notification outbox rows, attempts, and provider breaker state.

Repositories are session-scoped and never commit.  The service uses short transactions around
reservation/claim/finalisation so a provider request is never made while the only copy of a
pending notification is held inside an uncommitted transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from tender_intelligence.core.errors import EMAIL_SEND_FAILED, is_valid_error_code
from tender_intelligence.db.models.mail import (
    MailProvider,
    MailProviderUsage,
    NotificationAttempt,
    NotificationLog,
)
from tender_intelligence.db.repositories.base import PersistenceError, Repository
from tender_intelligence.mail.chain import ChainAttempt
from tender_intelligence.mail.provider import Capabilities

# One initial send plus one automatic retry is the safety default for an ambiguous provider
# timeout.  Further retries require an explicit operator requeue; a possible duplicate must not
# turn into an unbounded stream of uncertain deliveries.
MAX_AMBIGUOUS_AUTOMATIC_SENDS = 2


class NotificationRepository(Repository):
    """Row-level persistence for the durable notification outbox."""

    def create_pending(
        self,
        *,
        dedupe_key: str,
        tender_id: int | None,
        verdict_id: int | None,
        correlation_id: str | None,
        recipients_snapshot: list[dict],
        attachments: list[dict],
        links: list[dict],
        message_payload: dict[str, Any] | None = None,
        notification_kind: str = "new",
        priority: int = 100,
    ) -> NotificationLog:
        """Insert a pre-send outbox row and flush it for the caller's transaction."""

        if not dedupe_key:
            raise PersistenceError("dedupe_key must not be empty")
        row = NotificationLog(
            tender_id=tender_id,
            verdict_id=verdict_id,
            correlation_id=correlation_id,
            recipients_snapshot=recipients_snapshot,
            attachments=attachments,
            links=links,
            message_payload=message_payload,
            notification_kind=notification_kind,
            priority=priority,
            status="pending",
            dedupe_key=dedupe_key,
            possible_duplicate=False,
            attempt_count=0,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_dedupe_key(self, dedupe_key: str) -> NotificationLog | None:
        return self.session.scalars(
            select(NotificationLog).where(NotificationLog.dedupe_key == dedupe_key)
        ).first()

    def get(self, log_id: int) -> NotificationLog | None:
        return self.session.get(NotificationLog, log_id)

    def get_for_update(self, log_id: int) -> NotificationLog | None:
        return self.session.scalars(
            select(NotificationLog).where(NotificationLog.id == log_id).with_for_update()
        ).first()

    def list_pending_retry(self, limit: int = 50) -> list[NotificationLog]:
        """List durable rows waiting for a scheduled retry, oldest first."""

        return list(
            self.session.scalars(
                select(NotificationLog)
                .where(
                    NotificationLog.status.in_(("pending", "pending_retry", "possible_duplicate"))
                )
                .order_by(NotificationLog.created_at, NotificationLog.id)
                .limit(limit)
            ).all()
        )

    def claim_pending(
        self,
        *,
        limit: int = 10,
        lease_seconds: float = 300.0,
        now: datetime | None = None,
    ) -> list[NotificationLog]:
        """Atomically claim retryable rows for delivery.

        A stale ``sending`` claim is recoverable after a worker crash.  On PostgreSQL the
        ``FOR UPDATE SKIP LOCKED`` clause prevents two workers claiming the same row; SQLite
        ignores the clause but its write lock still serialises the update, and the service's
        in-process guard covers the offline test harness.
        """

        moment = _utc(now)
        lease_until = moment + timedelta(seconds=max(1.0, lease_seconds))
        self._recover_stale_sending(moment)
        rows = list(
            self.session.scalars(
                select(NotificationLog)
                .where(
                    or_(
                        (
                            (NotificationLog.status == "pending")
                            & (
                                NotificationLog.created_at
                                <= moment - timedelta(seconds=lease_seconds)
                            )
                        ),
                        (
                            (NotificationLog.status == "pending_retry")
                            & (
                                NotificationLog.next_retry_at.is_(None)
                                | (NotificationLog.next_retry_at <= moment)
                            )
                        ),
                        (NotificationLog.status == "sending")
                        & (NotificationLog.claim_until.is_not(None))
                        & (NotificationLog.claim_until <= moment),
                    )
                )
                .order_by(NotificationLog.priority, NotificationLog.created_at, NotificationLog.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        claimed: list[NotificationLog] = []
        for row in rows:
            row.status = "sending"
            row.claim_token = uuid.uuid4().hex
            row.claim_until = lease_until
            row.attempt_count = int(row.attempt_count or 0) + 1
            claimed.append(row)
        self.session.flush()
        return claimed

    def begin_send(
        self,
        log_id: int,
        *,
        claim_token: str,
        lease_seconds: float = 300.0,
    ) -> NotificationLog:
        """Durably mark a reserved row as in-flight before any network call."""

        row = self._get_for_update(log_id)
        if row.status == "sent":
            raise PersistenceError("notification has already been sent")
        if row.status == "sending" and row.claim_token:
            raise PersistenceError("notification is already claimed by another worker")
        if row.status not in {"pending", "pending_retry"}:
            raise PersistenceError(f"notification cannot transition from {row.status!r} to sending")
        row.status = "sending"
        row.claim_token = claim_token
        row.claim_until = _utc(None) + timedelta(seconds=max(1.0, lease_seconds))
        row.attempt_count = int(row.attempt_count or 0) + 1
        self.session.flush()
        return row

    def renew_claim(
        self,
        log_id: int,
        *,
        claim_token: str,
        lease_seconds: float = 300.0,
    ) -> NotificationLog:
        """Extend a lease only when the caller still owns the row."""

        row = self._get_for_update(log_id)
        if row.status != "sending" or row.claim_token != claim_token:
            raise PersistenceError("notification claim is no longer owned by this worker")
        row.claim_until = _utc(None) + timedelta(seconds=max(1.0, lease_seconds))
        self.session.flush()
        return row

    def mark_sent(
        self,
        log_id: int,
        *,
        provider_used: str,
        provider_id: int | None,
        attachments: list[dict],
        links: list[dict],
        possible_duplicate: bool,
        claim_token: str | None = None,
    ) -> NotificationLog:
        row = self._get_for_update(log_id)
        if claim_token is not None and row.claim_token not in (None, claim_token):
            raise PersistenceError("notification claim is no longer owned by this worker")
        row.status = "sent"
        row.provider_used = provider_used
        row.sent_at = datetime.now(UTC)
        row.attachments = attachments
        row.links = links
        row.possible_duplicate = bool(possible_duplicate or row.possible_duplicate)
        row.error = None
        row.last_error_code = None
        row.next_retry_at = None
        row.claim_token = None
        row.claim_until = None
        self.session.flush()
        return row

    def keep_pending(
        self,
        log_id: int,
        *,
        error_code: str,
        error_message: str,
        possible_duplicate: bool = False,
        next_retry_at: datetime | None = None,
        claim_token: str | None = None,
    ) -> NotificationLog:
        """Leave a recoverable outbox row after a whole-chain failure."""

        row = self._get_for_update(log_id)
        if claim_token is not None and row.claim_token not in (None, claim_token):
            raise PersistenceError("notification claim is no longer owned by this worker")
        ambiguous = bool(possible_duplicate or row.possible_duplicate)
        row.possible_duplicate = ambiguous
        row.error = f"{error_code}: {error_message}"[:2000]
        row.last_error_code = error_code if is_valid_error_code(error_code) else EMAIL_SEND_FAILED
        if ambiguous and int(row.attempt_count or 0) >= MAX_AMBIGUOUS_AUTOMATIC_SENDS:
            # An uncertain timeout may have reached the provider.  Quarantine after the bounded
            # automatic window instead of repeatedly sending an unbounded number of messages
            # whose delivery state cannot be known.  An operator must explicitly requeue it.
            row.status = "possible_duplicate"
            row.next_retry_at = None
        else:
            row.status = "pending_retry"
            if next_retry_at is None:
                # [PROPOSED implementation default] Whole-chain retries are scheduled rather
                # than spun in a tight loop.  The provider retry policy remains bounded per call;
                # this backoff is the durable outbox schedule and can be made configurable later.
                delay_seconds = (
                    0
                    if row.attempt_count <= 1
                    else min(3600, 30 * (2 ** min(10, row.attempt_count - 2)))
                )
                row.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            else:
                row.next_retry_at = next_retry_at
        row.claim_token = None
        row.claim_until = None
        self.session.flush()
        return row

    def requeue_possible_duplicate(
        self,
        log_id: int,
        *,
        next_retry_at: datetime | None = None,
    ) -> NotificationLog:
        """Explicitly release a quarantined ambiguous row for one operator-approved retry."""

        row = self._get_for_update(log_id)
        if row.status != "possible_duplicate":
            raise PersistenceError("notification is not quarantined for possible duplicate")
        row.status = "pending_retry"
        row.next_retry_at = next_retry_at or datetime.now(UTC)
        row.claim_token = None
        row.claim_until = None
        self.session.flush()
        return row

    def mark_legacy_sent(self, log_id: int) -> NotificationLog:
        """Compatibility alias for callers that only need the state transition."""

        return self.mark_sent(
            log_id,
            provider_used="unknown",
            provider_id=None,
            attachments=[],
            links=[],
            possible_duplicate=False,
        )

    def record_attempt(
        self,
        *,
        notification_id: int,
        provider_id: int | None,
        provider_name: str,
        correlation_id: str | None,
        chain_attempt: ChainAttempt,
    ) -> NotificationAttempt:
        """Persist one provider attempt without provider payloads or credentials."""

        if not isinstance(chain_attempt, ChainAttempt):
            raise TypeError(f"expected ChainAttempt, got {type(chain_attempt).__name__}")
        code = chain_attempt.error_code
        if not is_valid_error_code(code or ""):
            code = EMAIL_SEND_FAILED
        attempt = NotificationAttempt(
            notification_id=notification_id,
            correlation_id=correlation_id,
            provider_id=provider_id,
            provider_name=provider_name,
            status=chain_attempt.status,
            error_code=code,
            duration_ms=chain_attempt.duration_ms,
            attempted_at=datetime.now(UTC),
            attempt_number=max(1, int(chain_attempt.attempt_number)),
            possible_duplicate=chain_attempt.possible_duplicate,
            provider_message_id=chain_attempt.provider_message_id,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    @staticmethod
    def safe_view(row: NotificationLog) -> dict[str, Any]:
        """Return operational metadata without message bodies or bearer-signed URLs."""

        links = []
        for item in row.links or []:
            if not isinstance(item, dict):
                continue
            links.append(
                {
                    "document_id": item.get("document_id"),
                    "filename": item.get("filename"),
                    "expires_at": item.get("expires_at"),
                }
            )
        return {
            "id": row.id,
            "tender_id": row.tender_id,
            "verdict_id": row.verdict_id,
            "correlation_id": row.correlation_id,
            "status": row.status,
            "provider_used": row.provider_used,
            "dedupe_key": row.dedupe_key,
            "possible_duplicate": row.possible_duplicate,
            "recipients": [
                {"email": item.get("email"), "delivery": item.get("delivery")}
                for item in (row.recipients_snapshot or [])
                if isinstance(item, dict)
            ],
            "attachments": [
                {"document_id": item.get("document_id"), "filename": item.get("filename")}
                for item in (row.attachments or [])
                if isinstance(item, dict)
            ],
            "links": links,
        }

    def count_recoverable(self) -> int:
        return len(
            self.session.scalars(
                select(NotificationLog.id).where(
                    NotificationLog.status.in_(("pending", "pending_retry", "possible_duplicate"))
                )
            ).all()
        )

    def _recover_stale_sending(self, moment: datetime) -> None:
        rows = self.session.scalars(
            select(NotificationLog).where(
                NotificationLog.status == "sending",
                NotificationLog.claim_until.is_not(None),
                NotificationLog.claim_until <= moment,
            )
        ).all()
        for row in rows:
            row.status = "pending_retry"
            row.claim_token = None
            row.claim_until = None
            # The provider may have accepted the request before the worker disappeared.  A
            # stale claim is therefore an explicit duplicate risk, not a clean failure.
            row.possible_duplicate = True
            row.error = "worker lease expired after an in-flight send; possible duplicate"
            row.last_error_code = "email_send_failed"
        if rows:
            self.session.flush()

    def _get_for_update(self, log_id: int) -> NotificationLog:
        statement = select(NotificationLog).where(NotificationLog.id == log_id).with_for_update()
        row = self.session.scalars(statement).first()
        if row is None:
            raise PersistenceError(f"notification log {log_id} not found")
        return row


class MailProviderRepository(Repository):
    """Configured providers: ordered reads and safe breaker persistence."""

    def list_active_ordered(self) -> list[MailProvider]:
        """Return all providers in priority/id order; inactive rows remain visible."""

        return list(
            self.session.scalars(
                select(MailProvider).order_by(MailProvider.priority, MailProvider.id)
            ).all()
        )

    def list_active(self) -> list[MailProvider]:
        return [row for row in self.list_active_ordered() if row.active]

    def get(self, provider_id: int) -> MailProvider | None:
        return self.session.get(MailProvider, provider_id)

    def claim_breaker_probe(
        self,
        provider_id: int,
        *,
        now_epoch: float,
        cooldown_seconds: float,
    ) -> bool:
        """Atomically reserve one cooldown probe for a persisted breaker.

        The row lock prevents two workers from both observing an expired OPEN/HALF_OPEN lease.
        The in-memory breaker is advanced separately by ``ProviderChain`` after this claim.
        """

        row = self.session.scalars(
            select(MailProvider)
            .where(MailProvider.id == provider_id)
            .with_for_update()
        ).first()
        if row is None:
            return False
        state = str(row.breaker_state or "closed")
        if state == "closed":
            return True
        if state not in {"open", "half_open"}:
            return False
        now = datetime.fromtimestamp(now_epoch, UTC)
        current_until = _as_utc(row.breaker_until) if row.breaker_until is not None else None
        if current_until is not None and current_until > now:
            return False
        row.breaker_state = "half_open"
        row.breaker_until = now + timedelta(seconds=max(0.0, float(cooldown_seconds)))
        self.session.flush()
        return True

    def with_breaker_open(self) -> list[MailProvider]:
        return list(
            self.session.scalars(
                select(MailProvider).where(MailProvider.breaker_state == "open")
            ).all()
        )

    @staticmethod
    def safe_view(row: MailProvider) -> dict[str, Any]:
        """Return provider metadata suitable for an API response; never decrypt credentials."""

        return {
            "id": row.id,
            "name": row.name,
            "provider_type": row.provider_type,
            "from_address": row.from_address,
            "from_name": row.from_name,
            "reply_to": row.reply_to,
            "priority": row.priority,
            "active": row.active,
            "capabilities": {
                key: (row.capabilities or {})[key]
                for key in (
                    "max_attachments",
                    "max_attachment_mb",
                    "max_message_mb",
                    "daily_limit",
                    "rate_limit_per_min",
                    "needs_verified_domain",
                )
                if key in (row.capabilities or {})
            },
            "breaker_state": row.breaker_state,
            "breaker_until": row.breaker_until.isoformat() if row.breaker_until else None,
            "credentials_set": bool(row.credentials_encrypted),
        }


class ProviderUsageRepository(Repository):
    """Atomically account provider requests against configured daily/rate limits."""

    def try_consume(
        self,
        provider_id: int,
        capabilities: Capabilities,
        *,
        now: datetime | None = None,
    ) -> bool:
        moment = _utc(now)
        minute_start = moment.replace(second=0, microsecond=0)
        day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.session.scalars(
            select(MailProviderUsage)
            .where(MailProviderUsage.provider_id == provider_id)
            .with_for_update()
        ).first()
        if row is None:
            try:
                with self.session.begin_nested():
                    row = MailProviderUsage(provider_id=provider_id)
                    self.session.add(row)
                    self.session.flush()
            except IntegrityError:
                row = self.session.scalars(
                    select(MailProviderUsage)
                    .where(MailProviderUsage.provider_id == provider_id)
                    .with_for_update()
                ).first()
                if row is None:
                    return False
        if row.day_started is None or _as_utc(row.day_started) < day_start:
            row.day_started = day_start
            row.day_count = 0
        if row.minute_started is None or _as_utc(row.minute_started) < minute_start:
            row.minute_started = minute_start
            row.minute_count = 0
        daily_limit = capabilities.daily_limit
        rate_limit = capabilities.rate_limit_per_min
        if daily_limit is not None and row.day_count >= daily_limit:
            return False
        if rate_limit is not None and row.minute_count >= rate_limit:
            return False
        row.day_count = int(row.day_count or 0) + 1
        row.minute_count = int(row.minute_count or 0) + 1
        self.session.flush()
        return True


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "MAX_AMBIGUOUS_AUTOMATIC_SENDS",
    "MailProviderRepository",
    "NotificationRepository",
    "ProviderUsageRepository",
]

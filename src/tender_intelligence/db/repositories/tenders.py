""":mod:`tender_intelligence.db.repositories.tenders` — Tender persistence (prompt 06).

Tender rows are the prompt-05 seen store keyed on ``(source_id, external_id)``
(docs/03 §3.2; v1.1 §5.2). This repository owns:

- identity lookups (``get_by_identity`` / ``get_many_by_identity``) — the seen store,
- the atomic **claim**: insert a NEW row inside a savepoint so a concurrent claim of the
  same ``(source_id, external_id)`` is caught by ``uq_tenders_source_external`` and
  resolved into the winner (:class:`TenderClaim`) without a lost update,
- row updates that persist a dedup UPDATE decision (mutable fields + ``is_update`` +
  ``status`` transition through the status guard),
- guarded status transitions and correlation/deadline-indexed reads.

The repository never commits and never classifies — the dedup service owns the decision,
the commit boundary, and the race log (classify.compare lives in prompt 05).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tender_intelligence.core.errors import PERSISTENCE_CONSTRAINT_VIOLATION
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories.base import PersistenceError, Repository
from tender_intelligence.db.repositories.status import assert_valid_transition
from tender_intelligence.interfaces.source import TenderListing


@dataclass(frozen=True)
class TenderClaim:
    """Result of an atomic claim: the winning row and whether we created it (prompt 06 §4)."""

    tender: Tender
    created: bool


class TenderRepository(Repository):
    """Row-level reads/writes for :class:`Tender` (prompt 06 §4, docs/03 §3.2)."""

    def get(self, tender_id: int) -> Tender | None:
        return self.session.get(Tender, tender_id)

    def get_by_identity(self, source_id: int, external_id: str) -> Tender | None:
        return self.session.scalar(
            select(Tender).where(Tender.source_id == source_id, Tender.external_id == external_id)
        )

    def get_many_by_identity(self, source_id: int, external_ids: Any) -> dict[str, Tender]:
        ids = [ext for ext in external_ids if isinstance(ext, str) and ext.strip()]
        if not ids:
            return {}
        rows = self.session.scalars(
            select(Tender).where(Tender.source_id == source_id, Tender.external_id.in_(ids))
        ).all()
        return {t.external_id: t for t in rows}

    def exists(self, source_id: int, external_id: str) -> bool:
        return (
            self.session.scalar(
                select(Tender.id).where(
                    Tender.source_id == source_id, Tender.external_id == external_id
                )
            )
            is not None
        )

    def claim_new(
        self,
        source_id: int,
        listing: TenderListing,
        correlation_id: str,
    ) -> TenderClaim:
        """Atomically claim ``listing`` for *source_id*, resolving the unique-constraint race.

        Attempts the insert under a SAVEPOINT so the (source_id, external_id) unique
        constraint catches a concurrent winner. On a race it rolls back the savepoint and
        re-selects the winner, mirroring prompt 05's crash-safe dedup transaction
        (prompt 05 §8 / prompt 06 §4).
        """
        tender = Tender(
            source_id=source_id,
            external_id=listing.external_id,
            url=listing.url,
            title=listing.title,
            published_date=listing.published_at,
            deadline=listing.deadline_at,
            deadline_timezone=listing.deadline_timezone,
            raw_metadata=dict(listing.raw_metadata),
            status="new",
            first_seen_at=datetime.now(UTC),
            correlation_id=correlation_id,
            is_update=False,
        )
        try:
            with self.session.begin_nested():
                self.session.add(tender)
                self.session.flush()
        except IntegrityError:
            winner = self.get_by_identity(source_id, listing.external_id)
            if winner is None:
                raise PersistenceError(
                    f"unique-constraint race with no winner for {listing.external_id!r}",
                    error_code=PERSISTENCE_CONSTRAINT_VIOLATION,
                    context={"source_id": source_id, "external_id": listing.external_id},
                ) from None
            return TenderClaim(tender=winner, created=False)
        return TenderClaim(tender=tender, created=True)

    def apply_update(self, tender: Tender, listing: TenderListing) -> None:
        """Persist a dedup UPDATE decision on *tender* (prompt 06 §4, docs/04 §4.14)."""
        tender.title = listing.title
        tender.url = listing.url
        tender.published_date = listing.published_at
        tender.deadline = listing.deadline_at
        tender.deadline_timezone = listing.deadline_timezone
        meta = dict(tender.raw_metadata or {})
        meta.update(listing.raw_metadata or {})
        tender.raw_metadata = meta
        tender.is_update = True
        self.set_status(tender, "updated")

    def set_status(self, tender: Tender, new_status: str) -> None:
        """Set *new_status* guarded by the documented transition matrix (prompt 06 §6)."""
        assert_valid_transition(tender, new_status)
        tender.status = new_status

    def list_by_source(self, source_id: int) -> list[Tender]:
        return list(
            self.session.scalars(
                select(Tender).where(Tender.source_id == source_id).order_by(Tender.first_seen_at)
            ).all()
        )

    def list_by_status(self, status: str) -> list[Tender]:
        return list(
            self.session.scalars(
                select(Tender).where(Tender.status == status).order_by(Tender.first_seen_at)
            ).all()
        )

    def list_unprocessed(
        self,
        source_id: int,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[Tender]:
        """Tenders this source persisted, never verdicted, and still biddable.

        A row still at ``status='new'`` is one dedup claimed (prompt 06 §4) whose downstream
        run never completed — the process was interrupted, the run crashed, or a stage aborted
        before the verdict moved it to ``processed``/``verdict_failed``. Those rows are invisible
        to a plain crawl: ``compare`` finds the listing identical, so dedup classifies it
        ``UNCHANGED`` and it is excluded from the work set (prompt 10 §13), which would strand
        it permanently.

        Rows whose deadline has already passed are excluded as well. Re-driving those would
        spend an LLM call and a raw-notice email on a tender nobody can bid on — pure noise, and
        with a large backlog enough of it to make the signal unreadable. ``deadline IS NULL`` is
        kept: a tender with no known deadline is still worth assessing, and docs/05 §5.2 treats
        an unresolved deadline as a gap to report rather than a reason to skip.

        Ordering by ``first_seen_at`` drains the oldest backlog first, and *limit* keeps one
        run's recovery bounded rather than re-driving an arbitrarily large backlog at once.
        """
        moment = now or datetime.now(UTC)
        statement = select(Tender).where(
            Tender.source_id == source_id,
            Tender.status == "new",
            (Tender.deadline.is_(None)) | (Tender.deadline > moment),
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(
            self.session.scalars(statement.order_by(Tender.first_seen_at, Tender.id)).all()
        )

    def list_expired_unprocessed(
        self,
        *,
        now: datetime | None = None,
        source_ids: Sequence[int] | None = None,
    ) -> list[Tender]:
        """Unprocessed tenders whose deadline has already passed — dead rows awaiting retirement.

        These are the ones :meth:`list_unprocessed` deliberately skips. They are surfaced so an
        operator action (or a maintenance sweep) can move them to the terminal ``expired`` status
        rather than leaving them looking like pending work forever.
        """
        moment = now or datetime.now(UTC)
        statement = select(Tender).where(
            Tender.status == "new",
            Tender.deadline.is_not(None),
            Tender.deadline <= moment,
        )
        if source_ids is not None:
            ids = [int(value) for value in source_ids]
            if not ids:
                return []
            statement = statement.where(Tender.source_id.in_(ids))
        return list(
            self.session.scalars(statement.order_by(Tender.source_id, Tender.id)).all()
        )

    def get_by_correlation(self, correlation_id: str) -> Tender | None:
        return self.session.scalar(select(Tender).where(Tender.correlation_id == correlation_id))

    def apply_deadline_resolution(
        self,
        tender: Tender,
        result: "DeadlineResult",  # type: ignore[name-defined]
        resolved_at: "datetime",   # type: ignore[name-defined]
    ) -> None:
        """Persist the outcome of deadline resolution (Prompt 12.1).

        Upserts one ``TenderDeadlineResolution`` row (migration 0010, separate table).
        Never touches ``Tender.deadline`` / ``Tender.deadline_timezone`` — those are owned
        by Prompt 05 dedup and compared by the classify module for DEADLINE_CHANGED.

        ``resolved_at`` is passed in by the caller so it can be stamped consistently
        in both the DB row and any audit log lines within the same operation.
        """
        from tender_intelligence.deadline.model import RESOLVED  # local import avoids circular
        from tender_intelligence.db.models.deadline import TenderDeadlineResolution

        deadline_resolved = None
        if result.status == RESOLVED and result.deadline_utc is not None:
            deadline_resolved = result.deadline_utc

        # Upsert: update existing row or create a new one
        existing = self.session.scalar(
            select(TenderDeadlineResolution).where(
                TenderDeadlineResolution.tender_id == tender.id
            )
        )
        if existing is not None:
            existing.deadline_resolved = deadline_resolved
            existing.deadline_source = result.source
            existing.deadline_timezone = result.deadline_timezone
            existing.deadline_evidence = result.evidence or {}
            existing.deadline_resolved_at = resolved_at
        else:
            row = TenderDeadlineResolution(
                tender_id=tender.id,
                deadline_resolved=deadline_resolved,
                deadline_source=result.source,
                deadline_timezone=result.deadline_timezone,
                deadline_evidence=result.evidence or {},
                deadline_resolved_at=resolved_at,
            )
            self.session.add(row)

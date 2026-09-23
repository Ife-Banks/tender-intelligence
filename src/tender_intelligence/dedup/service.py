""":mod:`tender_intelligence.dedup.service` — new-listing detection service (prompt 05).

The service turns discovery candidates (Prompt 04's ``TenderListing`` contract) into a
decision (:class:`DedupResult`) using persisted seen-tender rows keyed on
``(source_id, external_id)`` — idempotent, crash-safe and concurrency-safe by construction.

Crash-safety point (prompt 05 §3): a candidate becomes **seen** inside ``_dedup_transaction``
in the same commit that releases downstream work. A crash before that commit leaves nothing
persisted (the candidate is reclassified NEXT on re-run — correct, it was never accepted); a
crash after the commit means the seen row is durable, so the next run sees UNCHANGED and never
re-notifies (docs/04 §3, §4.7).

Run-row ownership is the one thing prompt 10 changes about this service, and only additively:
standalone, dedup creates/completes/error-marks its own ``RunHistory`` row exactly as before;
under the orchestrator the row spans stages 04–09, so the coordinator creates it and dedup is
handed ``run_history_id``. See :meth:`DedupService.run`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.core.correlation import (
    correlation_context,
    get_correlation_id,
    new_correlation_id,
)
from tender_intelligence.core.errors import (
    DEDUP_INVALID_STATE,
    DEDUP_MALFORMED_CANDIDATE,
    DEDUP_MISSING_IDENTITY,
    DEDUP_TRANSACTION_FAILED,
)
from tender_intelligence.db.models.runs import RunHistory
from tender_intelligence.db.models.tenders import Tender
from tender_intelligence.db.repositories import (
    PersistenceError,
    RunHistoryRepository,
    SourceRepository,
    TenderRepository,
)
from tender_intelligence.dedup.classify import (
    NEW,
    UNCHANGED,
    UPDATE,
    ChangePolicy,
    Comparison,
    compare,
)
from tender_intelligence.interfaces.source import TenderListing

log = logging.getLogger("tender_intelligence.dedup")

RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
ERRORED = "ERRORED"


class DedupError(Exception):
    """Structured dedup failure carrying a machine-readable code (prompt 05 §13)."""

    def __init__(
        self,
        message: str,
        error_code: str = DEDUP_TRANSACTION_FAILED,
        *,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.context = context or {}


@dataclass(frozen=True)
class DedupOutcome:
    """What happened to one candidate (prompt 05 §4, §11)."""

    classification: str
    listing: TenderListing
    tender_id: int
    correlation_id: str
    material_change: bool
    change_types: tuple[str, ...] = ()
    change_details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DedupResult:
    """Clean pipeline seam consumers (prompt 10) can drive (prompt 05 §11)."""

    run_correlation_id: str
    run_history_id: int
    run_status: str
    new_listings: tuple[TenderListing, ...]
    updates: tuple[DedupOutcome, ...]
    unchanged_count: int
    new_count: int
    update_count: int
    duplicate_count: int
    correlation_map: dict[str, str]


def derive_run_status(run: RunHistory) -> str:
    """Reconstruct the run lifecycle from persisted fields (docs/04 §4.8)."""
    if run.ended_at is None:
        return RUNNING
    return ERRORED if run.error_count > 0 else COMPLETED


class DedupService:
    """Per-source deduplication against persisted seen tenders (prompt 05)."""

    STAGE = "dedup"

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        change_policy: ChangePolicy | None = None,
    ) -> None:
        self._maker = session_factory
        self._policy = change_policy or ChangePolicy.default()

    def run(
        self,
        source_id: int,
        listings: Sequence[TenderListing],
        *,
        run_correlation_id: str | None = None,
        run_history_id: int | None = None,
        dry_run: bool = False,
    ) -> DedupResult:
        """Deduplicate *listings* for *source_id* and return the downstream work set.

        Two optional, backward-compatible modes let the orchestrator (prompt 10) own the run
        row without dedup losing its classification ownership (prompt 10 §5, §20):

        * *run_history_id* — the orchestrator already opened the row at run start, because
          that row spans stages 04–09. Dedup then neither creates, completes nor error-marks
          it; it only writes the counts onto it and reports its still-RUNNING status.
        * *dry_run* — classify for real, then roll the whole transaction back, so nothing is
          written: no seen-tender rows, no counts. ``claim_new`` uses a SAVEPOINT, so the
          discard is total (prompt 10 §14).

        With neither argument the behaviour is exactly as before: dedup creates, completes
        and error-marks its own row.
        """
        run_cid = run_correlation_id or get_correlation_id() or new_correlation_id()
        with correlation_context(run_cid):
            return self._run(
                source_id,
                list(listings),
                run_cid,
                run_history_id=run_history_id,
                dry_run=dry_run,
            )

    # -- internals ---------------------------------------------------------

    def _run(
        self,
        source_id: int,
        listings: list[TenderListing],
        run_cid: str,
        *,
        run_history_id: int | None = None,
        dry_run: bool = False,
    ) -> DedupResult:
        # Dedup owns the run row only when nobody else handed it one and we are not dry.
        owns_run = run_history_id is None and not dry_run
        session = self._maker()
        run_row: RunHistory | None = None
        try:
            run_row = self._open_run(session, source_id, run_cid, run_history_id, dry_run)
            result = self._dedup_transaction(
                session, source_id, listings, run_row, owns_run=owns_run, dry_run=dry_run
            )
            log.info(
                "dedup complete: %d new, %d update, %d unchanged, %d duplicate(s)",
                result.new_count,
                result.update_count,
                result.unchanged_count,
                result.duplicate_count,
                extra={
                    "stage": self.STAGE,
                    "status": "done",
                    "correlation_id": run_cid,
                    "source_id": source_id,
                },
            )
            return result
        except DedupError:
            self._mark_errored(session, run_row, run_cid, owns_run=owns_run)
            raise
        except PersistenceError as exc:
            session.rollback()
            self._mark_errored(session, run_row, run_cid, owns_run=owns_run)
            raise DedupError(
                exc.message, error_code=exc.error_code, context=exc.context
            ) from exc
        except (IntegrityError, OperationalError) as exc:
            session.rollback()
            self._mark_errored(session, run_row, run_cid, owns_run=owns_run)
            raise DedupError(
                f"dedup transaction failed: {exc}",
                error_code=DEDUP_TRANSACTION_FAILED,
                context={"source_id": source_id, "correlation_id": run_cid},
            ) from exc
        except Exception as exc:
            session.rollback()
            self._mark_errored(session, run_row, run_cid, owns_run=owns_run)
            raise DedupError(
                f"unexpected dedup failure: {exc}",
                context={"source_id": source_id, "correlation_id": run_cid},
            ) from exc
        finally:
            session.close()

    def _open_run(
        self,
        session: Session,
        source_id: int,
        run_cid: str,
        run_history_id: int | None = None,
        dry_run: bool = False,
    ) -> RunHistory:
        """Resolve the run row dedup will classify against.

        Standalone, dedup creates and commits a RUNNING row at run start (reconstructable
        after a crash, docs/04 §4.8). Under the orchestrator the row already exists and is
        only fetched. In dry-run mode a row is flushed for its primary key but never
        committed — it vanishes with the rollback at the end of the transaction.
        """
        if not SourceRepository(session).exists(source_id):
            raise DedupError(
                f"unknown source_id {source_id}",
                error_code=DEDUP_INVALID_STATE,
                context={"source_id": source_id},
            )
        repo = RunHistoryRepository(session)
        if run_history_id is not None:
            run_row = repo.get(run_history_id)
            if run_row is None:
                raise DedupError(
                    f"unknown run_history_id {run_history_id}",
                    error_code=DEDUP_INVALID_STATE,
                    context={"source_id": source_id, "run_history_id": run_history_id},
                )
            return run_row
        run_row = repo.create(source_id, correlation_id=run_cid)
        if dry_run:
            session.flush()
            return run_row
        session.commit()
        return run_row

    def _mark_errored(
        self,
        session: Session,
        run_row: RunHistory | None,
        run_cid: str,
        *,
        owns_run: bool = True,
    ) -> None:
        """Persist RUNNING → ERRORED in its own transaction (docs/04 §4.8, §4.3).

        Only when dedup owns the row: under orchestration the run does not end here — a
        stage-05 failure is finalised by the coordinator, which also has the stage map and
        the failed-stage attribution (prompt 10 §5).
        """
        if run_row is None:
            return
        try:
            session.rollback()
            if not owns_run:
                return
            RunHistoryRepository(session).mark_errored(run_row, run_cid)
            session.commit()
        except Exception:
            log.exception(
                "could not persist errored RunHistory",
                extra={"stage": self.STAGE, "status": "error", "correlation_id": run_cid},
            )

    def _dedup_transaction(
        self,
        session: Session,
        source_id: int,
        listings: list[TenderListing],
        run_row: RunHistory,
        *,
        owns_run: bool = True,
        dry_run: bool = False,
    ) -> DedupResult:
        result = self._dedup_classify(session, source_id, listings, run_row, owns_run=owns_run)
        if owns_run:
            RunHistoryRepository(session).complete(
                run_row,
                listings_found=result.new_count + result.update_count + result.unchanged_count,
                new_count=result.new_count,
                update_count=result.update_count,
                unchanged_count=result.unchanged_count,
            )
        if dry_run:
            # Nothing is written: the whole classification — claims included — is discarded.
            session.rollback()
            return result
        session.commit()
        return result

    def _dedup_classify(
        self,
        session: Session,
        source_id: int,
        listings: list[TenderListing],
        run_row: RunHistory,
        *,
        owns_run: bool = True,
    ) -> DedupResult:
        seen = self._load_seen(session, source_id, listings)

        outcomes: list[DedupOutcome] = []
        new_listings: list[TenderListing] = []
        correlation_map: dict[str, str] = {}
        duplicate_count = 0
        accounted: set[str] = set()

        unique_ids: list[str] = []
        for listing in listings:
            ext = (listing.external_id or "").strip()
            if not ext:
                raise DedupError(
                    "candidate missing deduplication identity",
                    error_code=DEDUP_MISSING_IDENTITY,
                    context={"correlation_id": run_row.correlation_id},
                )
            if not (listing.title or "").strip() or not (listing.url or "").strip():
                raise DedupError(
                    "malformed candidate (missing title or url)",
                    error_code=DEDUP_MALFORMED_CANDIDATE,
                    context={"external_id": ext, "correlation_id": run_row.correlation_id},
                )
            if ext in accounted:
                duplicate_count += 1
                log.warning(
                    "duplicate candidate within one discovery run: %s",
                    ext,
                    extra={
                        "stage": self.STAGE,
                        "status": "duplicate",
                        "correlation_id": run_row.correlation_id,
                        "external_id": ext,
                    },
                )
                continue
            accounted.add(ext)
            unique_ids.append(ext)

            outcome = self._classify_one(session, source_id, listing, ext, seen)
            correlation_map[ext] = outcome.correlation_id
            if outcome.classification == NEW:
                new_listings.append(listing)
                outcomes.append(outcome)
            elif outcome.classification == UPDATE:
                outcomes.append(outcome)

        new_count = sum(1 for o in outcomes if o.classification == NEW)
        update_count = sum(1 for o in outcomes if o.classification == UPDATE)
        unchanged_count = len(unique_ids) - new_count - update_count

        return DedupResult(
            run_correlation_id=run_row.correlation_id or "",
            run_history_id=run_row.id,
            # Under orchestration the run is still going: stages 06–09 have not run yet, so
            # reporting COMPLETED here would be a lie (prompt 10 §8). Derive it honestly.
            run_status=COMPLETED if owns_run else derive_run_status(run_row),
            new_listings=tuple(new_listings),
            updates=tuple(o for o in outcomes if o.classification == UPDATE),
            unchanged_count=unchanged_count,
            new_count=new_count,
            update_count=update_count,
            duplicate_count=duplicate_count,
            correlation_map=correlation_map,
        )

    def _load_seen(
        self, session: Session, source_id: int, listings: list[TenderListing]
    ) -> dict[str, Tender]:
        ids = [cand.external_id for cand in listings if (cand.external_id or "").strip()]
        if not ids:
            return {}
        return TenderRepository(session).get_many_by_identity(source_id, ids)

    def _classify_one(
        self,
        session: Session,
        source_id: int,
        listing: TenderListing,
        ext: str,
        seen: dict[str, Tender],
    ) -> DedupOutcome:
        tender = seen.get(ext)
        if tender is None:
            return self._insert_new(session, source_id, listing)
        return self._maybe_update(session, listing, tender)

    def _insert_new(
        self, session: Session, source_id: int, listing: TenderListing
    ) -> DedupOutcome:
        """Insert a NEW seen row under a SAVEPOINT; on a unique-race, reclassify (prompt 05 §8)."""
        correlation_id = new_correlation_id()
        try:
            claim = TenderRepository(session).claim_new(
                source_id, listing, correlation_id
            )
        except PersistenceError as exc:
            raise DedupError(
                "unique-constraint race with no winner",
                error_code=DEDUP_INVALID_STATE,
                context={"external_id": listing.external_id},
            ) from exc
        if claim.created:
            return DedupOutcome(
                classification=NEW,
                listing=listing,
                tender_id=claim.tender.id,
                correlation_id=correlation_id,
                material_change=False,
            )
        # A concurrent run inserted the same (source_id, external_id) first.
        log.warning(
            "unique-constraint race resolved for %s; reclassifying",
            listing.external_id,
            extra={
                "stage": self.STAGE,
                "status": "race",
                "correlation_id": get_correlation_id(),
                "external_id": listing.external_id,
            },
        )
        return self._maybe_update(session, listing, claim.tender)

    def _maybe_update(
        self,
        session: Session,
        listing: TenderListing,
        tender: Tender,
    ) -> DedupOutcome:
        comparison: Comparison = compare(listing, tender, self._policy)
        if comparison.classification == UNCHANGED:
            return DedupOutcome(
                classification=UNCHANGED,
                listing=listing,
                tender_id=tender.id,
                correlation_id=tender.correlation_id,
                material_change=False,
            )

        # UPDATE: persist the changed listing state (status updated, is_update true).
        TenderRepository(session).apply_update(tender, listing)

        values = comparison.current
        changed_details = {
            f: {"before": comparison.seen[f], "after": values[f]}
            for f in comparison.changed_fields
        }
        return DedupOutcome(
            classification=UPDATE,
            listing=listing,
            tender_id=tender.id,
            correlation_id=tender.correlation_id,
            material_change=comparison.material_change,
            change_types=comparison.change_types,
            change_details=changed_details,
        )

"""Driving stages 04 -> 09 for one source (prompt 10 §2, §4–§9, §12–§15).

What this class owns
====================

Execution order, run lifecycle, stage coordination, failure isolation and the run's
persistence. Nothing else. Every stage's actual behaviour stays with the prompt that owns
it, reached through the service that prompt already exposes:

===================  ===========================================================
Stage                Reached via
===================  ===========================================================
04 discovery         ``SourceAdapter.list_new_tenders`` (prompt 04)
05 dedup             ``DedupService.run`` (prompt 05)
06 persistence       realised inside 05's transaction — ``claim_new``/``apply_update``
                     (prompt 06); no separate runtime exists
07 detail/document   ``SourceAdapter.get_attachments`` (prompt 07), per tender
08 acquisition       ``DocumentAcquisitionService.acquire`` (prompt 08), per tender
09 processing        ``DocumentProcessingService.process_tender`` (prompt 09), per tender
===================  ===========================================================

The adapter is built **once** per run, inside stage 04, and reused by stage 07. Building it
per stage would construct a second HTTP client for what is one logical crawl, and stage 07's
``get_attachments`` already fetches each tender's detail page itself — calling ``get_detail``
as well would double-fetch every page.

Run identity vs correlation identity (prompt 10 §4)
===================================================

* ``run_id`` — ``RunHistory.id``. Unique per attempt. Not a correlation id.
* ``run_correlation_id`` — the ambient correlation for the whole run, held in a context
  variable for the run's duration so every stage, retry and log line shares it (§19 K).
* ``Tender.correlation_id`` — per tender, persisted by dedup and **never overwritten**.
  Stages 08 and 09 are handed the tender's own correlation id so the artifacts they persist
  (``Document.correlation_id``, bundle metadata) keep the tender's identity rather than the
  run's. A run that re-processes an unchanged tender must not rewrite its identity.

Run lifecycle (prompt 10 §5, §12)
=================================

Not dry-run: the ``RunHistory`` row is created and committed **before stage 04**, so a
process that dies mid-run leaves a row that is reconstructable (``ended_at IS NULL`` ⇒
RUNNING). Nothing treats that row as a lock — the scheduler reads ``Source.last_run_at``,
which is only stamped once a run *ends*, so a crashed source is simply due again.

Dry-run (prompt 10 §14): **nothing at all is written** — no run row, no seen tenders, no
documents, no bundles. Stage 04 reads, stage 05 classifies and rolls back, and stages 07–09
are recorded PENDING with the actions a live run would have taken. Dry-run is not "a normal
run with email disabled"; there is no email in this prompt at all (§17).

Failure isolation (prompt 10 §9, §15)
=====================================

Two different kinds of failure, deliberately handled differently:

* **Stage-level** — the stage's own entry point raised (stage 04 cannot reach the source;
  stage 05's transaction failed). The run stops there, and every stage after it is recorded
  ``PENDING`` rather than silently omitted, so the record says what did *not* happen.
* **Item-level** — one tender's detail fetch, acquisition or extraction failed. The stage
  records ``failed_items`` and the run carries on. A stage is ``FAILED`` only when *every*
  item failed, ``PARTIAL`` when some did, ``COMPLETED`` otherwise.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.acquisition.service import (
    AcquisitionResult,
    DocumentAcquisitionService,
)
from tender_intelligence.core.correlation import correlation_context, new_correlation_id
from tender_intelligence.core.errors import STAGE_FAILED
from tender_intelligence.deadline.model import RESOLVED as DEADLINE_RESOLVED
from tender_intelligence.deadline.service import DeadlineResolutionService
from tender_intelligence.db.repositories import (
    RunCounts,
    RunHistoryRepository,
    SourceRepository,
    TenderRepository,
)
from tender_intelligence.dedup.service import DedupResult, DedupService
from tender_intelligence.interfaces.source import (
    SourceAdapter,
    TenderAttachment,
    TenderDetail,
    TenderListing,
)
from tender_intelligence.orchestrator.alerts import AlertHook, AlertNotice, NullAlertHook
from tender_intelligence.orchestrator.config import ConfigLoader, RuntimeConfigSnapshot
from tender_intelligence.orchestrator.errors import SourceNotRunnableError
from tender_intelligence.orchestrator.registry import AdapterRegistry
from tender_intelligence.orchestrator.retry import (
    STAGE_LOCAL_RETRYABLE_CATEGORIES,
    RetryPolicy,
    run_with_retry,
)
from tender_intelligence.orchestrator.scheduler import SourceScheduler, SourceSpec
from tender_intelligence.orchestrator.stages import StageReport, StageRunner
from tender_intelligence.orchestrator.status import (
    STAGE_ORDER,
    RunReport,
    RunStatus,
    StageNumber,
    StageOutcome,
    StageStatus,
)
from tender_intelligence.processing.service import DocumentProcessingService
from tender_intelligence.triage.service import (
    LLMClientFactory,
    TriageDecision,
    TriageError,
    TriageService,
)

log = logging.getLogger("tender_intelligence.orchestrator.coordinator")


@dataclass(frozen=True)
class TenderWorkItem:
    """One tender to carry through stages 07–09 (prompt 10 §13).

    A value snapshot, not an ORM instance: nothing downstream may lazily load or mutate a
    detached row, and the tender's own correlation id travels with it (prompt 10 §4).
    """

    tender_id: int
    external_id: str
    correlation_id: str


@dataclass(frozen=True)
class _Discovery:
    """Stage 04's product: the built adapter plus the candidates it enumerated."""

    adapter: SourceAdapter
    listings: tuple[TenderListing, ...]


@dataclass(frozen=True)
class _ProcessingTally:
    """Stage 09's product: how many documents came out, and how cleanly."""

    documents: int
    bundles_complete: int
    bundles_incomplete: int


class _RetryableAcquisitionError(Exception):
    """Internal signal: an acquisition result whose failures are all safe to re-drive.

    Exists because ``acquire`` *returns* per-attachment failures rather than raising them, so
    the retry decision is made on a value, not an exception. The last result rides along so an
    exhausted retry still reports the real failures rather than a synthetic one.
    """

    def __init__(self, result: AcquisitionResult) -> None:
        super().__init__("acquisition failed with retryable, stage-local errors")
        self.result = result


class RunCoordinator:
    """Runs stages 04–09 for one source and finalises its ``RunHistory`` (prompt 10 §2)."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        registry: AdapterRegistry,
        dedup: DedupService,
        acquisition: DocumentAcquisitionService,
        processing: DocumentProcessingService,
        scheduler: SourceScheduler,
        config_loader: ConfigLoader,
        alert_hook: AlertHook | None = None,
        retry_policy: RetryPolicy | None = None,
        stage_runner: StageRunner | None = None,
        deadline_service: DeadlineResolutionService | None = None,
        triage_client_factory: LLMClientFactory | None = None,
        triage_pass_handoff: Callable[[TriageDecision], None] | None = None,
        verdict_handoff: Callable[[TriageDecision], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._maker = session_factory
        self._registry = registry
        self._dedup = dedup
        self._acquisition = acquisition
        self._processing = processing
        self._scheduler = scheduler
        self._config_loader = config_loader
        self._alerts: AlertHook = alert_hook or NullAlertHook()
        self._retry = retry_policy or RetryPolicy()
        self._runner = stage_runner or StageRunner()
        self._deadline = deadline_service or DeadlineResolutionService()
        self._triage_client_factory = triage_client_factory
        self._triage_pass_handoff = triage_pass_handoff
        self._verdict_handoff = verdict_handoff
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper

    # -- public entry point -------------------------------------------------

    def run_source(
        self,
        source_id: int,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> RunReport:
        """Run the full pipeline for *source_id* (prompt 10 §2).

        The worker-independent callable boundary prompt 10 §21 asks for: a source id in, a
        report out, with no dependency on a request context, an admin API or a process-wide
        singleton.

        *force* permits an explicitly-named inactive source; without it an inactive source is
        refused, because "inactive" means "do not crawl".
        """
        decision = self._scheduler.get(source_id)
        if decision is None:
            raise SourceNotRunnableError(
                f"unknown source_id {source_id}", context={"source_id": source_id}
            )
        if not decision.spec.active and not force:
            raise SourceNotRunnableError(
                "source is inactive",
                context={"source_id": source_id, "reason": decision.reason},
            )

        # Prompt 10 §3: configuration is re-read here, per run, and never cached.
        config = self._config_loader.load()
        run_cid = new_correlation_id()
        with correlation_context(run_cid):
            return self._execute(decision.spec, config, run_cid, dry_run=dry_run)

    # -- the run ------------------------------------------------------------

    def _execute(
        self,
        spec: SourceSpec,
        config: RuntimeConfigSnapshot,
        run_cid: str,
        *,
        dry_run: bool,
    ) -> RunReport:
        started_at = self._clock()
        log.info(
            "run starting for source %s",
            spec.name,
            extra={
                "stage": "run",
                "status": "start",
                "correlation_id": run_cid,
                "source_id": spec.id,
                "config_version": config.version,
                "dry_run": dry_run,
            },
        )

        run_history_id = None if dry_run else self._open_run(spec, run_cid, config)
        state = _RunState(run_cid=run_cid, dry_run=dry_run)

        try:
            self._drive(spec, state, run_history_id)
        except Exception:  # noqa: BLE001 - the run boundary must never leak an exception
            # Only an orchestrator bug reaches here: every stage already contains its own
            # failures. Attribute it to the run and keep the row rather than losing the
            # record (prompt 10 §9, §12).
            log.exception(
                "unexpected orchestrator failure",
                extra={"stage": "run", "status": "error", "correlation_id": run_cid},
            )
            if state.error_code is None:
                state.error_code = STAGE_FAILED
            state.mark_ended(self._clock())

        self._pending_remaining(state, "run stopped before this stage")
        status = RunStatus.DRY_RUN if dry_run else _run_status(state.outcomes)
        started_at, ended_at = state.bounds(started_at)

        if run_history_id is not None:
            self._finalize_run(
                run_history_id=run_history_id,
                run_cid=run_cid,
                state=state,
                status=status,
                config=config,
            )
            self._stamp_source(spec.id, state)

        report = RunReport(
            source_id=spec.id,
            source_name=spec.name,
            correlation_id=run_cid,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            stages=tuple(state.outcomes),
            run_history_id=run_history_id,
            config_version=config.version,
            failed_stage=state.failed_stage,
            error_code=state.error_code,
            tenders_considered=len(state.work),
            documents_acquired=state.documents_acquired,
            documents_processed=state.documents_processed,
            dry_run=dry_run,
            planned_actions=tuple(state.planned_actions),
        )
        log.info(
            "run finished: %s",
            status,
            extra={
                "stage": "run",
                "status": str(status),
                "correlation_id": run_cid,
                "source_id": spec.id,
                "error_code": state.error_code,
                "failed_stage": state.failed_stage.value if state.failed_stage else None,
                "tenders_considered": len(state.work),
            },
        )
        if status is RunStatus.FAILED:
            self._alert(report)
        return report

    # -- stages -------------------------------------------------------------

    def _drive(
        self,
        spec: SourceSpec,
        state: _RunState,
        run_history_id: int | None,
    ) -> None:
        """Stages 04 → 09, in the order prompt 10 §6 fixes."""
        discovery = self._runner.run(StageNumber.DISCOVERY, lambda: self._stage_discovery(spec))
        state.record(discovery.outcome)
        if discovery.outcome.status is StageStatus.FAILED or discovery.payload is None:
            return
        found = discovery.payload

        dedup = self._runner.run(
            StageNumber.DEDUP,
            lambda: self._stage_dedup(
                spec, found.listings, state.run_cid, run_history_id, state.dry_run
            ),
        )
        state.record(dedup.outcome)
        if dedup.outcome.status is StageStatus.FAILED:
            return
        result = dedup.payload
        if result is not None:
            state.record_dedup(result)

        persistence = self._runner.run(
            StageNumber.PERSISTENCE, lambda: self._stage_persistence(result)
        )
        state.record(persistence.outcome)

        if state.dry_run:
            self._plan_remaining(state, result)
            return

        state.work = self._resolve_work(spec, _work_external_ids(result), state.run_cid)
        if not state.work:
            return

        detail = self._runner.run(
            StageNumber.DETAIL, lambda: self._stage_detail(found.adapter, state.work)
        )
        state.record(detail.outcome)
        if detail.outcome.status is StageStatus.FAILED:
            return

        acquisition = self._runner.run(
            StageNumber.ACQUISITION,
            lambda: self._stage_acquisition(state.work, detail.payload or {}),
        )
        state.record(acquisition.outcome)
        if acquisition.payload is not None:
            state.documents_acquired = acquisition.payload

        processing = self._runner.run(
            StageNumber.PROCESSING, lambda: self._stage_processing(state.work)
        )
        state.record(processing.outcome)
        if processing.payload is not None:
            state.documents_processed = processing.payload.documents

        triage = self._runner.run(
            StageNumber.TRIAGE,
            lambda: self._stage_triage(state.work, run_history_id=run_history_id),
        )
        state.record(triage.outcome)
        passed_decisions = triage.payload or []
        if self._verdict_handoff is None:
            state.record_skipped(StageNumber.VERDICT, "no Stage B engine configured")
        else:
            verdict = self._runner.run(
                StageNumber.VERDICT,
                lambda: self._stage_verdict(passed_decisions),
            )
            state.record(verdict.outcome)

    # -- 04 discovery -------------------------------------------------------

    def _stage_discovery(self, spec: SourceSpec) -> tuple[StageReport, _Discovery]:
        """Build the adapter for *spec* and enumerate listing rows (prompt 04, 10 §7).

        Adapter construction happens *inside* the stage, so an unbuildable source fails stage
        04 with ``source_not_runnable`` instead of escaping the run (prompt 10 §7).
        """
        adapter = self._registry.build(spec)
        listings = tuple(adapter.list_new_tenders())
        return (
            StageReport(
                detail=f"adapter {type(adapter).__name__} returned {len(listings)} listing(s)",
                item_count=len(listings),
            ),
            _Discovery(adapter=adapter, listings=listings),
        )

    # -- 05 dedup -----------------------------------------------------------

    def _stage_dedup(
        self,
        spec: SourceSpec,
        listings: Sequence[TenderListing],
        run_cid: str,
        run_history_id: int | None,
        dry_run: bool,
    ) -> tuple[StageReport, DedupResult]:
        """Classify the discovered candidates (prompt 05).

        The run row is *handed in*, never created here, so the row spans the whole run. In
        dry-run mode dedup classifies for real and rolls the whole transaction back.
        """
        result = self._dedup.run(
            spec.id,
            list(listings),
            run_correlation_id=run_cid,
            run_history_id=run_history_id,
            dry_run=dry_run,
        )
        suffix = " (dry run: nothing persisted)" if dry_run else ""
        return (
            StageReport(
                detail=(
                    f"{result.new_count} new, {result.update_count} update, "
                    f"{result.unchanged_count} unchanged, "
                    f"{result.duplicate_count} duplicate{suffix}"
                ),
                item_count=result.new_count + result.update_count,
                status=StageStatus.COMPLETED,
            ),
            result,
        )

    # -- 06 persistence -----------------------------------------------------

    def _stage_persistence(self, result: DedupResult | None) -> tuple[StageReport, None]:
        """Record the persistence stage (prompt 06).

        Prompt 06 is the models-and-repositories layer; it has no runtime of its own. Its work
        is performed inside stage 05's transaction, where ``claim_new`` and ``apply_update``
        write the ``Tender`` rows in the same commit that releases downstream work (docs/04
        §4.7). Recording it COMPLETED with the row count says exactly that, rather than
        inventing a stage that does not exist.
        """
        written = 0 if result is None else result.new_count + result.update_count
        return (
            StageReport(
                detail=(
                    "realised inside stage 05's transaction (claim_new/apply_update); "
                    f"{written} tender row(s) written"
                ),
                item_count=written,
                status=StageStatus.COMPLETED,
            ),
            None,
        )

    # -- 07 detail / document discovery / deadline resolution ---------------

    def _stage_detail(
        self, adapter: SourceAdapter, work: Sequence[TenderWorkItem]
    ) -> tuple[StageReport, dict[int, list[TenderAttachment]]]:
        """Fetch each tender's detail page, resolve its deadline, and collect attachments.

        Prompt 12.1: calls ``get_detail()`` (not just ``get_attachments()``) so the
        detail-page deadline can be extracted and compared against the listing-page
        deadline already persisted.  Per-tender failures are isolated (prompt 10 §15).
        """
        attachments: dict[int, list[TenderAttachment]] = {}
        failures = 0
        deadlines_resolved = 0
        deadlines_unresolved = 0

        for item in work:
            try:
                detail = adapter.get_detail(item.external_id)
                attachments[item.tender_id] = list(detail.attachments)
                # Resolve and persist deadline (Prompt 12.1)
                resolved = self._resolve_deadline(item, detail)
                if resolved:
                    deadlines_resolved += 1
                else:
                    deadlines_unresolved += 1
            except Exception as exc:  # noqa: BLE001 - per-tender isolation (prompt 10 §15)
                failures += 1
                attachments[item.tender_id] = []
                self._item_failure(StageNumber.DETAIL, item, exc)

        found = sum(len(files) for files in attachments.values())
        return (
            StageReport(
                detail=(
                    f"{found} attachment(s) across {len(work)} tender(s); "
                    f"{deadlines_resolved} deadline(s) resolved, "
                    f"{deadlines_unresolved} unresolved"
                ),
                item_count=len(work),
                failed_items=failures,
            ),
            attachments,
        )

    def _resolve_deadline(
        self,
        item: TenderWorkItem,
        detail: "TenderDetail",  # type: ignore[name-defined]
    ) -> bool:
        """Run deadline resolution for *item* using *detail* and persist the result.

        Returns True when the deadline was RESOLVED, False otherwise (UNRESOLVED or
        CONFLICTING).  Per-tender: a failure here is logged but never propagates to fail
        the whole stage — attachment discovery still succeeded.
        """
        from tender_intelligence.interfaces.source import TenderDetail as _TDType

        try:
            with self._maker() as session:
                repo = TenderRepository(session)
                tender = repo.get(item.tender_id)
                if tender is None:
                    return False

                # Build the listing DTO from persisted tender row (listing-page values)
                from tender_intelligence.interfaces.source import TenderListing
                listing = TenderListing(
                    external_id=str(tender.external_id),
                    title=str(tender.title),
                    url=str(tender.url),
                    deadline_at=tender.deadline,
                    deadline_timezone=tender.deadline_timezone,
                    raw_metadata=dict(tender.raw_metadata or {}),
                )

                # Attempt document-bundle lookup for text-based fallback
                bundle_docs: list[tuple[int, str, str]] = []
                try:
                    from tender_intelligence.processing.store import ExtractionStore
                    from tender_intelligence.processing.representation import TenderDocumentBundle
                    # ExtractionStore requires ObjectStorage — only attempt if processing ran
                    # The coordinator does not own the store directly; check via document rows
                    from sqlalchemy import select as sa_select
                    from tender_intelligence.db.models.documents import Document
                    doc_rows = session.scalars(
                        sa_select(Document)
                        .where(
                            Document.tender_id == item.tender_id,
                            Document.extraction_status == "extracted",
                            Document.extracted_text_ref.is_not(None),
                        )
                    ).all()
                    # Only attempt text read if processing service is available via storage
                except Exception:  # noqa: BLE001
                    doc_rows = []

                result = self._deadline.resolve(
                    listing,
                    detail=detail,
                    bundle_docs=bundle_docs,
                    correlation_id=item.correlation_id,
                )

                resolved_at = self._clock()
                repo.apply_deadline_resolution(tender, result, resolved_at)
                session.commit()

                log.info(
                    "deadline resolution complete: %s (%s)",
                    result.status,
                    result.source,
                    extra={
                        "stage": "deadline_resolution",
                        "status": result.status,
                        "source": result.source,
                        "correlation_id": item.correlation_id,
                        "tender_id": item.tender_id,
                        "deadline_utc": (
                            result.deadline_utc.isoformat() if result.deadline_utc else None
                        ),
                    },
                )
                return result.status == DEADLINE_RESOLVED

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "deadline resolution failed for tender %s: %s",
                item.external_id,
                type(exc).__name__,
                extra={
                    "stage": "deadline_resolution",
                    "status": "error",
                    "correlation_id": item.correlation_id,
                    "tender_id": item.tender_id,
                    "error_code": getattr(exc, "error_code", None),
                },
            )
            return False

    # -- 08 acquisition -----------------------------------------------------

    def _stage_acquisition(
        self,
        work: Sequence[TenderWorkItem],
        attachments: dict[int, list[TenderAttachment]],
    ) -> tuple[StageReport, list[TriageDecision]]:
        """Acquire each tender's attachments, with bounded stage-local retry (§11, §15)."""
        acquired = 0
        failed_items = 0
        failures = 0
        attempts = 0
        for item in work:
            try:
                result, item_attempts = self._acquire_one(
                    item, attachments.get(item.tender_id, [])
                )
            except Exception as exc:  # noqa: BLE001 - per-tender isolation (prompt 10 §15)
                failed_items += 1
                self._item_failure(StageNumber.ACQUISITION, item, exc)
                continue
            attempts += item_attempts
            acquired += len(result.documents)
            failures += len(result.failures)
            if result.failures:
                failed_items += 1

        return (
            StageReport(
                detail=(
                    f"{acquired} document(s) acquired for {len(work)} tender(s); "
                    f"{failures} attachment failure(s), {attempts} acquisition attempt(s)"
                ),
                item_count=len(work),
                failed_items=failed_items,
                attempts=attempts,
            ),
            acquired,
        )

    def _acquire_one(
        self, item: TenderWorkItem, attachments: Sequence[TenderAttachment]
    ) -> tuple[AcquisitionResult, int]:
        """Acquire one tender, retrying only failures the fetcher never owned (prompt 10 §11).

        A retryable failure is surfaced as :class:`_RetryableAcquisitionError` so the generic
        retry loop can make its decision on a *value* — ``acquire`` reports per-attachment
        failures by returning them, not by raising. Anything else propagates and is counted as
        an item-level failure by the caller.
        """
        if not attachments:
            return AcquisitionResult(), 0

        def attempt() -> AcquisitionResult:
            result = self._acquisition.acquire(
                item.tender_id, list(attachments), correlation_id=item.correlation_id
            )
            if _is_retryable_acquisition(result):
                raise _RetryableAcquisitionError(result)
            return result

        outcome = run_with_retry(
            attempt,
            policy=self._retry,
            should_retry=lambda exc: isinstance(exc, _RetryableAcquisitionError),
            sleep=self._sleeper,
        )
        if outcome.succeeded and outcome.value is not None:
            return outcome.value, outcome.attempts
        error = outcome.error
        if isinstance(error, _RetryableAcquisitionError):
            return error.result, outcome.attempts
        # Not retryable, and not a value: a genuine fault. Let the caller isolate it.
        if error is None:  # pragma: no cover - run_with_retry always sets one on failure
            raise RuntimeError("acquisition produced neither a result nor an error")
        raise error

    # -- 09 processing ------------------------------------------------------

    def _stage_processing(
        self, work: Sequence[TenderWorkItem]
    ) -> tuple[StageReport, _ProcessingTally]:
        """Extract text/tables for each tender's documents, isolating failures (§15).

        Two kinds of degraded tender count towards ``failed_items``: one whose extraction
        *raised*, and one whose bundle came back with ``incomplete_inputs`` — prompt 09 §16
        makes a failed document a result, not an exception, so counting only exceptions would
        report a stage COMPLETED while a document in it produced nothing. Either way the run
        becomes PARTIAL, never FAILED: §19 G requires a document-level failure not to become a
        whole-run failure.
        """
        documents = 0
        complete = 0
        incomplete = 0
        failures = 0
        for item in work:
            try:
                bundle = self._processing.process_tender(
                    item.tender_id, correlation_id=item.correlation_id
                )
            except Exception as exc:  # noqa: BLE001 - per-tender isolation (prompt 10 §15)
                failures += 1
                self._item_failure(StageNumber.PROCESSING, item, exc)
                continue
            documents += len(bundle.documents)
            if bundle.incomplete_inputs:
                incomplete += 1
            else:
                complete += 1
        return (
            StageReport(
                detail=(
                    f"{documents} document(s) across {len(work)} tender(s); {complete} complete "
                    f"bundle(s), {incomplete} incomplete, {failures} tender-level failure(s)"
                ),
                item_count=len(work),
                failed_items=failures + incomplete,
            ),
            _ProcessingTally(
                documents=documents, bundles_complete=complete, bundles_incomplete=incomplete
            ),
        )

    # -- 13 triage ----------------------------------------------------------

    def _stage_triage(
        self, work: Sequence[TenderWorkItem], *, run_history_id: int | None = None
    ) -> tuple[StageReport, int]:
        """Persist a relevance decision for every new or updated tender.

        Only durable PASS decisions are passed to Stage B.
        """
        passed: list[TriageDecision] = []
        discarded = 0
        failed = 0
        for item in work:
            try:
                with self._maker() as session:
                    decision = TriageService(
                        session,
                        client_factory=self._triage_client_factory,
                        run_id=run_history_id,
                    ).evaluate(item.tender_id)
                    session.commit()
                if decision.status == "passed":
                    passed.append(decision)
                    if self._triage_pass_handoff is not None:
                        self._triage_pass_handoff(decision)
                elif decision.status == "triage_discarded":
                    discarded += 1
                else:
                    failed += 1
                    self._item_failure(
                        StageNumber.TRIAGE,
                        item,
                        TriageError(
                            "triage evaluation failed",
                            decision.error_code or "triage_failed",
                        ),
                    )
            except Exception as exc:  # noqa: BLE001 - each tender must remain isolated
                failed += 1
                self._item_failure(StageNumber.TRIAGE, item, exc)
        return (
            StageReport(
                detail=(
                    f"{len(passed)} passed, {discarded} discarded, {failed} failed across "
                    f"{len(work)} tender(s)"
                ),
                item_count=len(work),
                failed_items=failed,
            ),
            passed,
        )

    def _stage_verdict(self, decisions: Sequence[TriageDecision]) -> tuple[StageReport, int]:
        """Run Stage B only for persisted Stage A PASS decisions."""
        succeeded = failed = 0
        for decision in decisions:
            try:
                self._verdict_handoff(decision)  # type: ignore[misc]
                succeeded += 1
            except Exception as exc:  # per-tender isolation mirrors Stage A
                failed += 1
                log.warning("Stage B failed", extra={"stage": StageNumber.VERDICT.value,
                    "tender_id": decision.tender_id, "correlation_id": decision.correlation_id,
                    "error_code": getattr(exc, "error_code", "verdict_failed")})
        return StageReport(detail=f"{succeeded} completed, {failed} failed", item_count=len(decisions),
                           failed_items=failed), succeeded

    # -- dry run planning ---------------------------------------------------

    def _plan_remaining(self, state: _RunState, result: DedupResult | None) -> None:
        """Record what a live run would have done, without doing any of it (prompt 10 §14)."""
        planned = _work_external_ids(result)
        for stage in (
            StageNumber.DETAIL,
            StageNumber.ACQUISITION,
            StageNumber.PROCESSING,
            StageNumber.TRIAGE,
            StageNumber.VERDICT,
        ):
            state.record_pending(stage, "dry run: no writes and no fetches beyond discovery")
        if planned:
            state.planned_actions = (
                f"would fetch attachment metadata for {len(planned)} tender(s)",
                "would acquire those attachments into object storage",
                "would extract text and tables into per-tender bundles",
                "would evaluate configurable Stage A triage rules",
            )
        else:
            state.planned_actions = ("no new or changed tenders; nothing further to do",)

    # -- work set -----------------------------------------------------------

    def _resolve_work(
        self, spec: SourceSpec, external_ids: Sequence[str], run_cid: str
    ) -> list[TenderWorkItem]:
        """The tenders stages 07–09 operate on (prompt 10 §13).

        Exactly the tenders dedup classified NEW or UPDATE — no second notion of identity is
        invented here; the rows are looked up by ``(source_id, external_id)``, the same key
        dedup used. ``unchanged`` tenders are deliberately excluded: re-processing them every
        run is the waste §13 exists to avoid.

        Rows are projected to value snapshots *inside* the session, so nothing downstream can
        touch a detached ORM instance.
        """
        if not external_ids:
            return []
        with self._maker() as session:
            rows = TenderRepository(session).get_many_by_identity(spec.id, list(external_ids))
            items = [
                TenderWorkItem(
                    tender_id=int(row.id),
                    external_id=str(row.external_id),
                    # The tender's own identity, never the run's (prompt 10 §4). A tender
                    # without one can only be one dedup just created, so fall back rather than
                    # propagate an empty correlation id downstream.
                    correlation_id=str(row.correlation_id or run_cid),
                )
                for row in rows.values()
            ]
        items.sort(key=lambda item: item.external_id)  # deterministic order across runs
        return items

    # -- run row ------------------------------------------------------------

    def _open_run(self, spec: SourceSpec, run_cid: str, config: RuntimeConfigSnapshot) -> int:
        """Commit a RUNNING row before stage 04 (prompt 10 §5, §12).

        Committed immediately and on its own: a crash anywhere after this point leaves a row
        that can be reconstructed, and — because it is committed rather than held open — it
        holds no lock for the rest of the run. The config version is stamped now, so the row
        records the settings the run started under even if they change while it runs (§3).
        """
        with self._maker() as session:
            row = RunHistoryRepository(session).create(spec.id, correlation_id=run_cid)
            row.config_version = config.version
            session.commit()
            return int(row.id)

    def _finalize_run(
        self,
        *,
        run_history_id: int,
        run_cid: str,
        state: _RunState,
        status: RunStatus,
        config: RuntimeConfigSnapshot,
    ) -> None:
        """Complete or error the run, then write the stage map (prompt 10 §5, §8).

        Only a **FAILED** run is error-marked. A PARTIAL run is completed with its tallies:
        §9's failure isolation means a partial result is a finished run, and marking it errored
        would make the derived ``ERRORED`` state unable to distinguish "half worked" from "did
        not work" — the distinction §8 exists to preserve. Partial-ness is recorded in the
        stage map instead. Error-marking still keeps the counts the run established.
        """
        try:
            with self._maker() as session:
                repo = RunHistoryRepository(session)
                row = repo.get(run_history_id)
                if row is None:  # pragma: no cover - the row was committed moments ago
                    return
                counts = state.counts()
                if status is RunStatus.FAILED:
                    repo.mark_errored(row, run_cid, counts=counts)
                else:
                    repo.complete(
                        row,
                        listings_found=counts.listings_found,
                        new_count=counts.new_count,
                        update_count=counts.update_count,
                        unchanged_count=counts.unchanged_count,
                    )
                repo.record_stages(
                    row,
                    stages={o.stage.value: str(o.status) for o in state.outcomes},
                    config_version=config.version,
                    failed_stage=state.failed_stage.value if state.failed_stage else None,
                    error_code=state.error_code,
                )
                session.commit()
        except Exception:  # noqa: BLE001 - finalisation must never lose the report
            log.exception(
                "could not finalise RunHistory",
                extra={"stage": "run", "status": "error", "correlation_id": run_cid},
            )

    def _stamp_source(self, source_id: int, state: _RunState) -> None:
        """Stamp ``last_run_at`` and the last error (prompt 10 §2, §9).

        Stamped at the **end** of a run, never the start: that is what keeps a crashed run
        from leaving a permanent lock — no stamp means the source is simply due again on the
        next pass (prompt 10 §5, §12).
        """
        try:
            with self._maker() as session:
                SourceRepository(session).mark_run(
                    source_id,
                    at=state.ended_at or self._clock(),
                    error=state.source_error(),
                )
                session.commit()
        except Exception:  # noqa: BLE001
            log.exception(
                "could not stamp source last_run_at",
                extra={"stage": "run", "status": "error", "source_id": source_id},
            )

    # -- small helpers ------------------------------------------------------

    def _pending_remaining(self, state: _RunState, reason: str) -> None:
        """Record every stage the run never reached as PENDING (prompt 10 §8)."""
        recorded = {outcome.stage for outcome in state.outcomes}
        for stage in STAGE_ORDER:
            if stage not in recorded:
                state.record_pending(stage, reason)

    def _item_failure(
        self, stage: StageNumber, item: TenderWorkItem, exc: BaseException
    ) -> None:
        """Log one tender's failure without leaking content and without ending the run.

        Only the exception *type* and its machine-readable code are recorded — never its
        message, which for acquisition can carry a URL (prompt 10 §10).
        """
        log.warning(
            "%s failed for tender %s: %s",
            stage.value,
            item.external_id,
            type(exc).__name__,
            extra={
                "stage": stage.value,
                "status": "error",
                "correlation_id": item.correlation_id,
                "tender_id": item.tender_id,
                "error_code": getattr(exc, "error_code", None),
            },
        )

    def _alert(self, report: RunReport) -> None:
        """Report a failed run through the injected hook (prompt 10 §16).

        Run-level FAILED only: a PARTIAL run is a completed run with degraded results, and
        alerting on it would page an operator for every flaky attachment.
        """
        try:
            self._alerts.notify(
                AlertNotice(
                    source_id=report.source_id,
                    source_name=report.source_name,
                    correlation_id=report.correlation_id,
                    error_code=report.error_code or STAGE_FAILED,
                    stage=report.failed_stage.value if report.failed_stage else None,
                    run_history_id=report.run_history_id,
                    message=f"pipeline run failed with status {report.status}",
                )
            )
        except Exception:  # noqa: BLE001 - an alert must never fail a run
            log.exception(
                "alert hook raised",
                extra={
                    "stage": "run",
                    "status": "error",
                    "correlation_id": report.correlation_id,
                },
            )


# -- module helpers --------------------------------------------------------


def _work_external_ids(result: DedupResult | None) -> list[str]:
    """New candidates plus materially-changed ones, de-duplicated, order preserved (§13)."""
    if result is None:
        return []
    ids = [listing.external_id for listing in result.new_listings]
    ids.extend(outcome.listing.external_id for outcome in result.updates)
    seen: set[str] = set()
    ordered: list[str] = []
    for external_id in ids:
        if external_id and external_id not in seen:
            seen.add(external_id)
            ordered.append(external_id)
    return ordered


def _is_retryable_acquisition(result: AcquisitionResult) -> bool:
    """True only when every failure is retryable *and* stage-local (prompt 10 §11).

    Deliberately strict. A batch containing even one fetcher-owned failure (``transport``,
    ``http_error``, ``response_too_large``) is not re-driven, because re-running it would
    re-issue HTTP the fetcher has already retried to exhaustion — the multiplication §11 names.
    """
    if not result.failures:
        return False
    return all(
        failure.retryable and failure.category in STAGE_LOCAL_RETRYABLE_CATEGORIES
        for failure in result.failures
    )


def _run_status(outcomes: Sequence[StageOutcome]) -> RunStatus:
    """Derive the run status from its stages (prompt 10 §8, §9).

    FAILED if any stage failed; PARTIAL if any stage half-succeeded; COMPLETED otherwise. A
    run is never reported COMPLETED merely because it finished.
    """
    if any(outcome.status is StageStatus.FAILED for outcome in outcomes):
        return RunStatus.FAILED
    if any(outcome.status is StageStatus.PARTIAL for outcome in outcomes):
        return RunStatus.PARTIAL
    return RunStatus.COMPLETED


class _RunState:
    """Mutable accumulator for one run; keeps ``_execute`` linear and readable."""

    def __init__(self, *, run_cid: str, dry_run: bool) -> None:
        self.run_cid = run_cid
        self.dry_run = dry_run
        self.outcomes: list[StageOutcome] = []
        self.listings_found = 0
        self.new_count = 0
        self.update_count = 0
        self.unchanged_count = 0
        self.work: list[TenderWorkItem] = []
        self.planned_actions: tuple[str, ...] = ()
        self.documents_acquired = 0
        self.documents_processed = 0
        self.failed_stage: StageNumber | None = None
        self.error_code: str | None = None
        self.ended_at: datetime | None = None

    def record(self, outcome: StageOutcome) -> None:
        self.outcomes.append(outcome)
        if outcome.status is StageStatus.FAILED and self.failed_stage is None:
            self.failed_stage = outcome.stage
            self.error_code = outcome.error_code
        self.mark_ended(outcome.ended_at)

    def record_dedup(self, result: DedupResult) -> None:
        """Keep the tallies dedup established, even if a later stage fails (prompt 10 §5).

        ``listings_found`` is computed exactly as ``DedupService`` computes it standalone
        (new + update + unchanged, excluding within-run duplicates), so one run recorded with
        and without the orchestrator produces the same row.
        """
        self.new_count = result.new_count
        self.update_count = result.update_count
        self.unchanged_count = result.unchanged_count
        self.listings_found = result.new_count + result.update_count + result.unchanged_count

    def record_pending(self, stage: StageNumber, detail: str) -> None:
        """Record a stage this run never reached (prompt 10 §8)."""
        moment = self.ended_at or _now()
        self.outcomes.append(
            StageOutcome(
                stage=stage,
                status=StageStatus.PENDING,
                correlation_id=self.run_cid,
                started_at=moment,
                ended_at=moment,
                detail=detail,
            )
        )

    def record_skipped(self, stage: StageNumber, detail: str) -> None:
        """Record a configured optional stage that has no runtime adapter."""
        moment = self.ended_at or _now()
        self.outcomes.append(StageOutcome(stage=stage, status=StageStatus.SKIPPED_NOT_IMPLEMENTED,
            correlation_id=self.run_cid, started_at=moment, ended_at=moment, detail=detail))

    def mark_ended(self, moment: datetime) -> None:
        if self.ended_at is None or moment > self.ended_at:
            self.ended_at = moment

    def bounds(self, started_at: datetime) -> tuple[datetime, datetime]:
        """A start/end pair that always brackets the stages actually recorded."""
        ended_at = self.ended_at or started_at
        return (min(started_at, ended_at), ended_at)

    def counts(self) -> RunCounts:
        """The listing tallies to persist, whatever the run's outcome (prompt 10 §5)."""
        return RunCounts(
            listings_found=self.listings_found,
            new_count=self.new_count,
            update_count=self.update_count,
            unchanged_count=self.unchanged_count,
        )

    def source_error(self) -> str | None:
        """A short operator-facing failure note for ``Source.last_error`` (never a secret)."""
        if self.failed_stage is None:
            return None
        return f"{self.failed_stage.value} failed ({self.error_code})"


def _now() -> datetime:
    return datetime.now(UTC)

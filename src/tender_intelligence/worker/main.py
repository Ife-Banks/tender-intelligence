"""Headless worker entrypoint (Phase 0 lifecycle + pipeline run loop).

Phase 0 builds the full startup spine the later pipeline slots into:
start → load env config → open DB → seed settings/dev-alert recipient (optional YAML init)
→ create correlation ID → log startup → run a placeholder pipeline stage → log completion
or failure → clean shutdown. The scheduler/crawler/AI stages are later slices (docs/12).

Prompt 14 (Stage B integration): ``build_pipeline`` composes the full
04 → 05 → 06 → 07 → 08 → 09 → 13 → 14 ``RunCoordinator`` with
``verdict_handoff=build_verdict_handoff(...)`` wired by default, matching the production
construction in ``src/tender_intelligence/admin/main.py:_compose_run_coordinator``.
This is the canonical production worker construction path for the normal pipeline.
"""

from __future__ import annotations

import sys
import argparse
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tender_intelligence.config.seed import ensure_settings_row, seed_dev_alert_recipient
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.core.correlation import new_correlation_id, set_correlation_id
from tender_intelligence.core.result import StageResult
from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.logging.structured import get_logger, setup_logging

if TYPE_CHECKING:
    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.db.session import SessionManager
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.worker import Worker
    from tender_intelligence.sources.polite import PoliteHttpClient


def build_pipeline(
    sessions: SessionManager,
    storage: Any,
    *,
    llm_client_factory: Any | None = None,
    triage_client_factory: Any | None = None,
    discovery_transport: Any | None = None,
    fetcher_transport: Any | None = None,
    crawl_policy: Any | None = None,
    ocr: Any | None = None,
) -> tuple[RunCoordinator, Worker, PoliteHttpClient, DocumentFetcher, AdapterRegistry]:
    """Compose the full 04→14 ``RunCoordinator`` with Stage B wired.

    This is the **canonical production construction path** for the normal pipeline.
    It mirrors ``admin/main.py:_compose_run_coordinator`` and is the function the
    production worker uses to build its coordinator.

    Stage B (``verdict_handoff``) is always wired via ``build_verdict_handoff``.
    When no LLM provider is configured (``llm_client_factory=None``), the verdict
    engine will return ``verdict_client_unavailable`` for each tender — Stage B
    executes but records the unavailability cleanly rather than skipping silently.

    Admin dry-run safety: this function is not called by Admin dry-run paths.  The
    Admin ``_compose_run_coordinator`` wires the same ``build_verdict_handoff`` via
    its own factory chain.  Neither path sends production email or bypasses Test Mode.
    """
    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.acquisition.service import DocumentAcquisitionService
    from tender_intelligence.dedup.service import DedupService
    from tender_intelligence.interfaces.openai_compatible import build_openai_compatible_client
    from tender_intelligence.notifications.channels import EmailNotificationChannel, NotificationDispatcher
    from tender_intelligence.notifications.service import NotificationService
    from tender_intelligence.orchestrator.config import ConfigLoader
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.scheduler import SourceScheduler
    from tender_intelligence.orchestrator.worker import Worker
    from tender_intelligence.processing.ocr import default_ocr_engine
    from tender_intelligence.processing.service import DocumentProcessingService
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.sources.polite import PoliteHttpClient
    from tender_intelligence.verdict.runtime import build_verdict_handoff

    policy = crawl_policy or CrawlPolicy()
    # Resolved once here so every extraction in the run shares one probed engine. An explicit
    # engine (including an injected test stub) wins; otherwise probe Tesseract.
    resolved_ocr = ocr if ocr is not None else default_ocr_engine()
    discovery_client = PoliteHttpClient.build(policy, transport=discovery_transport)
    fetcher = DocumentFetcher(policy, transport=fetcher_transport)
    adapter_registry = AdapterRegistry.default(policy=policy, fetcher=discovery_client.get)

    # Decrypt the API key from the profile before handing it to the LLM client factory.
    # Mirrors admin/main.py:_profile_client_factory so both runtime paths use the same
    # key-handling convention.
    def _profile_client_factory(factory):
        if factory is None:
            return None

        def create(profile):
            from tender_intelligence.crypto.secrets import decrypt_secret, get_master_key

            api_key = (
                decrypt_secret(profile.api_key_encrypted, get_master_key())
                if profile.api_key_encrypted
                else ""
            )
            return factory(profile=profile, api_key=api_key)

        return create

    effective_llm_factory = llm_client_factory or build_openai_compatible_client
    runtime_settings = get_env_settings()

    def _triage_profile_client(profile):
        from tender_intelligence.crypto.secrets import decrypt_secret, get_master_key

        api_key = (
            decrypt_secret(profile.api_key_encrypted, get_master_key())
            if profile.api_key_encrypted
            else ""
        )
        return effective_llm_factory(profile=profile, api_key=api_key)

    coordinator = RunCoordinator(
        session_factory=sessions,
        registry=adapter_registry,
        dedup=DedupService(sessions),
        acquisition=DocumentAcquisitionService(sessions, storage=storage, fetcher=fetcher),
        # Scanned TORs are the normal case in this domain, so the real engine is wired here rather
        # than left as a test-time stub. default_ocr_engine probes once and yields None when the
        # binary is absent, which the processor records per document as ocr_failed.
        processing=DocumentProcessingService(sessions, storage=storage, ocr=resolved_ocr),
        scheduler=SourceScheduler(sessions),
        config_loader=ConfigLoader(sessions),
        triage_client_factory=triage_client_factory or _triage_profile_client,
        # Stage B is always wired — when no LLM is configured the engine records
        # verdict_client_unavailable and moves on; it never silently skips.
        verdict_handoff=build_verdict_handoff(  # type: ignore[arg-type]
            sessions, storage, _profile_client_factory(effective_llm_factory)
        ),
        notification_dispatcher=NotificationDispatcher(
            EmailNotificationChannel(
                NotificationService(
                    sessions,
                    storage=storage,
                    link_base_url=runtime_settings.link_base_url,
                )
            )
        ),
    )
    worker = Worker(coordinator=coordinator, scheduler=SourceScheduler(sessions))
    return coordinator, worker, discovery_client, fetcher, adapter_registry


def main(
    argv: list[str] | None = None,
    *,
    llm_client_factory: Any | None = None,
    triage_client_factory: Any | None = None,
    discovery_transport: Any | None = None,
    fetcher_transport: Any | None = None,
    crawl_policy: Any | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tender_intelligence.worker",
        description="Run one scheduled worker pass; optionally target a source id.",
    )
    parser.add_argument("seed_yaml", nargs="?", help="optional initial configuration YAML")
    parser.add_argument(
        "--source-id", type=int, action="append", dest="source_ids",
        help="run only this source id regardless of due time (repeatable; active sources only)",
    )
    parsed = parser.parse_args(argv if argv is not None else sys.argv[1:])
    yaml_path = Path(parsed.seed_yaml) if parsed.seed_yaml else None

    settings = get_env_settings()
    setup_logging(settings.log_level)
    logger = get_logger("tender_intelligence.worker")

    correlation_id = new_correlation_id()
    set_correlation_id(correlation_id)
    logger.info(
        "worker starting",
        extra={"correlation_id": correlation_id, "stage": "startup", "status": "started"},
    )

    engine = build_engine(settings.database_url)
    maker = session_factory(engine)
    try:
        counts: dict = {}
        with maker() as session:
            ensure_settings_row(session)
            if yaml_path and yaml_path.exists():
                from tender_intelligence.config.seed import seed_from_yaml

                counts = seed_from_yaml(session, str(yaml_path))
            if settings.dev_alert_email and not counts.get("dev_alert", 0):
                seed_dev_alert_recipient(session, settings.dev_alert_email)
            session.commit()
            seeded = {
                "settings": counts.get("settings", 0),
                "sources": counts.get("sources", 0),
                "tender_recipients": counts.get("tender_recipients", 0),
                "dev_alert_email": 1 if settings.dev_alert_email else 0,
            }

        from tender_intelligence.storage.local import LocalFileSystemStorage

        storage = LocalFileSystemStorage(settings.storage_dir)
        _coordinator, worker, discovery_client, fetcher, _ = build_pipeline(
            maker,
            storage,
            llm_client_factory=llm_client_factory,
            triage_client_factory=triage_client_factory,
            discovery_transport=discovery_transport,
            fetcher_transport=fetcher_transport,
            crawl_policy=crawl_policy,
        )
        worker_report = worker.run_once(source_ids=parsed.source_ids)
        try:
            discovery_client.close()
        finally:
            fetcher.close()

        stage = StageResult(
            stage="worker",
            status="success" if worker_report.succeeded else "failed",
            correlation_id=correlation_id,
        )
        logger.info(
            "worker pass complete",
            extra={
                "correlation_id": correlation_id,
                "stage": "worker",
                "status": stage.status,
                "seeded": seeded,
                "runs": worker_report.ran,
                "failures": worker_report.failed,
            },
        )
        return 0 if stage.status == "success" else 1
    except Exception:  # noqa: BLE001 - top-level process boundary
        logger.exception(
            "worker failed during startup",
            extra={"correlation_id": correlation_id, "stage": "startup", "status": "failed"},
        )
        return 1
    finally:
        set_correlation_id(None)
        engine.dispose()
        logger.info(
            "worker shut down cleanly",
            extra={"correlation_id": correlation_id, "stage": "shutdown", "status": "success"},
        )


if __name__ == "__main__":
    raise SystemExit(main())

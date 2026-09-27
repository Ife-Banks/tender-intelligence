"""FastAPI Admin API over the worker's shared database (docs/09)."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from tender_intelligence import __version__
from tender_intelligence.admin.api import router as api_router
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db.engine import build_engine, session_factory
from tender_intelligence.health import APP_NAME, build_health
from tender_intelligence.logging.structured import get_logger, setup_logging
from tender_intelligence.storage.local import LocalFileSystemStorage

logger = get_logger("tender_intelligence.admin")
_scheduler_log = logging.getLogger("tender_intelligence.admin.scheduler")
STATIC_DIR = Path(__file__).with_name("static")


def _compose_run_coordinator(
    sessions: sessionmaker,
    storage: Any,
    registry: Any,
    llm_client_factory: Any | None = None,
):
    """Compose the existing shared pipeline for Admin's read-only source dry-run action."""
    from tender_intelligence.acquisition.fetcher import DocumentFetcher
    from tender_intelligence.acquisition.service import DocumentAcquisitionService
    from tender_intelligence.dedup.service import DedupService
    from tender_intelligence.notifications.channels import EmailNotificationChannel, NotificationDispatcher
    from tender_intelligence.notifications.service import NotificationService
    from tender_intelligence.orchestrator.config import ConfigLoader
    from tender_intelligence.orchestrator.coordinator import RunCoordinator
    from tender_intelligence.orchestrator.registry import AdapterRegistry
    from tender_intelligence.orchestrator.scheduler import SourceScheduler
    from tender_intelligence.processing.ocr import default_ocr_engine
    from tender_intelligence.processing.service import DocumentProcessingService
    from tender_intelligence.sources.policy import CrawlPolicy
    from tender_intelligence.sources.polite import PoliteHttpClient
    from tender_intelligence.verdict.runtime import build_verdict_handoff

    policy = CrawlPolicy()
    discovery_client = PoliteHttpClient.build(policy)
    fetcher = DocumentFetcher(policy)
    adapter_registry = registry or AdapterRegistry.default(
        policy=policy, fetcher=discovery_client.get
    )
    coordinator = RunCoordinator(
        session_factory=sessions,
        registry=adapter_registry,
        dedup=DedupService(sessions),
        acquisition=DocumentAcquisitionService(sessions, storage=storage, fetcher=fetcher),
        # Same OCR wiring as worker/main.py:build_pipeline, so a dry-run over a scanned tender
        # exercises the same extraction path production uses instead of silently skipping it.
        processing=DocumentProcessingService(
            sessions, storage=storage, ocr=default_ocr_engine()
        ),
        scheduler=SourceScheduler(sessions),
        config_loader=ConfigLoader(sessions),
        triage_client_factory=_triage_profile_client_factory(
            llm_client_factory or _default_llm_client_factory()
        ),
        verdict_handoff=build_verdict_handoff(
            sessions,
            storage,
            _profile_client_factory(llm_client_factory or _default_llm_client_factory()),
        ),
        notification_dispatcher=NotificationDispatcher(
            EmailNotificationChannel(
                NotificationService(
                    sessions,
                    storage=storage,
                    link_base_url=get_env_settings().link_base_url,
                )
            )
        ),
    )
    return coordinator, discovery_client, fetcher, adapter_registry


def _profile_client_factory(factory: Any | None):
    """Adapt the Admin's write-only credential-aware provider seam for Stage B."""
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


def _triage_profile_client_factory(factory: Any | None):
    """Adapt encrypted profile credentials to Stage A's profile-only seam."""
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


def _default_llm_client_factory():
    from tender_intelligence.interfaces.openai_compatible import build_openai_compatible_client

    return build_openai_compatible_client


def _scheduled_source_run(app: FastAPI, source_id: int, lock: threading.Lock) -> None:
    try:
        report = app.state.run_coordinator.run_source(source_id, trigger="scheduled")
        with app.state.session_factory() as session:
            from tender_intelligence.db.audit import log_config_change

            log_config_change(
                session,
                actor="admin-scheduler",
                entity="ScheduledRun",
                entity_id=source_id,
                changed_fields={
                    "correlation_id": report.correlation_id,
                    "run_history_id": report.run_history_id,
                    "trigger": "scheduled",
                    "status": str(report.status),
                },
            )
            session.commit()
    except Exception:  # noqa: BLE001 - one source failure must not terminate the scheduler
        _scheduler_log.exception(
            "scheduled source run failed",
            extra={"stage": "scheduler", "status": "error", "source_id": source_id},
        )
    finally:
        lock.release()


async def _scheduled_source_loop(app: FastAPI, poll_seconds: int) -> None:
    active_run = None
    try:
        while True:
            for decision in app.state.source_scheduler.decisions():
                if not decision.due:
                    continue
                lock = app.state.source_run_locks.setdefault(decision.spec.id, threading.Lock())
                if not lock.acquire(blocking=False):
                    continue
                active_run = asyncio.create_task(
                    asyncio.to_thread(_scheduled_source_run, app, decision.spec.id, lock)
                )
                try:
                    await active_run
                except Exception:  # noqa: BLE001
                    _scheduler_log.exception(
                        "scheduler pass failed",
                        extra={"stage": "scheduler", "status": "error"},
                    )
                finally:
                    active_run = None
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        if active_run is not None:
            try:
                await asyncio.shield(active_run)
            except Exception:  # noqa: BLE001
                pass
        raise


def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Never echo rejected request values: bodies may contain write-only secrets."""
    errors = [
        {
            "location": list(error.get("loc", ())),
            "message": str(error.get("msg", "invalid request")),
            "type": str(error.get("type", "validation_error")),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422, content={"error": {"code": "validation_error", "fields": errors}}
    )


def create_app(
    *,
    engine: Engine | None = None,
    sessions: sessionmaker | None = None,
    object_storage: Any | None = None,
    adapter_registry: Any | None = None,
    run_coordinator: Any | None = None,
    llm_client_factory: Any | None = None,
    notification_service: Any | None = None,
    mail_provider_tester: Any | None = None,
) -> FastAPI:
    """Build an internal Admin app; services remain injectable for offline tests."""
    app_engine = engine
    app_sessions = sessions

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal app_engine, app_sessions
        owned_pipeline_clients = []
        owns_run_coordinator = False
        settings = get_env_settings()
        setup_logging(settings.log_level)
        if app_engine is None:
            app_engine = build_engine(settings.database_url)
        if app_sessions is None:
            app_sessions = session_factory(app_engine)
        app.state.engine = app_engine
        app.state.session_factory = app_sessions
        app.state.object_storage = object_storage or LocalFileSystemStorage(settings.storage_dir)
        from tender_intelligence.orchestrator.scheduler import SourceScheduler

        app.state.source_scheduler = SourceScheduler(app_sessions)
        if app.state.run_coordinator is None:
            try:
                app.state.llm_client_factory = (
                    app.state.llm_client_factory or _default_llm_client_factory()
                )
                (app.state.run_coordinator, *owned_pipeline_clients, app.state.adapter_registry) = (
                    _compose_run_coordinator(
                        app_sessions,
                        app.state.object_storage,
                        app.state.adapter_registry,
                        app.state.llm_client_factory,
                    )
                )
                owns_run_coordinator = True
            except Exception as exc:
                # Keep the read/config API available; source test returns a safe 503.
                logger.warning(
                    "source dry-run coordinator unavailable",
                    extra={
                        "stage": "startup",
                        "status": "unavailable",
                        "error_class": type(exc).__name__,
                    },
                )
        if app.state.notification_service is None and (
            settings.link_signing_secret or settings.master_key
        ):
            try:
                from tender_intelligence.notifications.service import NotificationService

                app.state.notification_service = NotificationService(
                    app_sessions,
                    storage=app.state.object_storage,
                    link_base_url=settings.link_base_url,
                )
            except Exception:
                # Do not prevent the Admin API from starting when optional mail configuration
                # is incomplete; the explicit test-send endpoint reports dependency failure.
                app.state.notification_service = None
        logger.info("admin api started", extra={"status": "success", "stage": "startup"})
        if owns_run_coordinator and settings.scheduler_enabled:
            app.state.scheduler_task = asyncio.create_task(
                _scheduled_source_loop(app, settings.scheduler_poll_seconds),
                name="admin-source-scheduler",
            )
        try:
            yield
        finally:
            scheduler_task = app.state.scheduler_task
            if scheduler_task is not None:
                scheduler_task.cancel()
                try:
                    await scheduler_task
                except asyncio.CancelledError:
                    pass
                app.state.scheduler_task = None
            for client in owned_pipeline_clients:
                try:
                    client.close()
                except Exception:
                    logger.warning(
                        "source dry-run client close failed",
                        extra={
                            "stage": "shutdown",
                            "status": "error",
                            "error_class": type(client).__name__,
                        },
                    )
            if owns_run_coordinator:
                app.state.run_coordinator = None
                app.state.adapter_registry = adapter_registry
            if engine is None and app_engine is not None:
                app_engine.dispose()
                app_engine = None
                app_sessions = None
                app.state.engine = None
                app.state.session_factory = None
            logger.info("admin api stopped", extra={"status": "success", "stage": "shutdown"})

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    @app.exception_handler(Exception)
    def safe_internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": {"code": "internal_error"}})

    app.state.engine = app_engine
    app.state.session_factory = app_sessions
    app.state.object_storage = object_storage
    app.state.adapter_registry = adapter_registry
    app.state.run_coordinator = run_coordinator
    app.state.llm_client_factory = llm_client_factory
    app.state.notification_service = notification_service
    app.state.mail_provider_tester = mail_provider_tester
    app.state.operations = {}
    app.state.source_run_locks = {}
    app.state.scheduler_task = None

    @app.middleware("http")
    async def protect_admin_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    app.include_router(api_router)

    @app.get("/health")
    def health() -> dict:
        """Process/dependency liveness endpoint; detailed health is in the Admin dashboard."""
        return build_health(app.state.engine).to_dict()

    app.mount("/admin", StaticFiles(directory=STATIC_DIR, html=True), name="admin-ui")
    return app


app = create_app()

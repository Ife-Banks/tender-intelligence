"""Tender Intelligence admin web application (Phase 0 foundation).

Thin FastAPI app over the shared database (docs/02 §2.3, docs/09). Phase 0 ships only the
health endpoint; the worker must keep operating when this process is stopped (docs/02
§2.16 worker/admin independence).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from tender_intelligence import __version__
from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db.engine import build_engine
from tender_intelligence.health import APP_NAME, build_health
from tender_intelligence.logging.structured import get_logger, setup_logging

logger = get_logger("tender_intelligence.admin")

_engine: Engine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _engine
    settings = get_env_settings()
    setup_logging(settings.log_level)
    _engine = build_engine(settings.database_url)
    logger.info("admin api started", extra={"status": "success", "stage": "startup"})
    try:
        yield
    finally:
        if _engine is not None:
            _engine.dispose()
        logger.info("admin api stopped", extra={"status": "success", "stage": "shutdown"})


def create_app() -> FastAPI:
    """Application factory producing the Phase 0 admin API."""
    return FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)


app = create_app()


@app.get("/health")
def health() -> dict:
    """Liveness + dependency health for the admin dashboard (docs/02 §2.3)."""
    return build_health(_engine).to_dict()

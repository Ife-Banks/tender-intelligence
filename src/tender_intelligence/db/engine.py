""":mod:`tender_intelligence.db.engine` — engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.config.settings import get_env_settings


def build_engine(database_url: str | None = None, **kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL.

    The production target is PostgreSQL via psycopg (sync); tests inject an in-memory
    SQLite engine through ``uri``.
    """
    url = database_url or get_env_settings().database_url
    engine_kwargs: dict = {}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine_kwargs.update(kwargs)
    return create_engine(url, pool_pre_ping=True, **engine_kwargs)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a sessionmaker bound to *engine* (sync sessions)."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def scoped_session(session_maker: sessionmaker[Session]) -> Iterator[Session]:
    """Context manager yielding a session that is always closed afterwards."""
    session = session_maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

""":mod:`tender_intelligence.db.engine` — engine and session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tender_intelligence.config.settings import get_env_settings


def _use_explicit_sqlite_transactions(engine: Engine) -> None:
    """Make pysqlite SAVEPOINT usage behave like PostgreSQL.

    pysqlite's legacy ``isolation_level=""`` starts a transaction implicitly on the first
    DML. A standalone ``SAVEPOINT`` then owns the transaction, so ``RELEASE SAVEPOINT``
    *commits* it — silently breaking ``begin_nested()`` rollbacks that dedup relies on.
    Putting the driver in autocommit and issuing an explicit ``BEGIN`` through
    SQLAlchemy's ``begin`` event makes savepoints nest inside a real outer transaction.
    """
    # Note: this explicit-BEGIN wiring is a best-effort workaround for pysqlite's
    # legacy implicit transaction behaviour. It is intentionally applied to SQLite
    # engines only; the production PostgreSQL (psycopg) path is unaffected and relies
    # on SQLAlchemy's normal transactional control.
    event.listen(
        engine, "connect", lambda dbapi_conn, _rec: setattr(dbapi_conn, "isolation_level", None)
    )
    event.listen(engine, "begin", lambda conn: conn.exec_driver_sql("BEGIN"))


def build_engine(database_url: str | None = None, **kwargs: Any) -> Engine:
    """Create a SQLAlchemy engine for the configured database URL.

    The production target is PostgreSQL via psycopg (sync); tests inject an in-memory
    SQLite engine through ``uri``. SQLite engines get explicit-BEGIN transaction control
    so SAVEPOINT semantics match the production dialect (pysqlite's implicit-BEGIN mode
    would commit on ``RELEASE SAVEPOINT``).
    """
    url = database_url or get_env_settings().database_url
    engine_kwargs: dict = {}
    if url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine_kwargs.update(kwargs)
    engine = create_engine(url, pool_pre_ping=True, **engine_kwargs)
    if url.startswith("sqlite"):
        _use_explicit_sqlite_transactions(engine)
    return engine


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

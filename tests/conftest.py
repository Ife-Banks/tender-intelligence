"""Shared fixtures for the Phase 0 test suite.

Integration tests run against an in-memory SQLite engine with the Alembic migration applied
(a documented simplification): the real target is PostgreSQL via psycopg (docs/12), and the
migration is deliberately dialect-generic. See README + ``tests/integration/test_migrations.py``.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _migrations_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "migrations")


@pytest.fixture()
def sqlite_engine():
    """In-memory SQLite engine (StaticPool) with the 0001 migration applied."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", _migrations_dir())
    cfg.set_main_option("sqlalchemy.url", "sqlite://")
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory_gr(sqlite_engine):
    return sessionmaker(bind=sqlite_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db_session(session_factory_gr):
    session = session_factory_gr()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def tmp_storage():
    with tempfile.TemporaryDirectory() as d:
        yield d
"""Alembic environment.

The database URL comes, in priority order, from ``sqlalchemy.url`` in ``alembic.ini``
(production default) or the ``TI_DATABASE_URL`` environment variable. Tests may override
it via ``-x db_url=...``.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from tender_intelligence.config.settings import get_env_settings
from tender_intelligence.db import models  # noqa: F401  (register all tables on Base.metadata)
from tender_intelligence.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url() -> str:
    override = config.get_main_option("sqlalchemy.url")
    env_url = os.environ.get("TI_DATABASE_URL")
    if env_url:
        return env_url
    if override:
        return override
    return get_env_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = create_engine(get_url())

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

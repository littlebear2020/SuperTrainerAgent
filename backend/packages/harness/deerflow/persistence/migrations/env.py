"""Alembic environment for DeerFlow application tables.

ONLY manages DeerFlow's tables (runs, threads_meta, users, etc.).
LangGraph's checkpointer tables (checkpoints / writes / checkpoint_*)
are managed by LangGraph itself -- they must not be touched by Alembic.
The ``include_object`` filter from ``_env_filters`` enforces this.
"""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.migrations._env_filters import include_object

# Import all models so metadata is populated.
try:
    import deerflow.persistence.models as models  # register ORM models with Base.metadata

    _ = models
except ImportError:
    logging.getLogger(__name__).warning("Could not import deerflow.persistence.models; Alembic may not detect all tables")

# Import app-level models that use harness Base.metadata.
try:
    import importlib

    importlib.import_module("app.gateway.voice_twin.models")
    importlib.import_module("app.gateway._shared.task_framework.models")
    importlib.import_module("app.gateway.content.models")
    importlib.import_module("app.gateway.content.models_version")
    importlib.import_module("app.gateway.content.models_share")
    importlib.import_module("app.gateway.models.channel_config")
    importlib.import_module("deerflow.persistence.feedback.model")
    importlib.import_module("deerflow.persistence.invite.model")
    importlib.import_module("deerflow.persistence.model_pricing.model")
    importlib.import_module("deerflow.persistence.run.model")
    importlib.import_module("deerflow.persistence.thread_meta.model")
    importlib.import_module("deerflow.persistence.user.model")
except ImportError:
    pass  # app-level models may not be available in all deployment contexts

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # Required for SQLite ALTER TABLE support
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    connectable = create_async_engine(url)

    # Set busy_timeout=30s on every new SQLite connection so alembic's own
    # engine waits for the file lock instead of failing fast with
    # "database is locked". Mirrors the PRAGMA set in persistence/engine.py.
    if url.startswith("sqlite"):

        @event.listens_for(connectable.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ARG001
            cursor = dbapi_conn.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=30000;")
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA synchronous=NORMAL;")
            finally:
                cursor.close()

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

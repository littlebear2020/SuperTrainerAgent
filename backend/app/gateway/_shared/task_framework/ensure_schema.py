"""Startup self-heal for the task_framework tables.

Mirrors ``ensure_writer_schema``: reconciles ``unified_tasks`` against its
ORM model (TaskRow). If the table exists but is missing a column declared on
the model (migration drift — e.g. dirty_frames added after initial deploy),
the column is added and existing rows backfilled with the model's scalar
default.

Why this exists: gateway uses ``create_all`` which creates missing tables but
does NOT add columns to existing tables. Environments that don't run
``alembic upgrade head`` (the common case here) would otherwise hit a
SQLAlchemy error on the first dirty_frames read/write after upgrade. The
companion Alembic migration (c0d1e2f3a4b5) covers environments that do run
migrations; this self-heal is the runtime safety net.

Constraints:
- Only the TaskRow model (``unified_tasks``) is reconciled.
- Tables that do not exist are skipped — ``create_all`` owns table creation.
- SQLite cannot ``ADD COLUMN ... NOT NULL`` on a non-empty table, so added
  columns are nullable; existing rows are backfilled with the scalar default
  (callable / ``func.now()`` defaults are left NULL).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, text

from app.gateway._shared.task_framework.models import TaskRow

logger = logging.getLogger(__name__)

_TASK_TABLES: list[Any] = [TaskRow]


def _reconcile_sync(conn) -> dict[str, list[str]]:
    """Add missing model columns to existing task tables. Runs in run_sync."""
    insp = inspect(conn)
    added: dict[str, list[str]] = {}
    for model in _TASK_TABLES:
        table_name = model.__tablename__
        if not insp.has_table(table_name):
            continue  # never create tables
        live_cols = {c["name"] for c in insp.get_columns(table_name)}
        cols_added: list[str] = []
        for col in model.__table__.columns:
            if col.name in live_cols:
                continue
            type_sql = col.type.compile(dialect=conn.dialect)
            conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{col.name}" {type_sql}'))
            default = col.default
            if default is not None and getattr(default, "is_scalar", False):
                conn.execute(
                    text(f'UPDATE "{table_name}" SET "{col.name}" = :value WHERE "{col.name}" IS NULL'),
                    {"value": default.arg},
                )
            cols_added.append(col.name)
        if cols_added:
            added[table_name] = cols_added
    return added


async def ensure_task_framework_schema(engine) -> dict[str, list[str]]:
    """Reconcile unified_tasks: add missing columns + backfill defaults.

    Returns a ``{table_name: [added_columns]}`` map (empty when nothing changed
    or the engine is absent). Tolerates a ``None`` engine without raising.
    Schema errors propagate to the caller, which wraps the call so a self-heal
    failure never blocks startup.
    """
    if engine is None:
        return {}
    async with engine.begin() as conn:
        added = await conn.run_sync(_reconcile_sync)
    if added:
        logger.info("Task framework schema self-heal applied: %s", added)
    return added

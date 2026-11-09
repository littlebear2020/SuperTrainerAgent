"""Startup self-heal for the voice_twins table.

Reconciles ``voice_twins`` against VoiceTwinRow: if the table exists but is
missing a column declared on the model (migration drift — voice_twins 无任何
alembic 建表迁移兜底，create_all 不补列），the column is added with scalar
defaults backfilled. Never creates tables; never touches other tables.
Failure must not block startup (caller wraps in try/except).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, text

from app.gateway.voice_twin.models import VoiceTwinRow

logger = logging.getLogger(__name__)

_VOICE_TABLES: list[Any] = [VoiceTwinRow]


def _reconcile_sync(conn) -> dict[str, list[str]]:
    insp = inspect(conn)
    added: dict[str, list[str]] = {}
    for model in _VOICE_TABLES:
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


async def ensure_voice_twin_schema(engine) -> dict[str, list[str]]:
    """Add missing model columns to an existing voice_twins table. Returns added cols."""
    async with engine.begin() as conn:
        added = await conn.run_sync(_reconcile_sync)
    if added:
        logger.warning("voice_twins schema self-heal added columns: %s", added)
    return added

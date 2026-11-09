"""Startup self-heal for the three writer tables.

Reconciles ``writer_docs`` / ``writer_doc_versions`` / ``writer_doc_shares``
against their ORM models: if a table exists but is missing a column declared on
the model (migration drift), the column is added and existing rows are
backfilled with the model's scalar default.

Constraints (per the fix plan's "do not touch harness / agent link" principle):

- Only the three writer models are reconciled. Agent-link tables
  (``checkpoints`` / ``writes`` / ``runs`` / ``unified_tasks`` / ``voice_*``)
  are never touched.
- Tables that do not exist are skipped — ``create_all`` owns table creation.
- SQLite cannot ``ADD COLUMN ... NOT NULL`` on a non-empty table, so added
  columns are nullable; existing rows are backfilled with the scalar default
  (callable / ``func.now()`` defaults are left NULL — those are timestamp
  columns that pre-exist anyway).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, text

from app.gateway.content.models import WriterDoc
from app.gateway.content.models_share import WriterDocShare
from app.gateway.content.models_version import WriterDocVersion

logger = logging.getLogger(__name__)

_WRITER_TABLES: list[Any] = [WriterDoc, WriterDocVersion, WriterDocShare]


def _reconcile_sync(conn) -> dict[str, list[str]]:
    """Add missing model columns to existing writer tables. Runs in run_sync."""
    insp = inspect(conn)
    added: dict[str, list[str]] = {}
    for model in _WRITER_TABLES:
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


async def ensure_writer_schema(engine) -> dict[str, list[str]]:
    """Reconcile writer tables: add missing columns + backfill defaults.

    Returns a ``{table_name: [added_columns]}`` map (empty when nothing changed
    or the engine is absent). Tolerates a ``None`` engine (memory backend /
    not initialized) without raising. Schema errors propagate to the caller,
    which wraps the call so a self-heal failure never blocks startup.
    """
    if engine is None:
        return {}
    async with engine.begin() as conn:
        added = await conn.run_sync(_reconcile_sync)
    if added:
        logger.info("Writer schema self-heal applied: %s", added)
    return added


def _versions_unique_migration_sync(conn) -> str:
    """Dedupe + rebuild writer_doc_versions with UNIQUE(doc_id, version_number).

    Runs inside ``run_sync`` (same pattern as ``_reconcile_sync``). Returns a
    status string for logging. Preconditions: runs at gateway startup BEFORE
    the app accepts requests, so there are no concurrent create_version
    writers during the DROP/RENAME window (启动阶段无并发写入前提).
    """
    insp = inspect(conn)
    if not insp.has_table("writer_doc_versions"):
        return "skip:no-table"
    # Fast path: a UNIQUE index on exactly (doc_id, version_number) already
    # exists. NOTE: can't key on "any unique index" — a TEXT PRIMARY KEY always
    # has a unique sqlite_autoindex, which would false-positive the skip
    # (执行中实证：legacy 表被误判 already-unique，迁移从未运行).
    index_rows = conn.exec_driver_sql("PRAGMA index_list('writer_doc_versions')").fetchall()
    for row in index_rows:
        if not row[2]:  # not a unique index
            continue
        idx_cols = [r[2] for r in conn.exec_driver_sql(f"PRAGMA index_info('{row[1]}')").fetchall()]
        if idx_cols == ["doc_id", "version_number"]:
            return "skip:already-unique"
    dup = conn.exec_driver_sql("SELECT doc_id, version_number, COUNT(*) c FROM writer_doc_versions GROUP BY doc_id, version_number HAVING c > 1 LIMIT 1").fetchone()
    if dup:
        logger.warning("writer_doc_versions duplicates found; renumbering per doc_id")
        conn.exec_driver_sql(
            """
            UPDATE writer_doc_versions SET version_number = (
                SELECT COUNT(*) FROM writer_doc_versions v2
                WHERE v2.doc_id = writer_doc_versions.doc_id
                  AND (v2.created_at < writer_doc_versions.created_at
                       OR (v2.created_at = writer_doc_versions.created_at
                           AND v2.id <= writer_doc_versions.id))
            )
            """
        )
    # SQLite can't ADD a UNIQUE constraint via ALTER → rebuild. The DDL is
    # generated from the ORM model (single source of truth): adding a column
    # to WriterDocVersion automatically flows here — no hand-written column
    # list to drift out of sync (评审问题 1).
    table = WriterDocVersion.__table__
    col_defs: list[str] = []
    for col in table.columns:
        type_sql = col.type.compile(dialect=conn.dialect)
        parts = [f'"{col.name}"', type_sql]
        if col.primary_key:
            parts.append("PRIMARY KEY")
        if not col.nullable:
            parts.append("NOT NULL")
        col_defs.append(" ".join(parts))
    col_defs.append("CONSTRAINT uq_wdv_doc_version UNIQUE (doc_id, version_number)")
    conn.exec_driver_sql(f"CREATE TABLE writer_doc_versions_new ({', '.join(col_defs)})")
    col_names = ", ".join(f'"{c.name}"' for c in table.columns)
    conn.exec_driver_sql(f"INSERT INTO writer_doc_versions_new ({col_names}) SELECT {col_names} FROM writer_doc_versions")
    conn.exec_driver_sql("DROP TABLE writer_doc_versions")
    conn.exec_driver_sql("ALTER TABLE writer_doc_versions_new RENAME TO writer_doc_versions")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_writer_doc_versions_doc_id ON writer_doc_versions (doc_id)")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_wdv_doc_version ON writer_doc_versions (doc_id, version_number)")
    return "migrated"


async def ensure_writer_versions_unique(engine) -> None:
    """One-off self-heal: dedupe (doc_id, version_number) and enforce UNIQUE.

    Idempotent: the PRAGMA fast path no-ops once the constraint exists
    (including fresh create_all DBs, which get it from models_version.py).
    Never blocks startup on empty/missing tables — mirrors ensure_writer_schema.
    """
    if engine is None:
        return
    async with engine.begin() as conn:
        status = await conn.run_sync(_versions_unique_migration_sync)
    if status == "migrated":
        logger.info("writer_doc_versions rebuilt with UNIQUE(doc_id, version_number)")

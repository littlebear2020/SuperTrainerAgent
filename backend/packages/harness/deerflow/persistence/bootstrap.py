"""Hybrid schema bootstrap for DeerFlow's application tables.

Replaces the unconditional ``Base.metadata.create_all`` at Gateway startup.
Combines two ideas:

1. ``create_all`` stays the empty-DB fast path -- it renders ``Base.metadata``
   faithfully across SQLite and Postgres dialects without anyone having to
   hand-keep a mirror baseline in sync with the models.
2. **Alembic owns every change from baseline onward.** Any new ORM column /
   table / index must ship as a revision under ``migrations/versions/``.

Three-branch decision (see ``_decide_state``)
---------------------------------------------

| DB state                              | Action                                  |
|---------------------------------------|-----------------------------------------|
| empty (no DeerFlow tables)            | ``create_all`` + ``alembic stamp head`` |
| legacy (DeerFlow tables, no alembic)  | ``create_all`` (baseline tables only, as backfill) + ``stamp _BASELINE_REVISION`` + ``upgrade head`` |
| versioned (``alembic_version`` row)   | ``alembic upgrade head``                |

The legacy branch handles pre-alembic databases that already have at least one
DeerFlow-owned table. ``create_all`` runs first because stamping at
``_BASELINE_REVISION`` makes alembic skip the baseline's own ``create_table``
DDL on the subsequent upgrade -- so any baseline table introduced into
``Base.metadata`` after the user's DB was first provisioned would otherwise
never be created, and the first request hitting that table would 500 with
``no such table``. The backfill is **restricted to ``_BASELINE_TABLE_NAMES``**
so it does not also create tables that future revisions introduce -- those
revisions' own ``op.create_table`` would then fail with ``relation already
exists``. A guard test pins the restriction set against the baseline
revision's actual output.

Column-level shape is answered by each ``versions/*.py`` revision via the
idempotent helpers in ``migrations/_helpers.py`` (``safe_add_column`` no-ops
when the column is already present and ``logger.warning``s on shape drift).
Future schema additions therefore plug in by writing a new revision file --
**no edit to this module is required** *unless* the new revision creates a new
baseline table, in which case ``_BASELINE_TABLE_NAMES`` must be updated to
match (the guard test fires otherwise).

Concurrency safety
------------------

* **Postgres -- true cross-process serialisation.** ``pg_advisory_lock`` runs
  the whole reflect-and-act sequence under an exclusive lock that survives
  cross-process. Concurrent Gateway instances queue cleanly.

* **SQLite -- single-process serialisation, best-effort cross-process.**
  A per-engine ``asyncio.Lock`` serialises async tasks inside one process.
  For the rare cross-process case we rely on SQLite's own file-level write
  lock plus a 30s ``PRAGMA busy_timeout`` set on both the production engine
  (``persistence/engine.py``) and the alembic-spawned engine
  (``migrations/env.py``).

* **Idempotent revisions -- retry fallback.** Column revisions use the helpers
  in ``migrations/_helpers.py`` so repeated post-baseline changes, manual
  ALTERs, or retries after SQLite lock contention do not duplicate work.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Where the alembic environment lives, relative to this file.
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Cached migration head, computed once per process from the disk script tree.
_HEAD_REVISION: str | None = None

# Baseline stamp target for legacy DBs.
#
# P0-5 (2026-08-04): 必须停在 baseline 的**父**（f7a1b3c5d9e2），不是 baseline
# 本身。alembic stamp 的语义是「本 revision 及其全部祖先均已应用」--停在
# d9e0f1a2b3c4 会连带宣告 c0d1e2f3a4b5（add_column dirty_frames）已应用，
# 于是老库永远补不上该列，首次 add_dirty_frame 抛
# `no such column: unified_tasks.dirty_frames`（已实测复现）。
#
# 判据：baseline 只**建表**，但 baseline 之前的**加列** migration 被跳过即致命。
# 前移后 baseline 的 create_table 会真正执行，故 legacy 分支**不得再预建**这些表
# （见 Step 3b），且区间内加列 migration 须幂等（Step 3c）。
_BASELINE_REVISION = "f7a1b3c5d9e2"

# Stable advisory-lock key for Postgres. Two random 32-bit halves picked once
# so we never collide with any other application's advisory locks.
_PG_LOCK_KEY = 0x0DEE_12F1_0BEE_3682

# Tables created by the baseline revision's upgrade(). The legacy branch
# restricts its ``create_all`` backfill to this set so it does NOT pre-empt
# later ``op.create_table`` revisions for models added after baseline --
# those revisions would otherwise fail with ``relation already exists`` if
# ``create_all`` had created their table first.
#
# ``test_baseline_table_names_constant_matches_baseline`` pins this set
# against what the baseline revision actually creates -- editing the baseline
# revision without updating this constant (or vice versa) fires that test.
#
# NOTE: The 4 tables covered by the pre-baseline chain (unified_tasks,
# writer_docs, writer_doc_versions, writer_doc_shares) are intentionally
# EXCLUDED. They already exist in every legacy DB and are NOT created by the
# baseline revision (which only adds the 9 previously uncovered tables).
# Including them here would cause the legacy backfill to try to create them
# again, which would collide with the existing tables.
_BASELINE_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "channel_configs",
        "channel_thread_mappings",
        "feedback",
        "invite_config",
        "run_events",
        "runs",
        "threads_meta",
        "users",
        "voice_twins",
    }
)

# Indexes created by the baseline revision's upgrade(). Same guard-test
# contract as ``_BASELINE_TABLE_NAMES``.
_BASELINE_INDEX_NAMES: frozenset[str] = frozenset(
    {
        # channel_configs
        "ix_channel_configs_user_id",
        # channel_thread_mappings
        "ix_channel_thread_mappings_user_id",
        # feedback
        "ix_feedback_run_id",
        "ix_feedback_thread_id",
        "ix_feedback_user_id",
        # run_events
        "ix_events_run",
        "ix_events_thread_cat_seq",
        "ix_run_events_user_id",
        # runs
        "ix_runs_thread_id",
        "ix_runs_thread_status",
        "ix_runs_user_id",
        # threads_meta
        "ix_threads_meta_assistant_id",
        "ix_threads_meta_user_id",
        # users
        "idx_users_oauth_identity",
        "ix_users_email",
        # voice_twins
        "ix_voice_twins_speaker_id",
        "ix_voice_twins_user_id",
    }
)

# Per-engine SQLite bootstrap locks. WeakKeyDictionary so entries are dropped
# automatically when the engine is collected (important in tests where each
# async test gets its own engine and event loop).
_SQLITE_LOCKS: weakref.WeakKeyDictionary[AsyncEngine, asyncio.Lock] = weakref.WeakKeyDictionary()


def _get_sqlite_local_lock(engine: AsyncEngine) -> asyncio.Lock:
    lock = _SQLITE_LOCKS.get(engine)
    if lock is None:
        lock = asyncio.Lock()
        _SQLITE_LOCKS[engine] = lock
    return lock


def _escape_url_for_alembic(url: str) -> str:
    """Double literal ``%`` so ``ConfigParser`` interpolation leaves the URL intact."""
    return url.replace("%", "%%")


def _alembic_safe_url(engine: AsyncEngine) -> str:
    """Render *engine*'s URL in a form alembic ``set_main_option`` accepts."""
    rendered = engine.url.render_as_string(hide_password=False)
    return _escape_url_for_alembic(rendered)


def _get_alembic_config(engine: AsyncEngine, *, postgres_schema: str = "") -> AlembicConfig:
    """Build an in-process alembic config pointing at our migrations dir."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _alembic_safe_url(engine))
    if postgres_schema:
        cfg.set_main_option("deerflow_pg_schema", postgres_schema)
    return cfg


def _get_head_revision() -> str:
    """Return the head revision id from ``versions/``, cached per process."""
    global _HEAD_REVISION
    if _HEAD_REVISION is None:
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        if head is None:
            raise RuntimeError("alembic has no head revision -- versions/ directory is empty")
        _HEAD_REVISION = head
    return _HEAD_REVISION


def _reflect_state(sync_conn: Any) -> dict[str, bool]:
    """Inspect *sync_conn* and return has_alembic_version / has_deerflow_tables."""
    from deerflow.persistence.base import Base

    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        logger.debug("deerflow.persistence.models not found; metadata may be incomplete")

    insp = sa_inspect(sync_conn)
    reflected = set(insp.get_table_names())
    metadata_tables = set(Base.metadata.tables)
    return {
        "has_alembic_version": "alembic_version" in reflected,
        "has_deerflow_tables": bool(reflected & metadata_tables),
    }


def _decide_state(state: dict[str, bool]) -> str:
    """Map a reflected DB state to one of three branch labels."""
    if state["has_alembic_version"]:
        return "versioned"
    if not state["has_deerflow_tables"]:
        return "empty"
    return "legacy"


def _run_create_all_sync(sync_conn: Any) -> None:
    """Create all DeerFlow-owned tables on *sync_conn*."""
    from deerflow.persistence.base import Base

    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        logger.debug("deerflow.persistence.models not found; bootstrap will create empty schema")

    Base.metadata.create_all(sync_conn)


def _run_baseline_create_all_sync(sync_conn: Any) -> None:
    """Create only the baseline tables on *sync_conn* (idempotent via checkfirst).

    Used by the legacy branch to backfill baseline-era tables missing from
    the user's DB. Restricting the table list to ``_BASELINE_TABLE_NAMES``
    is the safety property: an unrestricted ``create_all`` would also create
    tables introduced by later revisions, which would then collide with
    those revisions' ``op.create_table`` calls when alembic ran upgrade.
    """
    from deerflow.persistence.base import Base

    try:
        import deerflow.persistence.models  # noqa: F401
    except ImportError:
        logger.debug("deerflow.persistence.models not found; baseline backfill may be incomplete")

    baseline_tables = [Base.metadata.tables[name] for name in _BASELINE_TABLE_NAMES if name in Base.metadata.tables]
    Base.metadata.create_all(sync_conn, tables=baseline_tables, checkfirst=True)

    # Explicitly create every baseline-era Index with checkfirst=True.
    # ``create_all`` with ``checkfirst=True`` skips a table and all its
    # subordinate Index objects when the table already exists. An index
    # added to the ORM model after the table was first provisioned would
    # therefore never be created. Scope: only indexes in
    # ``_BASELINE_INDEX_NAMES`` -- post-baseline indexes added by later
    # revisions must not be created prematurely.
    for table in baseline_tables:
        for idx in table.indexes:
            if idx.name not in _BASELINE_INDEX_NAMES:
                continue
            try:
                idx.create(sync_conn, checkfirst=True)
            except Exception:
                logger.warning(
                    "bootstrap: failed to create baseline index %r on %r -- the DB may contain rows that violate the index constraint.",
                    idx.name,
                    table.name,
                )


def _stamp(cfg: AlembicConfig, revision: str) -> None:
    """Synchronous alembic stamp; callers must wrap in ``asyncio.to_thread``."""
    alembic_command.stamp(cfg, revision)


def _upgrade(cfg: AlembicConfig, revision: str) -> None:
    """Synchronous alembic upgrade; callers must wrap in ``asyncio.to_thread``."""
    alembic_command.upgrade(cfg, revision)


@asynccontextmanager
async def _postgres_lock(engine: AsyncEngine):
    """Hold a Postgres session-level advisory lock for the body of the block."""
    async with engine.connect() as conn:
        await conn.execute(text("SET LOCAL idle_in_transaction_session_timeout = 0"))
        await conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _PG_LOCK_KEY})
        try:
            logger.info("bootstrap: acquired postgres advisory lock key=0x%x", _PG_LOCK_KEY)
            yield
        finally:
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _PG_LOCK_KEY})
            except Exception:  # noqa: BLE001
                logger.warning("bootstrap: pg_advisory_unlock raised; session close will release", exc_info=True)


@asynccontextmanager
async def _sqlite_lock(engine: AsyncEngine):
    """Serialise SQLite bootstrap inside one process; cross-process is
    best-effort via SQLite's own file lock + ``PRAGMA busy_timeout``."""
    async with _get_sqlite_local_lock(engine):
        logger.info("bootstrap: acquired sqlite in-process lock")
        yield


def _bootstrap_lock(engine: AsyncEngine, *, backend: str):
    if backend == "postgres":
        return _postgres_lock(engine)
    if backend == "sqlite":
        return _sqlite_lock(engine)
    raise ValueError(f"bootstrap: unsupported backend {backend!r}")


async def bootstrap_schema(engine: AsyncEngine, *, backend: str, postgres_schema: str = "") -> None:
    """Bring the DB schema to head.

    Branch dispatch is documented at module top. ``alembic.command.stamp`` and
    ``alembic.command.upgrade`` are synchronous and are wrapped in
    ``asyncio.to_thread``.
    """
    head = _get_head_revision()
    cfg = _get_alembic_config(engine, postgres_schema=postgres_schema if backend == "postgres" else "")

    async with _bootstrap_lock(engine, backend=backend):
        async with engine.connect() as conn:
            state = await conn.run_sync(_reflect_state)
        decision = _decide_state(state)

        if decision == "empty":
            logger.info("bootstrap: branch=empty -> create_all + stamp head (%s)", head)
            async with engine.begin() as conn:
                await conn.run_sync(_run_create_all_sync)
            await asyncio.to_thread(_stamp, cfg, head)

        elif decision == "legacy":
            logger.info(
                "bootstrap: branch=legacy -> stamp %s + upgrade head (%s)",
                _BASELINE_REVISION,
                head,
            )
            # P0-5: 不再预建 baseline 表。stamp 点前移到 baseline 之前后，
            # baseline revision 自己的 op.create_table 会真正执行；此处若再
            # create_all 预建，upgrade 时必撞 "table already exists"。
            await asyncio.to_thread(_stamp, cfg, _BASELINE_REVISION)
            await asyncio.to_thread(_upgrade, cfg, "head")

        elif decision == "versioned":
            logger.info("bootstrap: branch=versioned -> upgrade head (%s)", head)
            await asyncio.to_thread(_upgrade, cfg, "head")

        else:  # pragma: no cover
            raise RuntimeError(f"bootstrap: unhandled decision {decision!r}")

    logger.info("bootstrap: complete (backend=%s)", backend)

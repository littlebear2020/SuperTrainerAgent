"""Task 2 (闸2): Gateway SQLite busy_timeout aligned to 30s.

The Gateway engine used Python sqlite3's 5s default while the Worker sets 30s;
under writer contention the Gateway failed fast with 'database is locked'. This
verifies the connect listener now PRAGMAs busy_timeout=30000. Read-back via a real
aiosqlite connection was confirmed to work on the pre-fix code (returned 5000),
so this is a true integration assertion, stronger than checking a PRAGMA string
was emitted.
"""

import tempfile

import pytest


@pytest.mark.asyncio
async def test_sqlite_engine_sets_busy_timeout_30s():
    """The connect listener should PRAGMA busy_timeout = 30000."""
    import deerflow.persistence.engine as engine_mod
    from deerflow.config.database_config import DatabaseConfig

    tmpdir = tempfile.mkdtemp()
    cfg = DatabaseConfig(backend="sqlite", sqlite_dir=tmpdir)
    try:
        await engine_mod.init_engine_from_config(cfg)
        eng = engine_mod.get_engine()
        assert eng is not None
        async with eng.connect() as conn:
            result = await conn.exec_driver_sql("PRAGMA busy_timeout;")
            row = result.fetchone()
            assert row is not None
            assert row[0] == 30000, f"busy_timeout should be 30000, got {row[0]}"
    finally:
        await engine_mod.close_engine()


@pytest.mark.asyncio
async def test_checkpointer_sqlite_sets_busy_timeout_30s():
    """The checkpointer shares deerflow.db with the app engine, so its connection
    must ALSO wait 30s — not SQLite's 5s default. Otherwise under writer contention
    the checkpointer fails fast with 'database is locked' while the app engine
    (30s) and Worker (30s) keep waiting, defeating 闸2's goal of aligning all
    writers on the shared file. (Code review Issue 1: the original Task 2 fix set
    busy_timeout only on the engine, missing the checkpointer connection.)"""
    from deerflow.config.database_config import DatabaseConfig
    from deerflow.runtime.checkpointer.async_provider import _async_checkpointer_from_database

    tmpdir = tempfile.mkdtemp()
    cfg = DatabaseConfig(backend="sqlite", sqlite_dir=tmpdir)
    async with _async_checkpointer_from_database(cfg) as saver:
        # AsyncSqliteSaver exposes its aiosqlite connection as ``.conn``.
        cur = await saver.conn.execute("PRAGMA busy_timeout;")
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == 30000, f"checkpointer busy_timeout should be 30000, got {row[0]}"

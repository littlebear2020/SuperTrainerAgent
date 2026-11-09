"""Tests for ensure_writer_schema startup self-heal (writer 3 tables only).

Verifies the migration-drift fix: when a writer table exists but is missing a
column declared on the ORM model (e.g. ``doc_type``), the self-heal adds the
column and backfills existing rows with the model's scalar default. It must
never create tables and must tolerate a missing engine.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.gateway.content.ensure_writer_schema import ensure_writer_schema

# Old (drifted) writer_docs DDL: everything the live table had BEFORE doc_type.
_OLD_WRITER_DOCS_DDL = """
CREATE TABLE writer_docs (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    title VARCHAR(200) NOT NULL,
    content_json TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    deleted_at DATETIME
)
"""


async def _columns(conn, table: str) -> set[str]:
    def _f(sync_conn):
        return {c["name"] for c in sa_inspect(sync_conn).get_columns(table)}

    return await conn.run_sync(_f)


@pytest.mark.asyncio
async def test_self_heal_adds_missing_doc_type_and_backfills():
    """Drifted writer_docs (no doc_type) -> column added, old rows backfilled."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_OLD_WRITER_DOCS_DDL))
        await conn.execute(text("INSERT INTO writer_docs (id, user_id, title, content_json, created_at, updated_at) VALUES ('doc_a', 'u1', 'Old', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))

    added = await ensure_writer_schema(engine)

    assert "writer_docs" in added
    assert "doc_type" in added["writer_docs"]
    async with engine.begin() as conn:
        assert "doc_type" in await _columns(conn, "writer_docs")
        backfilled = (await conn.execute(text("SELECT doc_type FROM writer_docs WHERE id = 'doc_a'"))).scalar_one()
    assert backfilled == "general"
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_heal_does_not_create_missing_tables():
    """versions/shares absent -> must NOT be created; only existing tables touched."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(text(_OLD_WRITER_DOCS_DDL))

    added = await ensure_writer_schema(engine)

    async with engine.begin() as conn:
        has_versions = await conn.run_sync(lambda c: sa_inspect(c).has_table("writer_doc_versions"))
        has_shares = await conn.run_sync(lambda c: sa_inspect(c).has_table("writer_doc_shares"))
    assert has_versions is False
    assert has_shares is False
    assert set(added.keys()) <= {"writer_docs"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_heal_noop_when_schema_current():
    """Fresh full schema (all columns present) -> nothing added."""
    from app.gateway.content.models import WriterDoc

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)

    assert await ensure_writer_schema(engine) == {}
    await engine.dispose()


@pytest.mark.asyncio
async def test_self_heal_handles_none_engine():
    """No engine (memory backend / not initialized) -> no error, empty result."""
    assert await ensure_writer_schema(None) == {}


# ── P2-2: unique (doc_id, version_number) self-heal migration ────────────


@pytest.mark.asyncio
async def test_versions_unique_migration_dedupes_and_enforces(tmp_path):
    """Legacy DB with duplicate version_numbers → dedupe + UNIQUE constraint."""
    db = tmp_path / "mig.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with eng.begin() as conn:
        # Legacy table WITHOUT unique constraint
        await conn.execute(
            text(
                "CREATE TABLE writer_doc_versions ("
                "id VARCHAR(64) PRIMARY KEY, doc_id VARCHAR(64) NOT NULL,"
                " version_number INTEGER NOT NULL, title VARCHAR(200) NOT NULL,"
                " content_json TEXT, snapshot_reason VARCHAR(50),"
                " created_at DATETIME NOT NULL)"
            )
        )
        await conn.execute(text("INSERT INTO writer_doc_versions VALUES ('v1','d1',1,'t','c','auto','2026-07-01 00:00:00'), ('v2','d1',1,'t','c','auto','2026-07-01 00:00:01'), ('v3','d1',2,'t','c','auto','2026-07-01 00:00:02')"))
    from app.gateway.content.ensure_writer_schema import ensure_writer_versions_unique

    await ensure_writer_versions_unique(eng)
    async with eng.begin() as conn:
        rows = (await conn.execute(text("SELECT id, version_number FROM writer_doc_versions ORDER BY version_number"))).fetchall()
        assert [r[1] for r in rows] == [1, 2, 3]  # renumbered dense
        # Unique constraint enforced: insert duplicate (d1, 3) must fail
        with pytest.raises(Exception):
            await conn.execute(text("INSERT INTO writer_doc_versions VALUES ('v9','d1',3,'t','c','auto','2026-07-02 00:00:00')"))
            await conn.execute(text("SELECT 1"))  # force flush inside tx
    # Idempotent: second run is a no-op fast path
    await ensure_writer_versions_unique(eng)
    await eng.dispose()

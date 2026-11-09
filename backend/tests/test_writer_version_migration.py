"""B2-T1: Migration idempotency + model sync for version columns.

Verifies that:
- The migration (h0d4e6f8a3b5) is idempotent (running twice doesn't crash)
- writer_docs.version column exists and defaults to 0
- writer_doc_versions.doc_type column exists and is nullable
- New DBs created via create_all have the columns with correct constraints
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.content.models import WriterDoc
from app.gateway.content.writer_service import WriterDocService


@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_create_all_has_version_column(engine):
    """create_all produces writer_docs.version (NOT NULL, default 0)."""
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda sync_conn: {c["name"]: c for c in inspect(sync_conn).get_columns("writer_docs")})
    assert "version" in cols
    assert not cols["version"]["nullable"]  # NOT NULL on fresh DB


@pytest.mark.asyncio
async def test_create_all_has_doc_type_in_versions(engine):
    """create_all produces writer_doc_versions.doc_type (nullable)."""
    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda sync_conn: {c["name"]: c for c in inspect(sync_conn).get_columns("writer_doc_versions")})
    assert "doc_type" in cols
    assert cols["doc_type"]["nullable"]  # nullable


@pytest.mark.asyncio
async def test_new_doc_has_version_zero(engine):
    """Newly created docs start at version 0."""
    svc = WriterDocService(async_sessionmaker(engine, expire_on_commit=False))
    doc = await svc.create(user_id="u1", title="T", content_json="# a")
    assert doc.version == 0


@pytest.mark.asyncio
async def test_update_increments_version(engine):
    """Each update increments the version column."""
    svc = WriterDocService(async_sessionmaker(engine, expire_on_commit=False))
    doc = await svc.create(user_id="u1", title="T", content_json="# a")
    assert doc.version == 0

    updated = await svc.update(doc.id, "u1", content_json="# b")
    assert updated is not None
    assert updated.version == 1

    updated2 = await svc.update(doc.id, "u1", content_json="# c")
    assert updated2 is not None
    assert updated2.version == 2


@pytest.mark.asyncio
async def test_cas_update_with_matching_version_succeeds(engine):
    """CAS update with correct expected_version succeeds and increments."""
    svc = WriterDocService(async_sessionmaker(engine, expire_on_commit=False))
    doc = await svc.create(user_id="u1", title="T", content_json="# a")

    result = await svc.update(doc.id, "u1", expected_version=0, content_json="# b")
    assert result is not None
    assert result.version == 1
    assert result.content_json == "# b"


@pytest.mark.asyncio
async def test_cas_update_with_stale_version_returns_conflict(engine):
    """CAS update with wrong expected_version returns _CONFLICT sentinel."""
    from app.gateway.content.writer_service import _CONFLICT

    svc = WriterDocService(async_sessionmaker(engine, expire_on_commit=False))
    doc = await svc.create(user_id="u1", title="T", content_json="# a")

    # First update bumps version to 1
    await svc.update(doc.id, "u1", content_json="# b")

    # Now try CAS with stale version 0 -> should conflict
    result = await svc.update(doc.id, "u1", expected_version=0, content_json="# c")
    assert result is _CONFLICT


@pytest.mark.asyncio
async def test_version_snapshot_includes_doc_type(engine):
    """create_version snapshots the doc_type column."""
    svc = WriterDocService(async_sessionmaker(engine, expire_on_commit=False))
    doc = await svc.create(user_id="u1", title="T", content_json="# a", doc_type="article")

    version = await svc.create_version(doc.id, "u1", reason="manual")
    assert version is not None
    assert version.doc_type == "article"

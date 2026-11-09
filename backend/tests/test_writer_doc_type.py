"""Tests for doc_type field on WriterDoc model, schemas, and service."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.content.models import WriterDoc
from app.gateway.content.schemas import WriterDocCreate, WriterDocResponse, WriterDocUpdate
from app.gateway.content.writer_service import WriterDocService

# ---------------------------------------------------------------------------
# Model: column exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writer_doc_has_doc_type_column():
    """WriterDoc model must expose a doc_type column."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    async with engine.begin() as conn:
        cols = await conn.run_sync(lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("writer_docs")})
    assert "doc_type" in cols
    await engine.dispose()


# ---------------------------------------------------------------------------
# Schemas: doc_type accepted on create, update, and returned on response
# ---------------------------------------------------------------------------


def test_writer_doc_create_accepts_doc_type():
    """WriterDocCreate must accept an optional doc_type field."""
    schema = WriterDocCreate(title="Test", doc_type="article")
    assert schema.doc_type == "article"


def test_writer_doc_create_defaults_doc_type_to_general():
    """WriterDocCreate must default doc_type to 'general'."""
    schema = WriterDocCreate(title="Test")
    assert schema.doc_type == "general"


def test_writer_doc_update_accepts_doc_type():
    """WriterDocUpdate must accept an optional doc_type field."""
    schema = WriterDocUpdate(doc_type="script")
    assert schema.doc_type == "script"


def test_writer_doc_update_doc_type_optional():
    """WriterDocUpdate must allow doc_type to be omitted."""
    schema = WriterDocUpdate(title="New Title")
    assert schema.doc_type is None


def test_writer_doc_response_includes_doc_type():
    """WriterDocResponse must include doc_type field."""
    schema = WriterDocResponse(
        id="doc_test",
        user_id="user1",
        title="Test",
        content_json="{}",
        doc_type="course",
    )
    assert schema.doc_type == "course"


def test_writer_doc_create_rejects_invalid_doc_type():
    """WriterDocCreate must reject unknown doc_type values."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WriterDocCreate(title="Test", doc_type="invalid_type")


# ---------------------------------------------------------------------------
# Service: create and update propagate doc_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_create_with_doc_type():
    """WriterDocService.create must persist doc_type."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    svc = WriterDocService(sf)

    doc = await svc.create(user_id="user1", title="My Article", doc_type="article")
    assert doc.doc_type == "article"

    fetched = await svc.get(doc.id, "user1")
    assert fetched is not None
    assert fetched.doc_type == "article"
    await engine.dispose()


@pytest.mark.asyncio
async def test_service_create_defaults_doc_type():
    """WriterDocService.create must default doc_type to 'general'."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    svc = WriterDocService(sf)

    doc = await svc.create(user_id="user1", title="My Doc")
    assert doc.doc_type == "general"
    await engine.dispose()


@pytest.mark.asyncio
async def test_service_update_doc_type():
    """WriterDocService.update must allow changing doc_type."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    svc = WriterDocService(sf)

    doc = await svc.create(user_id="user1", title="My Doc", doc_type="general")
    updated = await svc.update(doc.id, "user1", doc_type="script")
    assert updated is not None
    assert updated.doc_type == "script"
    await engine.dispose()

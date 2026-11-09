"""B5-T1: Writer doc list pagination/search/filter/sort tests."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.content.models import WriterDoc
from app.gateway.content.writer_service import WriterDocService
from app.gateway.routers.writer import router


@pytest_asyncio.fixture()
async def svc():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    yield WriterDocService(async_sessionmaker(eng, expire_on_commit=False))
    await eng.dispose()


def _make_app(svc) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.writer_doc_service = svc
    return app


@pytest.mark.asyncio
async def test_list_pagination_has_more(svc):
    """Fetching limit items with more available -> has_more=True."""
    for i in range(5):
        await svc.create(user_id="u1", title=f"Doc {i}", content_json="# a")
    items, has_more = await svc.list_docs("u1", limit=3, offset=0)
    assert len(items) == 3
    assert has_more is True


@pytest.mark.asyncio
async def test_list_pagination_no_more(svc):
    """Fetching all items -> has_more=False."""
    for i in range(3):
        await svc.create(user_id="u1", title=f"Doc {i}", content_json="# a")
    items, has_more = await svc.list_docs("u1", limit=10, offset=0)
    assert len(items) == 3
    assert has_more is False


@pytest.mark.asyncio
async def test_list_search_by_title(svc):
    """Search filters by ilike on title."""
    await svc.create(user_id="u1", title="Python 入门", content_json="# a")
    await svc.create(user_id="u1", title="Java 进阶", content_json="# a")
    items, _ = await svc.list_docs("u1", search="Python")
    assert len(items) == 1
    assert items[0].title == "Python 入门"


@pytest.mark.asyncio
async def test_list_filter_by_doc_type(svc):
    """Filter by doc_type."""
    await svc.create(user_id="u1", title="A", content_json="# a", doc_type="article")
    await svc.create(user_id="u1", title="B", content_json="# b", doc_type="course")
    items, _ = await svc.list_docs("u1", doc_type="course")
    assert len(items) == 1
    assert items[0].title == "B"


@pytest.mark.asyncio
async def test_list_sort_by_title_asc(svc):
    """Sort by title ascending."""
    await svc.create(user_id="u1", title="Charlie", content_json="# a")
    await svc.create(user_id="u1", title="Alpha", content_json="# a")
    await svc.create(user_id="u1", title="Bravo", content_json="# a")
    items, _ = await svc.list_docs("u1", sort_by="title", sort_dir="asc")
    assert [d.title for d in items] == ["Alpha", "Bravo", "Charlie"]


@pytest.mark.asyncio
async def test_list_offset_pagination(svc):
    """Offset skips first page of results."""
    for i in range(5):
        await svc.create(user_id="u1", title=f"Doc {i}", content_json="# a")
    items, has_more = await svc.list_docs("u1", limit=2, offset=2)
    assert len(items) == 2
    assert has_more is True


@pytest.mark.asyncio
async def test_list_api_returns_wrapper(svc):
    """B5-T1: GET /api/writer/docs returns {items, has_more} (breaking contract)."""
    await svc.create(user_id="u1", title="Test", content_json="# a")
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    with patch("app.gateway.routers.writer.get_effective_user_id", return_value="u1"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/api/writer/docs")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert "has_more" in body
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1

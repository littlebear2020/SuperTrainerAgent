"""PUT /api/writer/docs/{id} optimistic lock via base_version (B2-T2)."""

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
async def test_put_without_base_is_backward_compatible(svc):
    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    with patch("app.gateway.routers.writer.get_effective_user_id", return_value="user1"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.put(f"/api/writer/docs/{doc.id}", json={"content_json": "# x"})
    assert resp.status_code == 200


# ── B2-T2: CAS via base_version (integer) ────────────────────────────────


@pytest.mark.asyncio
async def test_put_with_stale_base_version_returns_409(svc):
    """B2-T2: PUT with a stale base_version gets 409 (CAS atomic check)."""
    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    # Someone else writes first, bumping version to 1
    await svc.update(doc.id, "user1", content_json="# newer")
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    with patch("app.gateway.routers.writer.get_effective_user_id", return_value="user1"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.put(
                f"/api/writer/docs/{doc.id}",
                json={"content_json": "# mine", "base_version": 0},  # stale: current is 1
            )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_put_with_matching_base_version_succeeds_and_bumps(svc):
    """B2-T2: PUT with matching base_version succeeds and version increments."""
    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    assert doc.version == 0
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    with patch("app.gateway.routers.writer.get_effective_user_id", return_value="user1"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.put(
                f"/api/writer/docs/{doc.id}",
                json={"content_json": "# mine", "base_version": 0},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["content_json"] == "# mine"


@pytest.mark.asyncio
async def test_cas_rename_creates_snapshot(svc):
    """RC1 regression: CAS path (base_version) must create a 'rename' version
    snapshot when title changes — was missing in the first round, fixed in RC1."""
    doc = await svc.create(user_id="user1", title="原名", content_json="# a")
    app = _make_app(svc)
    transport = httpx.ASGITransport(app=app)
    with patch("app.gateway.routers.writer.get_effective_user_id", return_value="user1"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.put(
                f"/api/writer/docs/{doc.id}",
                json={"title": "新名", "base_version": 0},
            )
    assert resp.status_code == 200
    assert resp.json()["title"] == "新名"
    # Verify a rename snapshot was created
    versions = await svc.list_versions(doc.id, "user1")
    rename_snaps = [v for v in versions if v.snapshot_reason == "rename"]
    assert len(rename_snaps) == 1, f"Expected 1 rename snapshot, got {len(rename_snaps)}"
    assert rename_snaps[0].title == "原名"  # snapshot has the OLD title

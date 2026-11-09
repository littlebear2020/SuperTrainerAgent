"""Public share access (P1-2) + response hygiene (P2-29) + bounds (P2-28)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.gateway.auth_middleware import _is_public
from app.gateway.content.schemas import CreateShareRequest
from app.gateway.routers.writer import share_router


def test_share_doc_get_is_public():
    assert _is_public("/api/writer/share/sometoken123") is True


def test_share_image_get_is_public():
    assert _is_public("/api/writer/assets/images/raw/deadbeef.png") is True


def test_writer_docs_api_still_private():
    assert _is_public("/api/writer/docs") is False
    assert _is_public("/api/writer/docs/doc_abc") is False


def test_writer_upload_and_generate_stay_private():
    """防回归（评审问题 2）：放通的只是 GET raw 前缀；写端点永远不进白名单，
    防未来有人把 upload 挪到 raw 前缀下或误配更宽前缀。"""
    assert _is_public("/api/writer/assets/images/upload") is False
    assert _is_public("/api/writer/assets/images/generate") is False


def test_create_share_request_bounds():
    with pytest.raises(ValidationError):
        CreateShareRequest(expires_days=0)
    with pytest.raises(ValidationError):
        CreateShareRequest(expires_days=-3)
    with pytest.raises(ValidationError):
        CreateShareRequest(expires_days=366)
    assert CreateShareRequest(expires_days=30).expires_days == 30
    assert CreateShareRequest().expires_days is None


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(share_router)

    class _Svc:
        async def get_by_share_token(self, token: str):
            return SimpleNamespace(
                id="doc_x",
                user_id="owner-secret-uuid",
                title="公开文档",
                content_json="# hi",
                doc_type="general",
                created_at=None,
                updated_at=None,
            )

    app.state.writer_doc_service = _Svc()
    return app


@pytest.mark.asyncio
async def test_shared_doc_response_hides_owner_user_id():
    transport = httpx.ASGITransport(app=_make_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/api/writer/share/tok123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "公开文档"
    assert "user_id" not in body

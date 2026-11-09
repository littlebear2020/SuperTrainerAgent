"""Tests for UGC thumbnail upload / AI-generate / serve endpoints."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config, get_current_user_from_request
from app.gateway.routers import gallery
from deerflow.config.app_config import AppConfig

app = FastAPI()
app.include_router(gallery.router)
client = TestClient(app)


class MockUser:
    def __init__(self):
        self.id = uuid4()
        self.email = "u@example.com"


@pytest.fixture
def thumb_env(monkeypatch, tmp_path):
    gallery.load_gallery_cases.cache_clear()
    monkeypatch.setattr(gallery, "GALLERY_FILE_PATH", tmp_path / "gallery-cases.yaml")
    monkeypatch.setattr(gallery, "UGC_FILE_PATH", tmp_path / "ugc-cases.json")
    monkeypatch.setattr(gallery, "LIKES_FILE_PATH", tmp_path / "likes.json")
    monkeypatch.setattr(gallery, "DB_DIR", tmp_path)
    mock_config = AppConfig.model_validate({"log_level": "info", "sandbox": {"use": "test"}, "gallery": {"enabled": True}})
    app.dependency_overrides[get_config] = lambda: mock_config
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser()
    yield tmp_path
    app.dependency_overrides.clear()


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_VALID_B64 = base64.b64encode(b"\x89PNG\r\n\x1a\nimagedata").decode()


def test_upload_thumbnail_success(thumb_env):
    res = client.post("/api/gallery/upload-thumbnail", files={"file": ("a.png", _PNG, "image/png")})
    assert res.status_code == 200, res.text
    url = res.json()["thumbnail"]
    assert url.startswith("/api/gallery/thumbnails/")
    fname = url.rsplit("/", 1)[1]
    assert (thumb_env / "thumbnails" / fname).is_file()


def test_upload_thumbnail_rejects_oversize(thumb_env):
    big = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024 + 1)
    res = client.post("/api/gallery/upload-thumbnail", files={"file": ("big.png", big, "image/png")})
    assert res.status_code == 413


def test_upload_thumbnail_rejects_non_image_magic_bytes(thumb_env):
    # forged Content-Type but non-image bytes
    res = client.post("/api/gallery/upload-thumbnail", files={"file": ("a.png", b"not an image at all", "image/png")})
    assert res.status_code == 400


def test_upload_thumbnail_rejects_svg(thumb_env):
    svg = b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    res = client.post("/api/gallery/upload-thumbnail", files={"file": ("a.svg", svg, "image/svg+xml")})
    assert res.status_code == 400


def test_generate_thumbnail_provider_not_configured(thumb_env):
    with patch("app.gateway.routers.gallery._resolve_image_provider", return_value=None):
        res = client.post("/api/gallery/generate-thumbnail", json={"title": "T", "category": "视觉设计"})
    assert res.status_code == 500
    assert "not configured" in res.json()["detail"]


def test_generate_thumbnail_success_data_url(thumb_env):
    fake_ref = SimpleNamespace(url=f"data:image/png;base64,{_VALID_B64}")
    fake_provider = AsyncMock()
    fake_provider.generate_image = AsyncMock(return_value=fake_ref)
    fake_provider.close = AsyncMock()
    with patch("app.gateway.routers.gallery._resolve_image_provider", return_value=fake_provider):
        res = client.post("/api/gallery/generate-thumbnail", json={"title": "封面", "category": "视觉设计"})
    assert res.status_code == 200, res.text
    url = res.json()["thumbnail"]
    assert url.startswith("/api/gallery/thumbnails/")
    fname = url.rsplit("/", 1)[1]
    assert (thumb_env / "thumbnails" / fname).read_bytes() == base64.b64decode(_VALID_B64)


@pytest.mark.asyncio
async def test_persist_thumbnail_http_download_failure(tmp_path, monkeypatch):
    """External URL whose download fails must raise (never return the raw external URL).

    C2 后下载走 safe_download（SSRF 守卫）——失败以 httpx 异常形态冒泡；
    合同不变：raise 且绝不返回外链原样。
    """
    monkeypatch.setattr(gallery, "DB_DIR", tmp_path)
    fake_ref = SimpleNamespace(url="https://example.com/x.png")

    import httpx

    async def _boom(url, **k):
        raise httpx.HTTPStatusError("502", request=httpx.Request("GET", url), response=httpx.Response(502))

    with (
        patch.object(gallery, "safe_download", _boom),
        pytest.raises(httpx.HTTPStatusError),
    ):
        await gallery._persist_thumbnail_from_ref(fake_ref, http_client=None)


def test_serve_thumbnail_roundtrip(thumb_env):
    up = client.post("/api/gallery/upload-thumbnail", files={"file": ("a.png", _PNG, "image/png")})
    fname = up.json()["thumbnail"].rsplit("/", 1)[1]
    res = client.get(f"/api/gallery/thumbnails/{fname}")
    assert res.status_code == 200


def test_serve_thumbnail_missing(thumb_env):
    res = client.get("/api/gallery/thumbnails/does-not-exist.png")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# 2026-07-27 T10：内容感知封面（分类视觉映射 + 内容注入）
# ---------------------------------------------------------------------------


def test_cover_prompt_includes_category_visual_direction():
    from app.gateway.routers.gallery import _build_cover_prompt

    prompt = _build_cover_prompt("周报助手", "办公效率", None)
    assert "周报助手" in prompt
    assert prompt != "为《周报助手》生成一张办公效率风格的极简封面图，16:9，无文字，高质感"
    # 每个分类必须有差异化视觉方向，不能再是裸标签
    p2 = _build_cover_prompt("周报助手", "数据分析", None)
    assert prompt != p2, "不同分类的视觉方向必须不同"


def test_cover_prompt_injects_content_hint():
    from app.gateway.routers.gallery import _build_cover_prompt

    prompt = _build_cover_prompt("PDF转PPT", "办公效率", "上传PDF文档自动提炼大纲并生成可编辑PPT")
    assert "上传PDF文档自动提炼大纲" in prompt


def test_generate_thumbnail_endpoint_passes_content_prompt(thumb_env):
    """端点必须把 body.prompt 注入生图 prompt（前端曾从不传 → 分支被饿死，2026-07-24 P2）。"""
    fake_ref = SimpleNamespace(url=f"data:image/png;base64,{_VALID_B64}")
    fake_provider = AsyncMock()
    fake_provider.generate_image = AsyncMock(return_value=fake_ref)
    fake_provider.close = AsyncMock()
    with patch("app.gateway.routers.gallery._resolve_image_provider", return_value=fake_provider):
        res = client.post(
            "/api/gallery/generate-thumbnail",
            json={"title": "PDF转PPT", "category": "办公效率", "prompt": "文档转换工作流"},
        )
    assert res.status_code == 200, res.text
    prompt = fake_provider.generate_image.call_args.args[0]
    assert "文档转换工作流" in prompt

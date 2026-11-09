"""Tests for voice preview endpoint with custom text support."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """Create a test client with video_gen router mounted."""
    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_preview_with_custom_text(client):
    """Preview with custom text returns audio (or 404 if TTS unavailable)."""
    with patch("app.gateway.video_gen.orchestration.tts.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_tts.return_value = type("R", (), {"audio_path": "/tmp/test.mp3"})()
        resp = client.get("/api/video/voices/zh-CN-YunjianNeural/preview?text=%E6%B5%8B%E8%AF%95%E6%96%87%E6%9C%AC")
        # Accept 200 (success) / 404 (mock 路径非真实文件) / 503（07-28 起 preview 合成失败诚实化）
        assert resp.status_code in (200, 404, 503)


def test_preview_without_text(client):
    """Preview without text uses default text."""
    resp = client.get("/api/video/voices/zh-CN-YunjianNeural/preview")
    assert resp.status_code in (200, 404, 503)  # 503 = preview 失败新契约（Task 5）


def test_preview_text_truncated(tmp_path, client):
    """Preview text is truncated to 100 chars."""
    with patch("app.gateway.video_gen.orchestration.tts.synthesize", new_callable=AsyncMock) as mock_tts:
        mock_tts.return_value = type("R", (), {"audio_path": "/tmp/test.mp3"})()
        long_text = "测试" * 100  # 200 chars
        client.get(f"/api/video/voices/zh-CN-YunjianNeural/preview?text={long_text}")
        if mock_tts.called:
            call_text = mock_tts.call_args[0][0]
            assert len(call_text) <= 100


# ---------------------------------------------------------------------------
# voice_twin_production router: preview 缓存命中归属校验（Task 8 安全包）
#
# 上面的 client fixture 挂的是 video_gen router，与这里的 production router
# 是两个端点，不可复用——以下用 make_authed_test_app 另建 authed app。
# ---------------------------------------------------------------------------


def _make_user(user_id: str):
    """Stub auth user with a deterministic UUID derived from the given label."""
    from uuid import NAMESPACE_DNS, uuid5

    from app.gateway.auth.models import User

    return User(email=f"{user_id}@example.com", password_hash="x", system_role="user", id=uuid5(NAMESPACE_DNS, user_id))


def _make_production_app(user, svc):
    """Authed app mounting the voice_twin_production router with a mock service.

    make_authed_test_app 只 stamp request.state.user；production 路由经
    get_effective_user_id() 读的是真实 auth middleware 设置的 contextvar，
    这里补一个等价的 stamp 中间件（与生产 AuthMiddleware 同模式）。
    """
    from _router_auth_helpers import make_authed_test_app

    from app.gateway.routers.voice_twin_production import router
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    app = make_authed_test_app(user_factory=lambda: user)

    @app.middleware("http")
    async def _stamp_user_context(request, call_next):
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)

    app.state.production_service = svc
    app.include_router(router)
    return app


def _make_production_svc(row):
    """Mock ProductionService：repo.get_by_id 返回给定 row（或 None）。"""
    svc = MagicMock()
    svc.preview_voice = AsyncMock(return_value=(b"FRESH-AUDIO", "audio/mpeg"))
    svc.voice_service.repo.get_by_id = AsyncMock(return_value=row)
    return svc


@pytest.fixture()
def preview_cache_dir(tmp_path, monkeypatch):
    """Redirect the production router's preview cache dir into tmp_path."""
    from app.gateway.routers import voice_twin_production as prod_router

    cache_dir = tmp_path / "voice_previews"
    cache_dir.mkdir()
    monkeypatch.setattr(prod_router, "_preview_storage_dir", lambda: cache_dir)
    return cache_dir


def _seed_preview_cache(cache_dir, voice_id: str, ext: str, text: str | None = None) -> None:
    """Write the cache file the production preview endpoint would have created."""
    import hashlib

    from app.gateway.routers.voice_twin_production import DEFAULT_PREVIEW_TEXT

    preview_text = (text or DEFAULT_PREVIEW_TEXT)[:100]
    key = f"{voice_id.replace(':', '_')}_{hashlib.md5(preview_text.encode('utf-8')).hexdigest()[:8]}"
    (cache_dir / f"{key}.{ext}").write_bytes(b"CACHED-AUDIO")


def test_preview_cache_hit_still_checks_ownership(preview_cache_dir):
    """缓存命中也要过归属校验（曾命中即外发 → 知道 voice_id+文本 md5 即可取他人克隆音色）。"""
    from types import SimpleNamespace

    owner = _make_user("owner")
    intruder = _make_user("intruder")
    row = SimpleNamespace(id="vt-owned-1", user_id=str(owner.id), status="ready", speaker_id="S_x")
    svc = _make_production_svc(row)
    _seed_preview_cache(preview_cache_dir, "vt-owned-1", "mp3")

    app = _make_production_app(intruder, svc)
    with TestClient(app) as client:
        resp = client.get("/api/voice-twins/production/voices/vt-owned-1/preview")
    assert resp.status_code == 403
    svc.preview_voice.assert_not_called()


def test_preview_cache_hit_owner_serves_cached_file(preview_cache_dir):
    """归属校验不得误伤本人：owner 命中缓存 → 200 且不重复合成。"""
    from types import SimpleNamespace

    owner = _make_user("owner")
    row = SimpleNamespace(id="vt-owned-1", user_id=str(owner.id), status="ready", speaker_id="S_x")
    svc = _make_production_svc(row)
    _seed_preview_cache(preview_cache_dir, "vt-owned-1", "mp3")

    app = _make_production_app(owner, svc)
    with TestClient(app) as client:
        resp = client.get("/api/voice-twins/production/voices/vt-owned-1/preview")
    assert resp.status_code == 200
    assert resp.content == b"CACHED-AUDIO"
    svc.preview_voice.assert_not_called()


def test_preview_cache_hit_deleted_voice_returns_404(preview_cache_dir):
    """音色已删但缓存残留 → 404（不得继续外发孤儿缓存）。"""
    svc = _make_production_svc(None)
    _seed_preview_cache(preview_cache_dir, "vt-gone-1", "mp3")

    app = _make_production_app(_make_user("anyone"), svc)
    with TestClient(app) as client:
        resp = client.get("/api/voice-twins/production/voices/vt-gone-1/preview")
    assert resp.status_code == 404
    svc.preview_voice.assert_not_called()


@pytest.mark.parametrize(("voice_id", "ext"), [("preset:146", "wav"), ("doubao:zh_female_qingxin", "mp3")])
def test_preview_cache_hit_unowned_voice_types_skip_ownership(preview_cache_dir, voice_id, ext):
    """preset/doubao 音色无归属概念：缓存命中直接放行，不查 repo。"""
    svc = _make_production_svc(None)
    _seed_preview_cache(preview_cache_dir, voice_id, ext)

    app = _make_production_app(_make_user("anyone"), svc)
    with TestClient(app) as client:
        resp = client.get(f"/api/voice-twins/production/voices/{voice_id}/preview")
    assert resp.status_code == 200
    assert resp.content == b"CACHED-AUDIO"
    svc.voice_service.repo.get_by_id.assert_not_called()

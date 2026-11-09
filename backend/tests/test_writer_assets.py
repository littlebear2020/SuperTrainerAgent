"""Tests for Writer assets API — AI generation, rate limiting, and upload."""

from __future__ import annotations

import io
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.content.assets_router import (
    _RATE_MAX_REQUESTS,
    _RATE_WINDOW,
    _USER_RATE,
    _check_user_rate,
    _cleanup_rate_table,
    router,
)


@pytest.fixture()
def app():
    """Create a FastAPI app with the assets router mounted."""
    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_caches():
    """Clear rate-limit table before each test."""
    _USER_RATE.clear()
    yield
    _USER_RATE.clear()


def _make_mock_vg_service(*, qihu_key="test-qihu-key"):
    """Create a mock VideoGenService on app.state."""
    mock_provider = AsyncMock()
    mock_provider.generate_image = AsyncMock(
        return_value=MagicMock(url="https://cdn.example.com/img.png", width=1024, height=1024, source="qihu"),
    )
    mock_providers = MagicMock()
    mock_providers.ai_image = mock_provider

    mock_config = MagicMock()
    mock_config.qihu_api_key = qihu_key

    mock_svc = MagicMock()
    mock_svc.config = mock_config
    mock_svc.providers = mock_providers
    return mock_svc


# ── L1: search_images route is gone (Pexels removed) ─────────────────────


def test_search_images_route_returns_404(client):
    """The Pexels image-search route has been deleted — GET returns 404."""
    resp = client.get("/api/writer/assets/images/search", params={"q": "test"})
    assert resp.status_code == 404


# ── L2: generate_image ────────────────────────────────────────────────────


def test_generate_image_success(client):
    """Mock throttled provider, verify output."""
    mock_svc = _make_mock_vg_service()
    client.app.state.video_gen_service = mock_svc

    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post(
            "/api/writer/assets/images/generate",
            json={"prompt": "A futuristic city", "size": "1024x1024"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["url"] == "https://cdn.example.com/img.png"
    assert data["source"] == "qihu"
    # Verify it called providers.ai_image (not a new instance)
    mock_svc.providers.ai_image.generate_image.assert_called_once()


def test_generate_image_uses_throttled_provider(client):
    """Verify the throttled singleton is used, not a new QihuImageProvider."""
    mock_svc = _make_mock_vg_service()
    client.app.state.video_gen_service = mock_svc

    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        client.post("/api/writer/assets/images/generate", json={"prompt": "test"})

    mock_svc.providers.ai_image.generate_image.assert_called_once()


def test_generate_image_no_api_key(client):
    """qihu_api_key empty → 503 with structured Chinese actionable detail (W19)."""
    mock_svc = _make_mock_vg_service(qihu_key="")
    client.app.state.video_gen_service = mock_svc

    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post("/api/writer/assets/images/generate", json={"prompt": "test"})

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "AI 生图" in detail["message"]
    assert "qihu_api_key" in detail["action"]
    assert detail["reason"] == "qihu_key_missing"


def test_generate_image_video_gen_unconfigured_returns_chinese_error(client):
    """video_gen_service is None → 503 with structured Chinese actionable detail (W19).

    The route reads ``request.app.state`` directly and returns a structured
    ``detail`` dict the frontend can render as message + action + retry.
    """
    # app.state.video_gen_service is not set (None by default)
    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post("/api/writer/assets/images/generate", json={"prompt": "test"})

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert "AI 生图" in detail["message"]
    assert detail["action"]
    assert detail["reason"] == "video_gen_unconfigured"
    # Must NOT be the old English message
    assert "Video gen service not available" not in str(detail)
    assert "Qihu API key not configured" not in str(detail)


def test_generate_image_user_rate_limit(client):
    """Same user exceeding 5/min → 429 on 6th request."""
    mock_svc = _make_mock_vg_service()
    client.app.state.video_gen_service = mock_svc

    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-heavy"):
        for _i in range(_RATE_MAX_REQUESTS):
            resp = client.post("/api/writer/assets/images/generate", json={"prompt": "test"})
            assert resp.status_code == 200

        # 6th request should be rate-limited
        resp = client.post("/api/writer/assets/images/generate", json={"prompt": "test"})
        assert resp.status_code == 429


def test_generate_image_correct_user_id():
    """Different users get independent rate-limit buckets."""
    _USER_RATE.clear()

    # User A uses all quota
    for _ in range(_RATE_MAX_REQUESTS):
        assert _check_user_rate("user-a") is True
    assert _check_user_rate("user-a") is False

    # User B should still be allowed
    assert _check_user_rate("user-b") is True


def test_generate_image_provider_failure(client):
    """Provider raises → 502."""
    mock_svc = _make_mock_vg_service()
    mock_svc.providers.ai_image.generate_image = AsyncMock(side_effect=RuntimeError("timeout"))
    client.app.state.video_gen_service = mock_svc

    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post("/api/writer/assets/images/generate", json={"prompt": "test"})

    assert resp.status_code == 502


# ── Rate-limit cleanup ────────────────────────────────────────────────────


def test_rate_cleanup_evicts_stale_keys():
    """_cleanup_rate_table removes users with no active timestamps."""
    # Add a stale user (all timestamps outside window)
    _USER_RATE["stale-user"] = [time.time() - _RATE_WINDOW - 1]
    # Add an active user
    _USER_RATE["active-user"] = [time.time()]

    _cleanup_rate_table()

    assert "stale-user" not in _USER_RATE
    assert "active-user" in _USER_RATE


# ── L3: upload_image (paste/drop uploads — issue ①) ───────────────────────


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # magic bytes + padding
_JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"


def _isolate_image_dir(monkeypatch, tmp_path):
    """Point writer-image storage at a per-test tmp dir (no repo litter)."""
    monkeypatch.setattr(
        "app.gateway.content.assets_router._writer_image_dir",
        lambda: tmp_path,
    )


def test_upload_image_png_returns_servable_url(client, monkeypatch, tmp_path):
    _isolate_image_dir(monkeypatch, tmp_path)
    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post(
            "/api/writer/assets/images/upload",
            files={"file": ("a.png", io.BytesIO(_PNG_BYTES), "image/png")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["url"].startswith("/api/writer/assets/images/raw/")
    assert data["source"] == "upload"
    filename = data["url"].rsplit("/", 1)[-1]
    assert filename.endswith(".png")

    served = client.get(f"/api/writer/assets/images/raw/{filename}")
    assert served.status_code == 200
    assert served.content.startswith(b"\x89PNG")


def test_upload_image_jpeg_detected_from_magic_bytes(client, monkeypatch, tmp_path):
    """Type comes from magic bytes, not the (forgeable) filename/Content-Type."""
    _isolate_image_dir(monkeypatch, tmp_path)
    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        # Mislabeled as png but bytes are JPEG → must store as .jpg
        resp = client.post(
            "/api/writer/assets/images/upload",
            files={"file": ("photo.png", io.BytesIO(_JPG_BYTES), "image/png")},
        )
    assert resp.status_code == 200
    filename = resp.json()["url"].rsplit("/", 1)[-1]
    assert filename.endswith(".jpg")


def test_upload_image_rejects_svg_and_unknown(client, monkeypatch, tmp_path):
    _isolate_image_dir(monkeypatch, tmp_path)
    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post(
            "/api/writer/assets/images/upload",
            files={"file": ("evil.svg", io.BytesIO(_SVG_BYTES), "image/svg+xml")},
        )
    assert resp.status_code == 400
    assert "Unsupported image type" in resp.json()["detail"]


def test_upload_image_rejects_too_large(client, monkeypatch, tmp_path):
    _isolate_image_dir(monkeypatch, tmp_path)
    monkeypatch.setattr("app.gateway.content.assets_router._WRITER_IMAGE_MAX_BYTES", 16)
    with patch("app.gateway.content.assets_router._get_user_id", return_value="user-1"):
        resp = client.post(
            "/api/writer/assets/images/upload",
            files={"file": ("big.png", io.BytesIO(_PNG_BYTES + b"\x00" * 100), "image/png")},
        )
    assert resp.status_code == 413


def test_serve_writer_image_missing_returns_404(client, monkeypatch, tmp_path):
    _isolate_image_dir(monkeypatch, tmp_path)
    resp = client.get("/api/writer/assets/images/raw/does-not-exist.png")
    assert resp.status_code == 404

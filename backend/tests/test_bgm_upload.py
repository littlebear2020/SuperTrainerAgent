"""Tests for custom BGM upload and list endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import router

_TEST_USER = "test-user-autouse"


def _isolate_deer_flow_home(tmp_path, monkeypatch):
    """C8 后 BGM 目录锚定 get_paths().base_dir（仓库锚定，与 CWD 无关）——
    隔离方式从 chdir 换成 DEER_FLOW_HOME + Paths 单例重置。"""
    from deerflow.config import paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)


def _make_app(tmp_path):
    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    mock_svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = mock_svc
    return app


def test_upload_bgm_success(tmp_path, monkeypatch):
    """Upload an MP3 as custom BGM."""
    # BGM storage paths are CWD-relative (.deer-flow/video-gen/user_bgm/) —
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    app = _make_app(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/bgm/upload",
        files={"file": ("mymusic.mp3", b"\xff\xfb\x90\x00fake_mp3_data", "audio/mpeg")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"].startswith("custom_")
    assert "preview_url" in data

    # Verify file was saved to user bgm dir
    user_bgm_dir = tmp_path / "video-gen/user_bgm" / _TEST_USER
    matching = list(user_bgm_dir.glob(f"{data['id']}.*"))
    assert len(matching) >= 1


def test_upload_bgm_rejects_non_audio(tmp_path, monkeypatch):
    """Non-audio file is rejected."""
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    app = _make_app(tmp_path)
    client = TestClient(app)

    resp = client.post(
        "/api/video/bgm/upload",
        files={"file": ("test.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_list_bgm_includes_custom(tmp_path, monkeypatch):
    """GET /bgm includes user-uploaded tracks."""
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    user_bgm_dir = tmp_path / "video-gen/user_bgm" / _TEST_USER
    user_bgm_dir.mkdir(parents=True, exist_ok=True)
    (user_bgm_dir / "custom_test123.mp3").write_bytes(b"fake_mp3")

    app = _make_app(tmp_path)
    client = TestClient(app)

    resp = client.get("/api/video/bgm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["built_in"] is not None
    custom_ids = [c["id"] for c in data["custom"]]
    assert "custom_test123" in custom_ids


def test_resolve_bgm_custom(tmp_path, monkeypatch):
    """_resolve_bgm resolves user-uploaded BGM by ID."""
    from app.gateway.video_gen.service import _resolve_bgm

    _isolate_deer_flow_home(tmp_path, monkeypatch)
    user_bgm_dir = tmp_path / "video-gen/user_bgm" / _TEST_USER
    user_bgm_dir.mkdir(parents=True, exist_ok=True)
    (user_bgm_dir / "custom_abc123.mp3").write_bytes(b"fake_mp3")

    result = _resolve_bgm("custom_abc123")
    assert result is not None
    assert "custom_abc123.mp3" in result


def test_resolve_bgm_returns_none_for_missing(tmp_path, monkeypatch):
    """_resolve_bgm returns None for unknown IDs."""
    from app.gateway.video_gen.service import _resolve_bgm

    _isolate_deer_flow_home(tmp_path, monkeypatch)
    assert _resolve_bgm("nonexistent_id") is None

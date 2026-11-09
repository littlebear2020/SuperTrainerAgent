"""SEC-M7 (批次 2 项 2.6): video_gen 5 个上传端点的字节级校验回归.

BGM / upload-visual / 角色三视图 / 风格锚图 / 道具——统一「大小上限 +
魔数嗅探（识别不了 = 400）」；BGM 与 upload-visual 扩展名不再取自用户
文件名。helper 直接单测 + BGM 端点过 TestClient（同 tests/test_bgm_upload.py
惯例）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import router
from app.gateway.video_gen.upload_validation import (
    UploadRejected,
    sniff_audio_ext,
    sniff_visual_ext,
    validate_audio_bytes,
    validate_image_bytes,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"0" * 32
MP3 = b"\xff\xfb\x90\x00" + b"0" * 32
WAV = b"RIFF" + b"0" * 4 + b"WAVE" + b"0" * 32
OGG = b"OggS" + b"0" * 32
FLAC = b"fLaC" + b"0" * 32
MP4 = b"0" * 4 + b"ftypisom" + b"0" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 32


def _isolate_deer_flow_home(tmp_path, monkeypatch):
    """C8 后 BGM 目录锚定 get_paths().base_dir（与 CWD 无关）——隔离用
    DEER_FLOW_HOME + Paths 单例重置（同 test_bgm_upload.py）。"""
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


# ── audio sniff ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("data,ext", [(MP3, ".mp3"), (WAV, ".wav"), (OGG, ".ogg"), (FLAC, ".flac"), (MP4, ".m4a"), (WEBM, ".webm"), (b"\xff\xf1" + b"0" * 30, ".aac")])
def test_sniff_audio_ext_families(data, ext):
    assert sniff_audio_ext(data) == ext


def test_sniff_audio_ext_rejects_text_and_images():
    assert sniff_audio_ext(b"hello world") == ""
    assert sniff_audio_ext(PNG) == ""


def test_validate_audio_bytes_size_and_empty():
    with pytest.raises(UploadRejected, match="too large"):
        validate_audio_bytes(b"\x00" * 11, max_bytes=10)
    with pytest.raises(UploadRejected, match="Empty"):
        validate_audio_bytes(b"")


# ── visual sniff ─────────────────────────────────────────────────────────


def test_sniff_visual_ext_images_and_videos():
    assert sniff_visual_ext(PNG) == ".png"
    assert sniff_visual_ext(JPG) == ".jpg"
    assert sniff_visual_ext(MP4) == ".mp4"
    assert sniff_visual_ext(WEBM) == ".webm"


def test_sniff_visual_ext_rejects_audio_and_unknown():
    assert sniff_visual_ext(MP3) == ""
    assert sniff_visual_ext(b"random bytes") == ""


def test_validate_image_bytes_rejects_svg_and_text():
    with pytest.raises(UploadRejected):
        validate_image_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    with pytest.raises(UploadRejected):
        validate_image_bytes(b"hello")


def test_validate_image_bytes_accepts_png_and_size_cap():
    assert validate_image_bytes(PNG) == ".png"
    with pytest.raises(UploadRejected, match="too large"):
        validate_image_bytes(PNG, max_bytes=8)


# ── BGM endpoint (TestClient, 同 test_bgm_upload 惯例) ───────────────────


def test_bgm_upload_oversize_rejected(tmp_path, monkeypatch):
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/api/video/bgm/upload",
        files={"file": ("big.mp3", b"\xff\xfb" + b"0" * (31 * 1024 * 1024), "audio/mpeg")},
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]


def test_bgm_upload_fake_mp3_with_audio_content_type_rejected(tmp_path, monkeypatch):
    """content_type 伪装 audio/* 但字节不是音频 → 400（魔数拒绝）。"""
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/api/video/bgm/upload",
        files={"file": ("fake.mp3", b"#!/bin/sh\nrm -rf /", "audio/mpeg")},
    )
    assert resp.status_code == 400


def test_bgm_upload_wav_gets_sniffed_extension(tmp_path, monkeypatch):
    """扩展名来自嗅探：上传 .wav 文件名但字节是 MP3 → 落盘 .mp3。"""
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    app = _make_app(tmp_path)
    client = TestClient(app)
    resp = client.post(
        "/api/video/bgm/upload",
        files={"file": ("actually-mp3.wav", MP3, "audio/wav")},
    )
    assert resp.status_code == 200
    bgm_id = resp.json()["id"]
    user_bgm_dir = tmp_path / "video-gen/user_bgm/test-user-autouse"
    matching = list(user_bgm_dir.glob(f"{bgm_id}.*"))
    assert len(matching) == 1
    assert matching[0].suffix == ".mp3"


# ── prop save (直接函数级) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_prop_upload_rejects_non_image(tmp_path):
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import _save_prop_upload

    with pytest.raises(HTTPException) as exc:
        await _save_prop_upload(b"not an image at all", tmp_path / "props" / "p1" / "main.png")
    assert exc.value.status_code == 400
    assert not (tmp_path / "props").exists()  # 未落盘


@pytest.mark.asyncio
async def test_save_prop_upload_sniffs_jpeg(tmp_path):
    from app.gateway.routers.video_gen import _save_prop_upload

    dest = await _save_prop_upload(JPG, tmp_path / "props" / "p1" / "main.png")
    assert dest.suffix == ".jpg"
    assert dest.exists()


# ── character sheet save (函数级) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_uploaded_character_sheet_sniffs_ext(tmp_path):
    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    sheet = await save_uploaded_character_sheet(
        name="主角",
        features="f",
        front_bytes=JPG,
        side_bytes=None,
        back_bytes=None,
        storage_dir=str(tmp_path),
        project_id="proj_x",
    )
    assert Path(sheet.front_url).suffix == ".jpg"

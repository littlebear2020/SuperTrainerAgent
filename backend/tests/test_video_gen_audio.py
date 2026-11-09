"""Tests for the per-frame audio streaming endpoint (plan-video-gen-post-mvp P0).

Covers the three security invariants called out in the plan:
  - owner-only access (non-owner → 404, not 403, to avoid leaking task existence),
  - path-traversal guard (audio file must resolve under the task dir),
  - happy path (Range-less full-file stream).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def audio_env(tmp_path: Path):
    """Mount the video_gen router with a mocked service owning a task under tmp_path."""
    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)

    svc = MagicMock()
    svc._storage_dir = str(tmp_path)
    task = MagicMock()
    task.id = "vg_test"
    task.user_id = "u1"
    svc.get_task = AsyncMock(return_value=task)
    app.state.video_gen_service = svc

    return TestClient(app), tmp_path


def _write_storyboard(tmp_path: Path, task_id: str, frames: list[dict]) -> Path:
    task_dir = tmp_path / "u1" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"frames": frames}), encoding="utf-8")
    return task_dir


@patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1")
def test_audio_happy_path_streams_file(_mock_uid, audio_env):
    client, tmp_path = audio_env
    task_dir = _write_storyboard(tmp_path, "vg_test", [])
    audio_file = task_dir / "audio" / "abc123.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"ID3fake-audio-bytes")
    # Rewrite storyboard pointing at the real audio file
    _write_storyboard(tmp_path, "vg_test", [{"index": 0, "audio_path": str(audio_file), "narration": "x"}])

    resp = client.get("/api/video/tasks/vg_test/audio?frame=0")
    assert resp.status_code == 200
    assert resp.content.startswith(b"ID3")


@patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1")
def test_audio_path_traversal_blocked(_mock_uid, audio_env):
    client, tmp_path = audio_env
    # audio_path resolves OUTSIDE the task dir
    evil = tmp_path / "evil.mp3"
    evil.write_bytes(b"secret")
    _write_storyboard(tmp_path, "vg_test", [{"index": 0, "audio_path": str(evil), "narration": "x"}])

    resp = client.get("/api/video/tasks/vg_test/audio?frame=0")
    assert resp.status_code == 404


@patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1")
def test_audio_rejects_non_audio_mime(_mock_uid, audio_env):
    """Non-audio extensions (even inside the task dir) must be rejected with 400,
    guarding the MIME whitelist so it can't be silently widened."""
    client, tmp_path = audio_env
    task_dir = _write_storyboard(tmp_path, "vg_test", [])
    not_audio = task_dir / "audio" / "notaudio.txt"
    not_audio.parent.mkdir(parents=True, exist_ok=True)
    not_audio.write_bytes(b"plain text, not audio")
    _write_storyboard(tmp_path, "vg_test", [{"index": 0, "audio_path": str(not_audio), "narration": "x"}])

    resp = client.get("/api/video/tasks/vg_test/audio?frame=0")
    assert resp.status_code == 400


def test_audio_non_owner_returns_404(audio_env):
    client, tmp_path = audio_env
    # get_task returns None → simulates non-owner (user filter) or missing task
    client.app.state.video_gen_service.get_task = AsyncMock(return_value=None)
    with patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="other"):
        resp = client.get("/api/video/tasks/vg_test/audio?frame=0")
    assert resp.status_code == 404


@patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1")
def test_audio_range_request_returns_206(_mock_uid, audio_env):
    client, tmp_path = audio_env
    task_dir = _write_storyboard(tmp_path, "vg_test", [])
    audio_file = task_dir / "audio" / "h.mp3"
    audio_file.parent.mkdir(parents=True, exist_ok=True)
    audio_file.write_bytes(b"0123456789abcdef")
    _write_storyboard(tmp_path, "vg_test", [{"index": 0, "audio_path": str(audio_file), "narration": "x"}])

    resp = client.get("/api/video/tasks/vg_test/audio?frame=0", headers={"range": "bytes=4-7"})
    assert resp.status_code == 206
    assert resp.headers.get("content-range") == "bytes 4-7/16"
    assert resp.headers.get("accept-ranges") == "bytes"
    assert resp.content == b"4567"


@pytest.mark.asyncio
async def test_entry1_reconciles_durations_before_payload(tmp_path, monkeypatch):
    """Entry-1（_prepare_storyboard_video）出 payload 前必须 reconcile——
    曾只有 recompose/render-from-draft 有；pad 静默失败等缺口会让记录时长≠实测直达 worker。"""
    import asyncio
    from types import SimpleNamespace

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    # 记录时长 8.55/7.2，实测 9.6/10.2——两帧偏差均 >0.3s 容差（stub 模式对齐
    # test_auto_render_early_storyboard / test_reconcile_durations 的 recompose 用例）
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=i, narration=f"句{i}", duration=d, audio_path=f"/a/{i}.mp3", speech_duration=d) for i, d in enumerate([8.55, 7.2])],
    )

    svc = VideoGenService.__new__(VideoGenService)
    svc._sb_write_lock = asyncio.Lock()  # _merge_audio_into_storyboard → _update_storyboard 需要

    async def fake_prep(*a, **k):
        return sb, str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt")

    monkeypatch.setattr(svc, "_prepare_storyboard_and_audio", fake_prep)
    monkeypatch.setattr(svc, "_has_mixed_sources", lambda s: False)

    class FakeVisual:
        async def render(self, storyboard, **kwargs):
            return None

    monkeypatch.setattr(svc, "_make_visual_source", lambda *a, **k: FakeVisual())
    monkeypatch.setattr(svc, "_make_on_frame_done", lambda *a, **k: None)
    monkeypatch.setattr(svc_mod, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)
    svc._config = SimpleNamespace(effective_frame_concurrency=lambda n: 1)

    # 音频实测偏离记录（patch probe，避免 ffprobe 依赖）
    # 2026-08-03 Task 15：reconcile 已搬到 audio_flow.py，它在自己模块内解析
    # _probe_audio_seconds → 必须 patch **源模块**。patch service 上的同名别名无效
    # （service 只是 re-export，改别名不影响 audio_flow 内部的名字解析）。
    probes = {"/a/0.mp3": 9.6, "/a/1.mp3": 10.2}
    monkeypatch.setattr("app.gateway.video_gen.audio_flow._probe_audio_seconds", lambda p: probes.get(p, 0.0))
    srt_calls = []
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: srt_calls.append(1))

    async def _async_none():
        return None

    # probe/decide 在 _prepare_storyboard_video 函数体内 from .worker_probe import ...
    # → patch service 模块属性无效，必须 patch 源模块 worker_probe
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", _async_none)
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", lambda sb, caps: "v1")

    async def progress(**kwargs):
        return None

    result = await svc._prepare_storyboard_video("ai_image", {"mode": "fixed", "script": "n"}, tmp_path, progress)

    assert result.payload["frames"][0]["duration"] == 9.6, "Entry-1 出 payload 前未按实测音频纠偏帧时长"
    assert result.payload["frames"][1]["duration"] == 10.2
    assert srt_calls, "时长被修正后必须重建 SRT（否则字幕仍按旧轴）"
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["duration"] == 9.6, "修正结果未落盘 storyboard.json"

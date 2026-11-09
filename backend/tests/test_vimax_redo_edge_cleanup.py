"""T5（2026-10-16 收口批）：redo 毛边清理——参考档位推导 / 缺失镜 4xx / 端点错误映射 / 死代码拆除。

- ``reference_mode`` 按实际降级档位推导（修前按参考数：单参考 tier-0 成功被
  误标 ``first_frame_only``——没发生降级却挂着降级标签，误导用户复核）。
- manifest 对 video.mp4 缺失镜记 ``video_missing`` 标记；退化态 redo 其他镜 →
  明确 422（修前重拼踩缺失文件裸 500）；redo 缺失镜自身 → 放行（自愈路径）。
- redo 端点非 404 异常映射为可读错误（修前裸 500 无 message）。
- 旧 ``redo_vimax_shot``/``RedoShot``/``RedoResult`` 死代码删除（router 早已改走
  service 新通路，旧测试只守死路径——绿灯给假信心）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.gateway.video_gen.engines.vimax.engine as ve
from app.gateway.video_gen.engines.vimax.engine import build_vimax_shots_manifest


def _seed_shot(working_dir: Path, scene: Path, idx: int) -> None:
    shot_dir = scene / "shots" / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "video.mp4").write_bytes(f"OLD{idx}".encode())
    (shot_dir / "first_frame.png").write_bytes(f"PNG{idx}".encode())


def _seed_task_disk(tmp_path: Path, shots: int = 3, task_id: str = "vg_redo", user: str = "test-user-autouse") -> tuple[Path, Path]:
    task_dir = tmp_path / user / "tasks" / task_id
    working_dir = task_dir / "vimax_working"
    for i in range(shots):
        _seed_shot(working_dir, working_dir, i)
    (working_dir / "storyboard.json").write_text(json.dumps([{"idx": i, "visual_desc": f"v{i}"} for i in range(shots)]), encoding="utf-8")
    (task_dir / "vimax_narration.mp3").write_bytes(b"AUDIO")
    (task_dir / "subtitle.srt").write_bytes(b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    return task_dir, working_dir


async def _fake_manifest(working_dir: Path, task_dir: Path) -> dict:
    return await build_vimax_shots_manifest(working_dir, task_dir)


def _make_service(tmp_path, task_result: dict, payload: dict | None = None):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    repo = MagicMock()
    task = MagicMock()
    task.task_type = "vimax"
    task.status = "ready"
    task.payload = json.dumps(payload or {"aspect": "9:16"}, ensure_ascii=False)
    task.result = json.dumps(task_result, ensure_ascii=False)
    repo.get = AsyncMock(return_value=task)
    repo.update = AsyncMock(return_value=None)
    svc = VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    return svc, repo, task


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


class _FakeVideoRef:
    def __init__(self, url: str):
        self.url = url


# ── reference_mode 按实际降级档位推导 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_reference_mode_reflects_actual_request_shape():
    """tier-0 成功 = 按可用全量参考（无降级）：单参考 → full（非 first_frame_only）、
    零参考 → prompt_only。first_frame_only 只在真实降级到首帧档时出现。"""

    async def ok_generate(prompt, **kwargs):
        return _FakeVideoRef("http://cdn/new.mp4")

    provider = MagicMock()
    provider.generate_video = ok_generate

    vref, mode, note = await ve._redo_generate_with_downgrade(provider, "p", references=["/abs/only_neighbor_frame.png"], duration=5.0, ratio=None)
    assert mode == ve._REDO_MODE_FULL, "单参考 tier-0 成功没有降级发生，不得标 first_frame_only"
    assert note == ""

    vref, mode, note = await ve._redo_generate_with_downgrade(provider, "p", references=[], duration=5.0, ratio=None)
    assert mode == ve._REDO_MODE_PROMPT
    assert note == ""


# ── manifest 缺失镜标记 + 退化态 redo 语义 ─────────────────────────────────


@pytest.mark.asyncio
async def test_manifest_marks_missing_video_shot(tmp_path):
    """video.mp4 缺失的镜：manifest 记 video_missing 标记（不静默当正常镜）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    (working_dir / "shots" / "2" / "video.mp4").unlink()

    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    assert manifest["2"].get("video_missing") is True, f"缺失镜须记标记，实际 {manifest['2']}"
    assert manifest["0"].get("video_missing") is not True
    assert manifest["1"].get("video_missing") is not True


@pytest.mark.asyncio
async def test_redo_refuses_when_other_shot_video_missing(tmp_path, monkeypatch):
    """退化态（他镜视频缺失）redo 正常镜 → VimaxShotVideoMissing（端点 422），
    不进重拼裸 500。"""

    async def failing_generate(prompt, **kwargs):
        raise AssertionError("provider 不应被调——重拼前置校验应先拒绝")

    task_dir, working_dir = _seed_task_disk(tmp_path)
    (working_dir / "shots" / "2" / "video.mp4").unlink()
    manifest = await _fake_manifest(working_dir, task_dir)

    svc, _repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=failing_generate))

    with pytest.raises(ve.VimaxShotVideoMissing, match="缺失"):
        await svc.redo_vimax_shot(task_id="vg_redo", user_id="test-user-autouse", shot_id=0)


@pytest.mark.asyncio
async def test_redo_missing_shot_itself_is_self_healing(tmp_path, monkeypatch):
    """redo 目标镜自身视频缺失 → 放行（新视频落位后重拼自愈）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    (working_dir / "shots" / "2" / "video.mp4").unlink()
    manifest = await _fake_manifest(working_dir, task_dir)

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        return _FakeVideoRef("http://cdn/new_shot_2.mp4")

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"HEALED"

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    resp = await svc.redo_vimax_shot(task_id="vg_redo", user_id="test-user-autouse", shot_id=2)
    assert resp["regenerated_shot_id"] == 2
    assert (working_dir / "shots" / "2" / "video.mp4").read_bytes() == b"HEALED"


# ── 端点错误映射 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redo_endpoint_maps_errors_to_readable_responses(tmp_path, monkeypatch):
    """VimaxShotVideoMissing → 422；通用异常 → 500 带可读 message（不裸 500）。"""
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import redo_vimax_shot_endpoint

    task_dir, working_dir = _seed_task_disk(tmp_path)
    (working_dir / "shots" / "2" / "video.mp4").unlink()
    manifest = await _fake_manifest(working_dir, task_dir)
    svc, _repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=AsyncMock(side_effect=AssertionError("not reached"))))

    with pytest.raises(HTTPException) as exc_info:
        await redo_vimax_shot_endpoint(_make_request(svc), "vg_redo", 0)
    assert exc_info.value.status_code == 422
    assert "缺失" in exc_info.value.detail

    # 通用异常：provider 下载失败 → 500 带可读 message
    task_dir2, working_dir2 = _seed_task_disk(tmp_path, task_id="vg_redo2")

    async def boom(prompt, **kwargs):
        raise RuntimeError("upstream 529")

    manifest2 = await _fake_manifest(working_dir2, task_dir2)
    svc2, _repo2, _task2 = _make_service(tmp_path, task_result={"vimax_shots": manifest2}, payload={"aspect": "9:16"})
    monkeypatch.setattr(svc2._providers, "ai_video", MagicMock(generate_video=boom))
    with pytest.raises(HTTPException) as exc_info2:
        await redo_vimax_shot_endpoint(_make_request(svc2), "vg_redo2", 1)
    assert exc_info2.value.status_code == 500
    assert "重做失败" in str(exc_info2.value.detail), f"detail 须可读，实际 {exc_info2.value.detail!r}"
    assert "529" in str(exc_info2.value.detail), "原因摘要须进 detail"


# ── 死代码拆除守卫 ──────────────────────────────────────────────────────────


def test_old_redo_dead_code_removed():
    """旧 redo_vimax_shot/RedoShot/RedoResult 已删除——新通路守卫在
    test_vimax_redo_pipeline.py / test_vimax_redo_drift_and_mutex.py。"""
    assert not hasattr(ve, "redo_vimax_shot"), "旧死函数应已删除"
    assert not hasattr(ve, "RedoShot")
    assert not hasattr(ve, "RedoResult")

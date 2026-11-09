"""Phase B1: stall_timeout_for_task 优先读 storyboard.json 实际帧数缩放。"""

import json

import pytest


def _svc(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._config = VideoGenConfig()
    svc.stall_timeout = 1800
    return svc


def _task(task_id="vg_x", user_id="u", payload="{}"):
    class _T:
        pass

    t = _T()
    t.id = task_id
    t.user_id = user_id
    t.payload = payload
    return t


def _write_frames(tmp_path, n, task_id="vg_x", user_id="u"):
    task_dir = tmp_path / user_id / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {"title": "t", "config": {}, "frames": [{"index": i} for i in range(n)]}
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")


@pytest.mark.asyncio
async def test_reads_real_frame_count_272(tmp_path):
    """272 帧 storyboard → 1800 + 60*272 = 18120s。"""
    _write_frames(tmp_path, 272)
    svc = _svc(tmp_path)
    assert svc.stall_timeout_for_task(_task()) == 1800 + 60 * 272


@pytest.mark.asyncio
async def test_falls_back_to_payload_when_no_storyboard(tmp_path):
    """无 storyboard.json → 回落 payload 推导（n_scenes=10 → 1800+600=2400）。"""
    svc = _svc(tmp_path)
    payload = json.dumps({"n_scenes": 10})
    assert svc.stall_timeout_for_task(_task(payload=payload)) == 1800 + 60 * 10


@pytest.mark.asyncio
async def test_falls_back_to_default_when_no_signal(tmp_path):
    """无 storyboard + payload 无字段 → 默认 stall_timeout=1800。"""
    svc = _svc(tmp_path)
    assert svc.stall_timeout_for_task(_task(payload="{}")) == 1800

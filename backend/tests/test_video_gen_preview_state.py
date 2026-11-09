"""preview_state 状态机（2026-09-07 方案 A）：单帧预览 running→done/error 写入时序。

修前缺陷：重新生成已有预览图的帧，前端用「visual_url 存在」判结案 → 点击瞬间误结案
（spinner 秒停+假成功 toast+停轮询），后端新图永远拉不回（docs/plans/2026-09-07-preview-regen-stale-image-plan.md）。
本文件锁后端三写入点：_preview_frame_bg 开始写 running；_generate_frame_preview 成功
分支写 done（与 visual_url 同批落盘）；_write_preview_error 写 error（旧 visual_url 保留）。

harness 参考 test_preview_async_endpoint.py（__new__ + 手设 _sb_write_lock/_storage_dir）。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest


def _write_storyboard(tmp_path, task_id, frames=None):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": frames if frames is not None else [{"index": 0, "narration": "n0", "image_prompt": "p0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


def _read_frame(task_dir, index=0):
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    return next(f for f in data["frames"] if f["index"] == index)


@pytest.fixture(autouse=True)
def _clean_preview_inflight():
    from app.gateway.routers import video_gen as vg

    vg._preview_inflight.clear()
    yield
    vg._preview_inflight.clear()


@pytest.mark.asyncio
async def test_preview_bg_success_running_then_done(tmp_path, monkeypatch):
    """成功全流程：生成期间 storyboard.json 可见 running；终值 done+visual_url 同批、无 preview_error。"""
    from app.gateway.routers import video_gen as vg
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.visual_sources import ai_image as ai_image_mod

    task_dir = _write_storyboard(tmp_path, "vg_done")

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()
    svc._config = SimpleNamespace(ai_image_provider="seedream")

    class _Repo:
        async def get(self, task_id, user_id=None):
            return SimpleNamespace(payload="{}")

    svc.repo = _Repo()

    class _Img:
        async def generate_image(self, prompt, **kwargs):
            return SimpleNamespace(url="http://x/new.png")

    svc._providers = SimpleNamespace(ai_image=_Img(), make_ai_image=lambda name: _Img())
    monkeypatch.setattr(svc_mod, "_inject_project_character_context", lambda *a, **k: None)
    monkeypatch.setattr(svc_mod, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)

    async def _no_localize(url, frame, task_dir):
        return None

    monkeypatch.setattr(ai_image_mod, "_localize_image", _no_localize)

    # 生成期间（_generate_frame_preview 已开始）快照 storyboard：应见 running
    real_gen = svc._generate_frame_preview
    observed = {}

    async def _spy(task_id, frame_index, user_id):
        observed["during"] = _read_frame(task_dir)
        return await real_gen(task_id, frame_index, user_id)

    svc._generate_frame_preview = _spy
    await vg._preview_frame_bg(svc, "vg_done", 0, "test-user-autouse")

    assert observed["during"].get("preview_state") == "running", "生成期间在途标记必须对前端轮询可见"
    final = _read_frame(task_dir)
    assert final["preview_state"] == "done"
    assert final["visual_url"] == "http://x/new.png"
    assert "preview_error" not in final
    assert ("vg_done", 0) not in vg._preview_inflight


@pytest.mark.asyncio
async def test_preview_bg_failure_writes_error_and_keeps_old_visual(tmp_path):
    """失败路径：preview_state=error + preview_error 非空；旧 visual_url 保留（UX 约定：失败仍显旧图）。"""
    from app.gateway.routers import video_gen as vg
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(
        tmp_path,
        "vg_err",
        frames=[{"index": 0, "narration": "n0", "image_prompt": "p0", "visual_url": "http://x/old.png"}],
    )

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()

    async def _boom(task_id, frame_index, user_id):
        raise RuntimeError("ark boom")

    svc._generate_frame_preview = _boom
    await vg._preview_frame_bg(svc, "vg_err", 0, "test-user-autouse")

    f0 = _read_frame(task_dir)
    assert f0["preview_state"] == "error"
    assert "RuntimeError" in f0["preview_error"]
    assert f0["visual_url"] == "http://x/old.png"


@pytest.mark.asyncio
async def test_set_preview_state_running_clears_stale_error(tmp_path):
    """running 单测：写入 running 且清掉旧 preview_error（新一次尝试开始，旧错误失效）。"""
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(
        tmp_path,
        "vg_run",
        frames=[{"index": 0, "narration": "n0", "preview_error": "上次失败"}],
    )

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()

    await svc._set_preview_state("vg_run", 0, "test-user-autouse", "running")

    f0 = _read_frame(task_dir)
    assert f0["preview_state"] == "running"
    assert "preview_error" not in f0


@pytest.mark.asyncio
async def test_set_preview_state_error_truncates_message(tmp_path):
    """error 单测：附截断 200 字消息（对齐 _set_regen_state 语义）。"""
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(tmp_path, "vg_err2")

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()

    await svc._set_preview_state("vg_err2", 0, "test-user-autouse", "error", error="x" * 300)

    f0 = _read_frame(task_dir)
    assert f0["preview_state"] == "error"
    assert len(f0["preview_error"]) == 200


# ── 条目 35（2026-09-12 缺陷批）：running 态崩溃滞留 10min 失效 ─────────────


def _write_storyboard_running(tmp_path, task_id, minutes_ago, extra_frame=None):
    from datetime import UTC, datetime, timedelta

    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    at = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    frame = {
        "index": 0,
        "narration": "n0",
        "image_prompt": "p0",
        "preview_state": "running",
        "preview_state_at": at,
        "regen_state": "running",
        "regen_state_at": at,
    }
    frame.update(extra_frame or {})
    (task_dir / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {"video_type": "ai_image", "width": 1080, "height": 1920}, "frames": [frame]}),
        encoding="utf-8",
    )
    return task_dir


@pytest.mark.asyncio
async def test_set_preview_state_writes_timestamp(tmp_path):
    """条目 35：_set_preview_state 同时写 preview_state_at（失效判定依据）。"""
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(tmp_path, "vg_at")
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()
    await svc._set_preview_state("vg_at", 0, "test-user-autouse", "running")
    f0 = _read_frame(task_dir)
    assert f0["preview_state"] == "running"
    from datetime import datetime

    datetime.fromisoformat(f0["preview_state_at"])  # ISO 可解析


def test_fold_stale_running_states_20min_error_5min_running():
    """GET 折算：>10min running → error（只改响应 dict）；5min → 保持 running；
    无时间戳的旧数据不触发（向后兼容）。"""
    from datetime import UTC, datetime, timedelta

    from app.gateway.video_gen.service import STALE_PREVIEW_RESET_MESSAGE, fold_stale_running_states

    at20 = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    at5 = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    data = {
        "frames": [
            {"index": 0, "preview_state": "running", "preview_state_at": at20, "regen_state": "running", "regen_state_at": at20},
            {"index": 1, "preview_state": "running", "preview_state_at": at5, "regen_state": "running", "regen_state_at": at5},
            {"index": 2, "preview_state": "running"},  # 旧数据无 _at → 不触发
        ]
    }
    fold_stale_running_states(data)
    f20, f5, f_legacy = data["frames"]
    assert f20["preview_state"] == "error"
    assert f20["preview_error"] == STALE_PREVIEW_RESET_MESSAGE
    assert f20["regen_state"] == "error"
    assert "重新生成中断" in f20["regen_error"]
    assert f5["preview_state"] == "running" and f5["regen_state"] == "running"
    assert f_legacy["preview_state"] == "running"


@pytest.mark.asyncio
async def test_expire_stale_running_state_resets_on_post_entry(tmp_path):
    """POST 入口落盘重置：20min running → 磁盘真的变 error+可重试文案；5min 不动。"""
    from app.gateway.video_gen.service import STALE_PREVIEW_RESET_MESSAGE, VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()

    task_dir = _write_storyboard_running(tmp_path, "vg_stale", 20)
    await svc._expire_stale_running_state("vg_stale", 0, "test-user-autouse", state_key="preview_state", error_key="preview_error", message=STALE_PREVIEW_RESET_MESSAGE)
    f0 = _read_frame(task_dir)
    assert f0["preview_state"] == "error"
    assert f0["preview_error"] == "预览中断，请重试"

    task_dir2 = _write_storyboard_running(tmp_path, "vg_fresh", 5)
    await svc._expire_stale_running_state("vg_fresh", 0, "test-user-autouse", state_key="preview_state", error_key="preview_error", message=STALE_PREVIEW_RESET_MESSAGE)
    assert _read_frame(task_dir2)["preview_state"] == "running"


def test_get_storyboard_folds_stale_running_without_disk_write(tmp_path):
    """GET storyboard 集成：>10min running 读出折算为 error；磁盘保持 running（不落盘）。"""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers.video_gen import router

    task_dir = _write_storyboard_running(tmp_path, "vg_fold", 20)
    app = FastAPI()
    app.include_router(router)
    svc = MagicMock()
    svc.get_task = AsyncMock(return_value=SimpleNamespace(status="ready", payload="{}", progress=100))
    svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = svc
    client = TestClient(app)

    resp = client.get("/api/video/tasks/vg_fold/storyboard")
    assert resp.status_code == 200, resp.text
    f0 = resp.json()["frames"][0]
    assert f0["preview_state"] == "error"
    assert f0["preview_error"] == "预览中断，请重试"
    # 不落盘：磁盘仍是 running（写入口才落盘）
    assert _read_frame(task_dir)["preview_state"] == "running"

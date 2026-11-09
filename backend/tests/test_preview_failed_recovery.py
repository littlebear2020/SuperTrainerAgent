"""Phase A: failed 任务的恢复入口——preview / preview-all / render 接受 failed。"""

import json

import pytest


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


class _Task:
    def __init__(self, status):
        self.status = status


def _write_storyboard(tmp_path, task_id="vg_x", n_frames=1):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": i, "narration": f"n{i}"} for i in range(n_frames)],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


@pytest.mark.asyncio
async def test_preview_all_accepts_failed(tmp_path, monkeypatch):
    """A1: failed 任务也能 preview-all（不再 409），磁盘 storyboard 保留时继续生预览。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_fail")

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task("failed")

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    # 阻止真起后台任务（只验证守卫放开）
    async def _no_bg(*a, **k):
        return None

    monkeypatch.setattr(vg, "_preview_all_bg", _no_bg)

    result = await vg.preview_all_frames(_make_request(_Svc()), "vg_fail")
    assert result["status"] == "started"


@pytest.mark.asyncio
async def test_preview_frame_accepts_failed(tmp_path, monkeypatch):
    """A1: failed 任务单帧 preview 不再 409；异步化后返回 started。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_fail2")
    # 异步端点在调度前同步校验 storyboard/帧/image_prompt → 测试分镜补 image_prompt
    sb_path = tmp_path / "test-user-autouse" / "tasks" / "vg_fail2" / "storyboard.json"
    data = json.loads(sb_path.read_text(encoding="utf-8"))
    data["frames"][0]["image_prompt"] = "p0"
    sb_path.write_text(json.dumps(data), encoding="utf-8")

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task("failed")

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    async def _no_bg(*a):
        return None

    monkeypatch.setattr(vg, "_preview_frame_bg", _no_bg)
    vg._preview_inflight.clear()
    result = await vg.preview_frame_visual(_make_request(_Svc()), "vg_fail2", 0)
    assert result == {"status": "started"}
    vg._preview_inflight.clear()


@pytest.mark.asyncio
async def test_preview_all_still_rejects_composing(tmp_path):
    """守卫语义不变：composing/running 仍 409（渲染中不可重入）。"""
    import pytest as _pytest
    from fastapi import HTTPException

    from app.gateway.routers import video_gen as vg

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task("composing")

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    with _pytest.raises(HTTPException) as exc:
        await vg.preview_all_frames(_make_request(_Svc()), "vg_c")
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_resume_draft_accepts_failed_with_storyboard(tmp_path, monkeypatch):
    """A2: failed + storyboard.json 存在 → resume_draft 注册续跑成功。"""
    import json as _json

    _write_storyboard(tmp_path, "vg_res")

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)

    class _Row:
        id = "vg_res"
        status = "failed"
        task_type = "ai_image"
        payload = _json.dumps({"mode": "fixed"})

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Row()

        async def update(self, task_id, **kw):
            # Task 6: resume_draft 摘除 recompose_only 后 repo.update 持久化（真实 repo 有此方法）
            pass

    svc.repo = _Repo()

    registered = {"ok": False}

    def _factory_reg(_task_id, _factory):
        registered["ok"] = True
        return True

    svc._register_active_task = _factory_reg  # type: ignore[method-assign]

    # 不应 raise
    await svc.resume_draft("vg_res", "test-user-autouse", frame_edits={}, quality_override=None)
    assert registered["ok"] is True


@pytest.mark.asyncio
async def test_resume_draft_rejects_failed_without_storyboard(tmp_path):
    """A2: failed 但 storyboard.json 缺失 → ValueError（提示删除重建）。"""
    import json as _json

    import pytest as _pytest

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)

    # 故意不写 storyboard.json
    (tmp_path / "test-user-autouse" / "tasks" / "vg_nosb").mkdir(parents=True)

    class _Row:
        id = "vg_nosb"
        status = "failed"
        task_type = "ai_image"
        payload = _json.dumps({"mode": "fixed"})

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Row()

    svc.repo = _Repo()

    with _pytest.raises(ValueError, match="storyboard"):
        await svc.resume_draft("vg_nosb", "test-user-autouse", frame_edits={}, quality_override=None)

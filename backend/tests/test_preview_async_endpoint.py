"""单帧预览异步化：202 立即返回 + 后台生成 + 失败写 preview_error + 在途去重。"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _write_storyboard(tmp_path, task_id="vg_a", frames=None):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": frames if frames is not None else [{"index": 0, "narration": "n0", "image_prompt": "p0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


@pytest.fixture(autouse=True)
def _clean_preview_state():
    from app.gateway.routers import video_gen as vg

    vg._preview_inflight.clear()
    vg._preview_all_running.clear()
    yield
    vg._preview_inflight.clear()
    vg._preview_all_running.clear()


class _Task:
    def __init__(self, status="draft"):
        self.status = status


@pytest.mark.asyncio
async def test_preview_returns_started_immediately(tmp_path, monkeypatch):
    """合法请求 → {"status": "started"}，后台函数被调度（测试里替换为 noop）。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_ok")

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    spawned = {"n": 0}

    async def _noop_bg(*a):
        spawned["n"] += 1

    monkeypatch.setattr(vg, "_preview_frame_bg", _noop_bg)
    result = await vg.preview_frame_visual(_make_request(_Svc()), "vg_ok", 0)
    assert result == {"status": "started"}
    await asyncio.sleep(0)  # 让调度的后台任务跑完
    assert spawned["n"] == 1


@pytest.mark.asyncio
async def test_preview_validation_still_synchronous_400(tmp_path):
    """无 image_prompt → 仍同步 400（不起后台任务）。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_noprompt", frames=[{"index": 0, "narration": "n0"}])

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    with pytest.raises(HTTPException) as exc:
        await vg.preview_frame_visual(_make_request(_Svc()), "vg_noprompt", 0)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_preview_inflight_dedup(tmp_path, monkeypatch):
    """同帧已在途 → already_running，不重复生成。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_dup")

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

        async def _expire_stale_running_state(self, *a, **kw):  # 条目 35：POST 入口失效重置（no-op）
            return None

    gate = asyncio.Event()

    async def _slow_bg(*a):
        await gate.wait()

    monkeypatch.setattr(vg, "_preview_frame_bg", _slow_bg)
    req = _make_request(_Svc())
    r1 = await vg.preview_frame_visual(req, "vg_dup", 0)
    r2 = await vg.preview_frame_visual(req, "vg_dup", 0)
    assert r1 == {"status": "started"}
    assert r2 == {"status": "already_running"}
    gate.set()


@pytest.mark.asyncio
async def test_preview_bg_failure_writes_preview_error(tmp_path):
    """后台生成异常 → storyboard.json 对应帧写入 preview_error；在途登记被清理。"""
    from app.gateway.routers import video_gen as vg
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(tmp_path, "vg_err")

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._sb_write_lock = asyncio.Lock()

    async def _boom(task_id, frame_index, user_id):
        raise RuntimeError("ark boom")

    svc._generate_frame_preview = _boom
    await vg._preview_frame_bg(svc, "vg_err", 0, "test-user-autouse")

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "RuntimeError" in data["frames"][0]["preview_error"]
    assert ("vg_err", 0) not in vg._preview_inflight


@pytest.mark.asyncio
async def test_generate_frame_preview_success_clears_preview_error(tmp_path, monkeypatch):
    """重生成功 → visual_url 写入且旧 preview_error 被清除。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.service import VideoGenService

    task_dir = _write_storyboard(
        tmp_path,
        "vg_clr",
        frames=[{"index": 0, "narration": "n0", "image_prompt": "p0", "preview_error": "old"}],
    )

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
            return SimpleNamespace(url="http://x/p.png")

    # E7：预览图经 make_ai_image(payload.image_provider) 与正式渲染同源——
    # None（未选择）→ 默认实例。
    _img = _Img()
    svc._providers = SimpleNamespace(ai_image=_img, make_ai_image=lambda name: _img)
    monkeypatch.setattr(svc_mod, "_inject_project_character_context", lambda *a, **k: None)
    monkeypatch.setattr(svc_mod, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)

    url = await svc._generate_frame_preview("vg_clr", 0, "test-user-autouse")
    assert url == "http://x/p.png"
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["visual_url"] == "http://x/p.png"
    assert "preview_error" not in data["frames"][0]

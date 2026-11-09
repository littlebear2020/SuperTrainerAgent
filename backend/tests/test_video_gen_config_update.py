"""storyboard config 生成后可调端点（2026-07-27）：voice/speed/bgm_volume/subtitle_style。

TDD 目标被测函数 ``vg.update_storyboard_config`` 是新建端点（RED 阶段不存在）。
harness 模式沿用 tests/test_preview_async_endpoint.py（直接调用 + _make_request/_Svc）。
"""

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


def _write_storyboard(tmp_path, task_id="vg_cfg", config_extra=None):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "video_type": "ai_image",
        "width": 1080,
        "height": 1920,
        "voice_id": "preset:146",
        "tts_speed": 1.2,
        "bgm_volume": 0.15,
    }
    cfg.update(config_extra or {})
    sb = {
        "title": "t",
        "config": cfg,
        "frames": [{"index": 0, "narration": "n0", "image_prompt": "p0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


class _Svc:
    def __init__(self, tmp_path, status="ready"):
        self._storage_dir = str(tmp_path)
        self._status = status
        self._sb_write_lock = asyncio.Lock()
        # B3（2026-09-28）：PUT config 双写 DB payload——端点契约新增 repo.update
        from unittest.mock import AsyncMock

        self.repo = SimpleNamespace(update=AsyncMock())

    async def get_task(self, task_id, user_id):
        from types import SimpleNamespace

        return SimpleNamespace(status=self._status, task_type="ai_image", payload="{}")

    async def _update_storyboard(self, task_dir, updater):
        # 委托真实实现（读-改-原子写）；_sb_write_lock 由 __init__ 提供
        from app.gateway.video_gen.service import VideoGenService

        return await VideoGenService._update_storyboard(self, task_dir, updater)


@pytest.mark.asyncio
async def test_update_config_persists_allowed_fields(tmp_path):
    from app.gateway.routers import video_gen as vg

    task_dir = _write_storyboard(tmp_path)
    body = vg.UpdateStoryboardConfigRequest(voice_id="preset:200", tts_speed=1.5, bgm_volume=0.3)
    result = await vg.update_storyboard_config(_make_request(_Svc(tmp_path)), "vg_cfg", body)
    assert result["config"]["voice_id"] == "preset:200"
    assert result["config"]["tts_speed"] == 1.5
    assert result["config"]["bgm_volume"] == 0.3
    on_disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert on_disk["config"]["voice_id"] == "preset:200"


def test_update_config_validates_ranges():
    from app.gateway.routers import video_gen as vg

    with pytest.raises(Exception):  # pydantic ValidationError
        vg.UpdateStoryboardConfigRequest(tts_speed=3.0)
    with pytest.raises(Exception):
        vg.UpdateStoryboardConfigRequest(bgm_volume=-0.1)
    with pytest.raises(Exception):  # extra="forbid"
        vg.UpdateStoryboardConfigRequest(width=1)


@pytest.mark.asyncio
async def test_update_config_rejects_composing_task(tmp_path):
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path)
    body = vg.UpdateStoryboardConfigRequest(bgm_volume=0.5)
    with pytest.raises(HTTPException) as exc:
        await vg.update_storyboard_config(_make_request(_Svc(tmp_path, status="composing")), "vg_cfg", body)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_update_config_rejects_empty_body(tmp_path):
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path)
    with pytest.raises(HTTPException) as exc:
        await vg.update_storyboard_config(_make_request(_Svc(tmp_path)), "vg_cfg", vg.UpdateStoryboardConfigRequest())
    assert exc.value.status_code == 400

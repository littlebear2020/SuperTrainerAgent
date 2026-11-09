"""P0-1（2026-08-08）：/api/tasks/{id} 读出侧必须透出 subtitle_status。

修前 tasks.py 从不含该名的 subtitle_filter 模块导入 _extract_subtitle_status
（唯一定义在 video_gen/service.py），ImportError 被裸 except 吞掉 →
subtitle_status 永久缺失，PPT/voice_twin 字幕烧录失败对用户不可见。
"""

from __future__ import annotations

import json
import types

import pytest

from app.gateway.routers.tasks import get_task


class _FakeRepo:
    def __init__(self, task):
        self._task = task

    async def get(self, task_id, user_id=None):
        return self._task


def _fake_request(repo, storage_dir: str):
    svc = types.SimpleNamespace(_storage_dir=storage_dir)
    state = types.SimpleNamespace(task_repository=repo, production_service=svc)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


@pytest.mark.asyncio
async def test_get_task_surfaces_subtitle_status(tmp_path):
    task_dir = tmp_path / "default" / "tasks" / "vp_sub1"
    task_dir.mkdir(parents=True)
    (task_dir / "subtitle_status.json").write_text(
        json.dumps({"status": "failed", "error": "字幕烧录失败：libass error"}),
        encoding="utf-8",
    )
    task = types.SimpleNamespace(
        id="vp_sub1",
        user_id="default",
        domain="voice_production",
        task_type="ppt_to_video",
        status="ready",
        progress=100,
        result="{}",
        payload="{}",
    )
    req = _fake_request(_FakeRepo(task), str(tmp_path))

    detail = await get_task(req, "vp_sub1")

    sub = detail.result.get("subtitle_status")
    assert sub is not None, "subtitle_status 必须出现在 result 中（修前导入失败被静默吞掉）"
    assert sub["status"] == "failed"
    assert "字幕烧录失败" in (sub.get("error") or "")


@pytest.mark.asyncio
async def test_get_task_subtitle_status_absent_file_is_skipped(tmp_path):
    """无 sidecar 时回 skipped（不崩、不 500）。"""
    task_dir = tmp_path / "default" / "tasks" / "vp_sub2"
    task_dir.mkdir(parents=True)
    task = types.SimpleNamespace(
        id="vp_sub2",
        user_id="default",
        domain="voice_production",
        task_type="ppt_to_video",
        status="ready",
        progress=100,
        result="{}",
        payload="{}",
    )
    req = _fake_request(_FakeRepo(task), str(tmp_path))

    detail = await get_task(req, "vp_sub2")

    assert detail.result["subtitle_status"]["status"] == "skipped"

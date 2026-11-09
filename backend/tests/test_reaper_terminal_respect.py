"""Task 4: (a) reaper 标 failed 后 CancelledError 不再把任务排回 pending（三态横跳）；
(b) 帧编辑 mutation 刷新 updated_at（活跃草稿不再被 stall 误杀）。

P1-7: PUT frame-edit / 分镜增删 / reorder 等 mutation 只写磁盘 storyboard.json，
从不调 repo → task.updated_at 不刷新 → stall watchdog 把"正在积极编辑的草稿"
误判 stalled（updated_at > stall_timeout）→ reaper 误杀。修法：VideoGenService
新增 touch_task()（repo.update 空 kwargs 仅刷 updated_at，repository.py:67-71），
所有只写磁盘的 mutation 端点成功路径末尾调用。

P1-8: reaper 先把任务标 failed（终态），随后 cancel 传播到 _wrap_pipeline 的
CancelledError 分支；修前该分支不查当前状态，retry_count < max_retries 就把任务
排回 pending → failed/pending/running 三态横跳，retry 耗尽后误标
"Cancelled by user" 覆盖 reaper 的真实失败原因。修法：重排前先 repo.get，
status ∈ TERMINAL_STATUSES（schemas.py:32 frozenset({ready, failed})）则尊重终态，
直接 re-raise 不重排、不覆写。
"""

from __future__ import annotations

import asyncio
import types

import pytest


@pytest.mark.asyncio
async def test_cancelled_error_respects_terminal_status(tmp_path):
    from app.gateway._shared.task_framework import service as fw

    updates: list[dict] = []

    class FakeRepo:
        """Reaper 已把任务标 failed（终态）；记录一切 update 写入。"""

        async def get(self, task_id, **kw):
            return types.SimpleNamespace(id=task_id, status="failed", retry_count=0)

        async def update(self, task_id, **kwargs):
            updates.append(kwargs)

    class _CancelledService(fw.BaseTaskService):
        """最小具体子类（参照 test_task_framework._FakeService 桩法）——pipeline 即抛 cancel。"""

        domain = "video_gen"
        id_prefix = "vg"
        max_retries = 2

        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            raise asyncio.CancelledError()

    svc = _CancelledService(FakeRepo(), storage_dir=str(tmp_path))
    with pytest.raises(asyncio.CancelledError):
        # 签名以 task_framework/service.py 现状为准：(task_id, user_id, task_type, payload)；
        # progress 由内部 ProgressUpdater 构造，其写入经 FakeRepo.update 汇入 updates。
        await svc._wrap_pipeline("t1", "default", "ai_video", {})
    # 修前：status="pending" 被重排（failed/pending/running 三态横跳起点）；
    # 修后：终态被尊重，无任何 pending 写入，CancelledError 原样 re-raise。
    assert all(u.get("status") != "pending" for u in updates)


@pytest.mark.asyncio
async def test_touch_task_bumps_updated_at():
    from app.gateway.video_gen.service import VideoGenService

    calls: list[str] = []

    class FakeRepo:
        async def update(self, task_id, **kwargs):
            calls.append(task_id)
            assert kwargs == {}  # 纯 touch，不改字段

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = FakeRepo()
    await svc.touch_task("vg_touch1")
    assert calls == ["vg_touch1"]

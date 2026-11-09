"""E2: 同任务并发 pipeline 防护——_register_active_task 不覆盖在跑的 handle。"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_register_refuses_when_running():
    """同 task_id 已有未完成 task 时，第二次登记被拒、不覆盖旧 handle、factory 不被调用。"""
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)  # 不跑 __init__
    svc._active_tasks = {}

    async def _long():
        await asyncio.sleep(100)

    first = asyncio.ensure_future(_long())
    svc._active_tasks["t1"] = first

    factory_called = {"n": 0}

    def _factory():
        factory_called["n"] += 1
        return asyncio.ensure_future(_long())

    ok = svc._register_active_task("t1", _factory)
    assert ok is False, "已有未完成 task 时第二次登记必须拒绝"
    assert svc._active_tasks["t1"] is first, "旧 handle 不得被覆盖（防孤儿 task + 并发写 task_dir）"
    assert factory_called["n"] == 0, "拒绝时不得调用 factory（否则又造一个孤儿 task）"
    first.cancel()


@pytest.mark.asyncio
async def test_register_replaces_when_done():
    """旧 task 已 done 时允许新登记（正常重生成/重合成流程不被误拦）。"""
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}

    async def _done():
        return "finished"

    done_task = asyncio.ensure_future(_done())
    await done_task  # 让它完成
    svc._active_tasks["t1"] = done_task

    async def _new():
        await asyncio.sleep(100)

    new_task = asyncio.ensure_future(_new())
    ok = svc._register_active_task("t1", lambda: new_task)
    assert ok is True, "旧 task 已 done 时应允许新登记"
    assert svc._active_tasks["t1"] is new_task, "已 done 时应替换为新 handle"
    new_task.cancel()


@pytest.mark.asyncio
async def test_register_when_empty():
    """无已有 task 时正常登记。"""
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}

    async def _new():
        await asyncio.sleep(100)

    new_task = asyncio.ensure_future(_new())
    ok = svc._register_active_task("t1", lambda: new_task)
    assert ok is True
    assert svc._active_tasks["t1"] is new_task
    new_task.cancel()


@pytest.mark.asyncio
async def test_recompose_refuses_concurrent(tmp_path):
    """HTTP 入口 recompose_task：在跑的 handle 上二次触发 → ValueError（router 转 409），不覆盖。"""
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}
    svc._storage_dir = str(tmp_path)
    # E13: recompose 现要求 audio.mp3 存在；seed 之以便走到并发 guard
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "audio.mp3").write_bytes(b"au")

    # 模拟一个在跑的 pipeline handle
    async def _running():
        await asyncio.sleep(100)

    running = asyncio.ensure_future(_running())
    svc._active_tasks["t1"] = running

    # 桩掉 repo.get 让 recompose 走到登记步骤
    class _Task:
        status = "ready"
        task_type = "ai_image"
        payload = "{}"
        # 预存修复：recompose_task 读 task.dirty_frames（F5 增量引入），stub 缺该属性
        dirty_frames = []

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Task()

    svc.repo = _Repo()

    with pytest.raises(ValueError):
        await svc.recompose_task("t1", user_id="u1")
    # 核心不变量：旧 handle 未被覆盖
    assert svc._active_tasks["t1"] is running
    running.cancel()

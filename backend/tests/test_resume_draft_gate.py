"""P1-U2/U9: resume_draft 接受 ready 任务 + 摘除持久化的 recompose_only。

现状：白名单仅 DRAFT/STORY_READY/failed（service.py:2156）→ 详情页「正式渲染」
按钮（preview→standard）对 ready 任务 100% 409；recompose_task 持久化的
recompose_only 在 resume 时不 pop → 永远早退 recompose 分支。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.service import VideoGenService


def _make_svc(status: str, payload: dict, storage: str):
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = storage
    task = SimpleNamespace(status=status, payload=json.dumps(payload), task_type="ai_image")
    captured = {}
    created_tasks = []

    class _Repo:
        def __init__(self):
            self.updates = []

        async def get(self, task_id, user_id=None):
            return task

        async def update(self, task_id, **kw):
            self.updates.append(kw)

    svc.repo = _Repo()

    async def fake_wrap(task_id, user_id, task_type, payload):
        captured["payload"] = payload

    svc._wrap_pipeline = fake_wrap
    # 真实 _register_active_task 内部调用 factory() 拿到 asyncio.Task。
    # 这里把 factory() 产生的 task 收集起来，供测试 await 以确保 fake_wrap 真实执行。
    svc._register_active_task = lambda task_id, factory: (created_tasks.append(factory()), True)[-1]
    return svc, captured, created_tasks


@pytest.mark.asyncio
async def test_resume_draft_allows_ready_and_pops_recompose_only(tmp_path):
    """ready 任务可 resume + recompose_only 在派发 payload 与持久化 payload 中均被摘除。"""
    svc, captured, created_tasks = _make_svc("ready", {"recompose_only": True, "topic": "x"}, str(tmp_path))
    await svc.resume_draft("t1", "u1")  # 修复前：ValueError（ready 不在白名单）
    # 让 fire-and-forget 的 _wrap_pipeline task 真实执行，填充 captured
    if created_tasks:
        await asyncio.gather(*created_tasks, return_exceptions=True)

    assert "payload" in captured, "wrap_pipeline 必须被派发"
    assert "recompose_only" not in captured["payload"], "派发 payload 不得再带一次性标记"

    persisted = [u["payload"] for u in svc.repo.updates if "payload" in u]
    assert persisted, "resume 必须 repo.update 持久化摘除后的 payload"
    assert "recompose_only" not in json.loads(persisted[-1]), "持久化 payload 不得再带一次性标记"


@pytest.mark.asyncio
async def test_resume_draft_failed_pops_recompose_only(tmp_path):
    """failed 任务 resume 同样摘除 recompose_only（依赖磁盘 storyboard.json）。"""
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text("{}", encoding="utf-8")
    svc, captured, created_tasks = _make_svc("failed", {"recompose_only": True}, str(tmp_path))
    await svc.resume_draft("t1", "u1")
    if created_tasks:
        await asyncio.gather(*created_tasks, return_exceptions=True)
    assert "recompose_only" not in captured.get("payload", {})


@pytest.mark.asyncio
async def test_resume_draft_rejects_non_confirmable(tmp_path):
    """composing 等不可续跑状态仍拒绝（守护：白名单不是全放开）。"""
    svc, _, _ = _make_svc("composing", {"topic": "x"}, str(tmp_path))
    # P1/BUG-008 批：文案已中文化（detail 经 render_from_draft 路由透传用户可见）
    with pytest.raises(ValueError, match="当前状态不可渲染"):
        await svc.resume_draft("t1", "u1")

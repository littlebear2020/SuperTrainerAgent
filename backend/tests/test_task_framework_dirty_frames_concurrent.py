"""P0-2: add_dirty_frame 并发不得丢标记。"""

import asyncio
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.repository import TaskRepository
from deerflow.persistence.base import Base


@pytest_asyncio.fixture()
async def repo():
    # 模块级 _dirty_locks 跨用例存活，但 asyncio.Lock 绑定创建它的 loop——
    # 每个用例独立 loop，不清空会在第二个用例报 "bound to a different event loop"。
    from app.gateway._shared.task_framework import repository as repo_mod

    repo_mod._dirty_locks.clear()
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    r = TaskRepository(sf)
    await r.create(id="t1", user_id="u1", domain="video_gen", task_type="ai_image", payload="{}")
    yield r
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_add_dirty_frame_loses_nothing(repo):
    await asyncio.gather(*(repo.add_dirty_frame("t1", i) for i in range(20)))
    row = await repo.get("t1")
    assert sorted(json.loads(row.dirty_frames)) == list(range(20)), f"并发丢标记：只剩 {row.dirty_frames}"


@pytest.mark.asyncio
async def test_concurrent_add_and_clear_never_interleave(repo):
    """P1-3（2026-08-08）：clear_dirty_frames 与 add_dirty_frame 持同一把锁——
    最终态只能是「空」或「含 clear 之后全部 add」，不得出现中间态覆盖
    （修前路由层裸 repo.update 清空不走锁，两类操作互相覆盖丢帧标记）。"""
    for round_no in range(30):
        await repo.clear_dirty_frames("t1")
        # 每轮：1 个 clear 与 5 个 add 并发
        await asyncio.gather(
            repo.clear_dirty_frames("t1"),
            *(repo.add_dirty_frame("t1", 1000 + round_no * 10 + i) for i in range(5)),
        )
        row = await repo.get("t1")
        final = json.loads(row.dirty_frames)
        expected = {1000 + round_no * 10 + i for i in range(5)}
        assert final == [] or set(final) == expected, f"第 {round_no} 轮出现中间态覆盖：{final}（锁内 clear 下只允许空或全量）"


def test_clear_dirty_frames_holds_dirty_lock():
    """P1-3 结构锁（确定性）：clear_dirty_frames 必须持 _dirty_lock_for，
    且路由层不得再有裸 update(dirty_frames=...) 清空。

    竞态时序在 aiosqlite 单连接下难以稳定复现（上面的并发用例是冒烟），
    真正的回归防线是这条源码级断言——去掉锁/回退裸 update 必红。
    """
    import inspect
    from pathlib import Path

    from app.gateway._shared.task_framework import repository as repo_mod

    src = inspect.getsource(repo_mod.TaskRepository.clear_dirty_frames)
    assert "_dirty_lock_for" in src, "clear_dirty_frames 必须持 _dirty_lock_for 锁"

    router_src = (Path(repo_mod.__file__).parents[2] / "routers" / "video_gen.py").read_text(encoding="utf-8")
    assert 'dirty_frames="[]"' not in router_src, "video_gen 路由不得再裸 update 清空 dirty_frames——必须走 clear_dirty_frames"

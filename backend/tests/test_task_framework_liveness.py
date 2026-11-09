"""W1（2026-08-24 看门狗活性重构计划）：活性信号契约（任务框架层）。

命名避歧：本文件的「活性 ping（liveness ping）」是任务框架的进程活性信号，
与 SSE heartbeat（stream_bridge，浏览器连接保活）同名不同物、零耦合。

两层防线之一：复用 ``updated_at`` 列承载活性（零迁移；列表按 created_at 排序，
ping 不扰序）。契约：``repo.touch`` 只写 updated_at；``liveness_ping`` 实例级
30s 节流合并；``with_liveness`` 阶段级上下文管理器（进入启动后台滴答、退出
取消并 await，不留残task）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import BaseTaskService


@pytest_asyncio.fixture(loop_scope="function")
async def async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture()
def session_factory(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def repo(session_factory):
    return TaskRepository(session_factory=session_factory)


class _LivenessSvc(BaseTaskService):
    """最小具体服务：仅激活性契约，管线 no-op。"""

    domain = "test"
    id_prefix = "ts"

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        return None


def _make_svc(repo, tmp_path, **overrides) -> _LivenessSvc:
    svc = _LivenessSvc(repo, storage_dir=str(tmp_path))
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


async def _backdate_updated_at(repo, task_id, delta: timedelta) -> None:
    old = datetime.now(UTC) - delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()


# ---------------------------------------------------------------------------
# repository.touch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_touch_only_updates_updated_at(repo):
    """W1: touch 仅 UPDATE updated_at，其余列（status/progress/result/payload）原样。"""
    await repo.create(id="t1", user_id="u1", domain="test", task_type="demo", payload='{"a":1}')
    await repo.update("t1", status="rendering", progress=42)
    before = await repo.get("t1")
    await _backdate_updated_at(repo, "t1", timedelta(hours=1))

    await repo.touch("t1")

    after = await repo.get("t1")
    assert after.updated_at is not None and before.updated_at is not None
    assert after.updated_at > before.updated_at, "touch 必须前推 updated_at"
    # 其余列不受 ping 影响（活性信号不得篡改业务状态）
    assert after.status == "rendering"
    assert after.progress == 42
    assert after.payload == '{"a":1}'
    assert after.result == before.result


@pytest.mark.asyncio
async def test_touch_missing_task_is_noop(repo):
    """touch 不存在的任务行 -> 静默无副作用（reaper 竞态下任务已删）。"""
    await repo.touch("nope")  # 不抛
    assert await repo.get("nope") is None


# ---------------------------------------------------------------------------
# liveness_ping 节流（P2: 30s 合并 -> DB ≤1 QPS @ 20 并发）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_throttled(repo, tmp_path):
    """W1: 30s 内 5 次 ping -> 写库 1 次；窗口过后下一次 ping 落库。"""
    from unittest.mock import AsyncMock

    mock_repo = AsyncMock(spec=TaskRepository)
    mock_repo.touch = AsyncMock()
    svc = _make_svc(mock_repo, tmp_path)

    for _ in range(5):
        await svc.liveness_ping("t1")
    assert mock_repo.touch.await_count == 1, "30s 内多次 ping 必须合并为一次写库"

    # 模拟窗口已过：把节流戳回拨 31s
    svc._liveness_last_ping["t1"] -= 31.0
    await svc.liveness_ping("t1")
    assert mock_repo.touch.await_count == 2, "窗口过后 ping 须再次落库"


@pytest.mark.asyncio
async def test_ping_failure_does_not_poison_throttle(repo, tmp_path):
    """ping 写库失败（瞬时 DB 抖动）须回落节流戳，下一 tick 重试而非被吞 30s。"""
    from unittest.mock import AsyncMock

    mock_repo = AsyncMock(spec=TaskRepository)
    mock_repo.touch = AsyncMock(side_effect=[RuntimeError("db locked"), None])
    svc = _make_svc(mock_repo, tmp_path)

    await svc.liveness_ping("t1")  # 失败
    assert "t1" not in svc._liveness_last_ping, "失败不得占用节流戳"
    await svc.liveness_ping("t1")  # 立即重试 -> 落库
    assert mock_repo.touch.await_count == 2


# ---------------------------------------------------------------------------
# with_liveness 上下文管理器
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_liveness_ticks_and_stops(repo, tmp_path):
    """W1: 上下文内滴答按期刷新 updated_at（≥2 次）；退出后停止、无残task。"""
    svc = _make_svc(repo, tmp_path)
    svc._liveness_ping_interval = 0.05  # 测试加速（滴答周期 = 节流窗口，语义同 30s/30s）
    await repo.create(id="t2", user_id="u1", domain="test", task_type="demo", payload="{}")

    touch_count = 0
    orig_touch = repo.touch

    async def _counting_touch(task_id):
        nonlocal touch_count
        touch_count += 1
        await orig_touch(task_id)

    repo.touch = _counting_touch

    async with svc.with_liveness("t2"):
        await asyncio.sleep(0.18)
        assert touch_count >= 2, f"上下文内 updated_at 须前进 ≥2 次，实际 {touch_count}"
        assert svc._liveness_covered("t2"), "活跃 with_liveness 期间任务须登记为已覆盖"
        task = await repo.get("t2")
        assert task.updated_at is not None

    count_at_exit = touch_count
    await asyncio.sleep(0.15)
    assert touch_count == count_at_exit, "退出后滴答必须停止"
    assert not svc._liveness_covered("t2"), "退出后覆盖登记必须摘除"

    # 无残task：asyncio.all_tasks 里不得残留本任务的 liveness tick
    residual = [t for t in asyncio.all_tasks() if "liveness-tick" in t.get_name() and "t2" in t.get_name()]
    assert not residual, f"残task: {[t.get_name() for t in residual]}"


@pytest.mark.asyncio
async def test_with_liveness_exits_cleanly_on_exception(repo, tmp_path):
    """阶段内抛异常 -> 上下文退出（tick 取消、覆盖摘除），异常原样传播。"""
    svc = _make_svc(repo, tmp_path)
    svc._liveness_ping_interval = 0.05

    with pytest.raises(RuntimeError, match="boom"):
        async with svc.with_liveness("t3"):
            await asyncio.sleep(0.02)
            raise RuntimeError("boom")

    assert not svc._liveness_covered("t3")
    await asyncio.sleep(0.1)
    residual = [t for t in asyncio.all_tasks() if "liveness-tick" in t.get_name() and "t3" in t.get_name()]
    assert not residual


@pytest.mark.asyncio
async def test_with_liveness_stage_registers_budget(repo, tmp_path):
    """W4 接线前置：带 stage 的 with_liveness 登记阶段计时条目，退出即摘除。"""
    svc = _make_svc(repo, tmp_path)
    svc._liveness_ping_interval = 30.0

    assert svc._stage_entries("t4") == []
    async with svc.with_liveness("t4", stage="tts_batch", stage_budget=123.0):
        entries = svc._stage_entries("t4")
        assert len(entries) == 1
        stage, started, budget = entries[0]
        assert stage == "tts_batch"
        assert budget == 123.0
        assert started <= asyncio.get_running_loop().time()
    assert svc._stage_entries("t4") == []


@pytest.mark.asyncio
async def test_with_liveness_nested_stages_independent(repo, tmp_path):
    """并发阶段（草稿音频后台任务 + 主管线阶段）各自登记/摘除，互不吞条目。"""
    svc = _make_svc(repo, tmp_path)
    svc._liveness_ping_interval = 30.0

    async with svc.with_liveness("t5", stage="outer", stage_budget=10.0):
        async with svc.with_liveness("t5", stage="inner", stage_budget=20.0):
            stages = sorted(s[0] for s in svc._stage_entries("t5"))
            assert stages == ["inner", "outer"]
        assert [s[0] for s in svc._stage_entries("t5")] == ["outer"]
    assert svc._stage_entries("t5") == []


@pytest.mark.asyncio
async def test_with_liveness_disabled_by_zero_interval(repo, tmp_path):
    """interval<=0 -> 完整 no-op：不启动滴答、不登记覆盖/stage（无滴答即无覆盖语义）。"""
    from unittest.mock import AsyncMock

    mock_repo = AsyncMock(spec=TaskRepository)
    svc = _make_svc(mock_repo, tmp_path)
    svc._liveness_ping_interval = 0

    async with svc.with_liveness("t6", stage="x", stage_budget=5.0):
        await asyncio.sleep(0.05)
        assert not svc._liveness_covered("t6")
        assert svc._stage_entries("t6") == []
    assert mock_repo.touch.await_count == 0


# ---------------------------------------------------------------------------
# W2b（N3①）：pending 排队期活性--并发闸获取段挂滴答，开工即退出
# ---------------------------------------------------------------------------


class _QueuingSvc(_LivenessSvc):
    """单并发闸 + 可阻塞管线：第一个任务占闸，后续任务在闸前排队。

    每任务独立 Event 闸：放行 t1 不会连带放行 t2（否则 t2 瞬间跑完，
    轮询抓不住 running 态）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._gates: dict[str, asyncio.Event] = {}
        self._entered = asyncio.Event()

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        self._gates[task_id] = asyncio.Event()
        self._entered.set()
        await self._gates[task_id].wait()
        return None

    async def _release(self, task_id: str) -> None:
        gate = self._gates.get(task_id)
        if gate is not None:
            gate.set()
        task = self._active_tasks.get(task_id)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except Exception:
                pass


def _shield_repo_touch(repo) -> None:
    """让 ``repo.touch`` 免疫取消（测试环境 flake 守卫，语义不变）。

    队列期滴答（本文件加速到 50ms 一轮）跑在会被 ``with_liveness`` 退出取消的
    tick 协程里；取消若落在 in-flight SQL 上，SQLAlchemy asyncio 会作废连接——
    本 fixture 用 StaticPool + :memory:，唯一连接一作废整个库蒸发，后续写报
    "no such table"（实测约 1/10 概率）。生产是文件库，重连后表仍在，无此问题。
    shield 让 touch 的内层协程在 tick 被取消后照常跑完。
    """
    orig_touch = repo.touch

    async def _shielded_touch(task_id):
        await asyncio.shield(orig_touch(task_id))

    repo.touch = _shielded_touch


@pytest.mark.asyncio
async def test_pending_queue_pings(repo, tmp_path):
    """N3①：闸占满期间排队任务 updated_at 按期前进、B/C 谓词旁路不被收尸。

    生产原型：20 人生产全局并发被长任务占满 -> 排队超 30min 完全现实；
    修前排队任务零 DB 写，A（updated_at 过期）/B（progress 冻结）都会误杀
    健康排队任务。
    """
    from datetime import UTC, datetime, timedelta

    svc = _QueuingSvc(repo, storage_dir=str(tmp_path), max_concurrent_tasks=1)
    svc._liveness_ping_interval = 0.05
    _shield_repo_touch(repo)

    t1 = await svc.submit(user_id="u1", task_type="demo", payload={})
    t2 = await svc.submit(user_id="u1", task_type="demo", payload={})
    await asyncio.wait_for(svc._entered.wait(), timeout=2)  # t1 进管线占闸
    await asyncio.sleep(0.15)  # t2 排队，滴答跑若干轮

    assert svc._liveness_covered(t2.id), "排队任务必须被活性覆盖"
    row2 = await repo.get(t2.id)
    assert row2 is not None and row2.updated_at is not None
    # 旧 A 谓词场景复现：updated_at 若不前进则早已过期。此处须刚被 ping 刷新。
    age = datetime.now(UTC).replace(tzinfo=None) - row2.updated_at
    assert age < timedelta(seconds=5), f"排队任务 updated_at 应被 ping 前推，实际龄 {age}"

    # B 谓词场景复现：progress=0 自 2h 前未变（闸占满 40min 的放大版）-> 覆盖期不得收尸
    svc._progress_baseline[t2.id] = (0, datetime.now(UTC) - timedelta(hours=2))
    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0, "健康排队任务不得被收尸（覆盖期 B/C 全旁路）"
    assert (await repo.get(t2.id)).status != "failed"

    await svc._release(t1.id)  # 放行 t1，释放全局并发闸
    # t2 拿到闸、进管线后才创建自己的 gate——等 gate 就位再放行（事件驱动）。
    # 修前直接 _release(t2)：gate 尚未创建 → helper 里 wait_for(shield) 白等满
    # 5s 超时（本用例 5.17s 的全部来源），且掩盖「t2 是否真进管线」。
    for _ in range(300):
        if t2.id in svc._gates:
            break
        await asyncio.sleep(0.01)
    assert t2.id in svc._gates, "t2 应在 t1 放行后进入管线"
    await svc._release(t2.id)


@pytest.mark.asyncio
async def test_queue_ping_exits_on_start(repo, tmp_path):
    """N3①：进入管线后排队 tick 退出--覆盖=活跃滴答，管线执行期不残留第二个 tick。"""
    svc = _QueuingSvc(repo, storage_dir=str(tmp_path), max_concurrent_tasks=1)
    svc._liveness_ping_interval = 0.05
    _shield_repo_touch(repo)

    t1 = await svc.submit(user_id="u1", task_type="demo", payload={})
    t2 = await svc.submit(user_id="u1", task_type="demo", payload={})
    await asyncio.wait_for(svc._entered.wait(), timeout=2)
    await asyncio.sleep(0.05)
    assert svc._liveness_covered(t2.id)

    # 放行 t1：t2 开工，停在自己的闸上（管线体执行中）。
    await svc._release(t1.id)

    # t2 现在应进入管线；轮询等待其真正在跑
    async def _wait_running():
        for _ in range(200):
            row = await repo.get(t2.id)
            if row and row.status == "running":
                return True
            await asyncio.sleep(0.01)
        return False

    assert await _wait_running(), "t2 应在放行后进入 running"
    await asyncio.sleep(0.1)  # 给排队 CM 的 finally 一点时间
    assert not svc._liveness_covered(t2.id), "开工后排队滴答必须退出"
    residual = [t for t in asyncio.all_tasks() if "liveness-tick" in t.get_name() and t2.id in t.get_name()]
    assert not residual, f"残留排队 tick: {[t.get_name() for t in residual]}"
    await svc._release(t2.id)


@pytest.mark.asyncio
async def test_with_liveness_stage_without_budget_not_registered(repo, tmp_path):
    """P1 防御（2026-08-25 复核）：传 stage 不传预算 -> 不登记计时条目
    （旧实现落 0 预算，下一 reaper tick 即「超预算」误杀健康任务）。"""
    svc = _make_svc(repo, tmp_path)
    svc._liveness_ping_interval = 30.0

    async with svc.with_liveness("t7", stage="no_budget_stage"):
        assert svc._liveness_covered("t7"), "纯活性覆盖语义保留"
        assert svc._stage_entries("t7") == [], "无预算的阶段不得登记计时条目"
    assert not svc._liveness_covered("t7")

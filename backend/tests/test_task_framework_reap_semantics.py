"""W3/W4/W6（2026-08-24 看门狗活性重构）：reaper 谓词重构 + 分阶段预算 + 收尸语义。

覆盖计划第三节：
- W3 主谓词（覆盖阶段沉默 > 阈值判死）、B/C 降级为无覆盖兜底（窗口 = P5）、
  DRAFT/STORY_READY 全谓词豁免（N3②）；
- W4 预算谓词（心跳正常但阶段超预算 -> 收尸，error_message 归因 budget）；
- W6 收尸三步语义（failed 落库 -> 有界 cancel 30s -> CAS 重排恰好一次），
  超时不重排、终态不被取消处理器重排、cleanup 按模块分治。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from _task_drain import drain_service_tasks
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


class _ReapSvc(BaseTaskService):
    """可控 reap 测试服务：管线可阻塞/吞取消；cleanup 行为可注入。"""

    domain = "test"
    id_prefix = "ts"
    max_retries = 2

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.pipeline_calls = 0
        self.cleaned: list[str] = []
        self._pipeline_block = asyncio.Event()
        self._swallow_cancel = False

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        self.pipeline_calls += 1
        await self._pipeline_block.wait()
        return None

    async def _cleanup_task(self, task):
        self.cleaned.append(task.id)


def _dummy_tick() -> asyncio.Task:
    """模拟一个活跃 liveness tick task（可被 reaper 安全取消）。"""
    return asyncio.create_task(asyncio.sleep(999), name="liveness-tick-test-dummy")


async def _age(repo, task_id, delta: timedelta) -> None:
    old = datetime.now(UTC) - delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()


async def _mk_task(repo, task_id="t1", *, status="running", progress=30, retry_count=0, claimed_by=None, lease_expires_at=None) -> None:
    await repo.create(id=task_id, user_id="u1", domain="test", task_type="demo", payload="{}")
    await repo.update(
        task_id,
        status=status,
        progress=progress,
        retry_count=retry_count,
        claimed_by=claimed_by,
        lease_expires_at=lease_expires_at,
    )


# ---------------------------------------------------------------------------
# W3：主谓词（死亡判定，仅覆盖阶段）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_covered_stage_death_detected(repo, tmp_path):
    """W3：已覆盖任务沉默 121s（> P1=120s）-> 主谓词收尸（死亡归因）。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.liveness_threshold_seconds = 120
    await _mk_task(repo, "covered", retry_count=2)  # 预算烧尽 -> failed 终态（消息断言用）
    svc._liveness_ticks["covered"] = {_dummy_tick()}  # 模拟活跃 with_liveness 登记中的 tick
    try:
        await _age(repo, "covered", timedelta(seconds=121))

        reaped = await svc.reap_stalled_tasks()
        assert reaped == 1
        row = await repo.get("covered")
        assert row.status == "failed"
        assert "Reaped by watchdog (death" in (row.error_message or ""), row.error_message
    finally:
        svc._liveness_ticks.pop("covered", None)


@pytest.mark.asyncio
async def test_covered_stage_death_kill_switch(repo, tmp_path):
    """P9：liveness_threshold_seconds=0 -> 主谓词静默（回滚开关）。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.liveness_threshold_seconds = 0
    await _mk_task(repo, "covered2")
    svc._liveness_ticks["covered2"] = {_dummy_tick()}
    try:
        await _age(repo, "covered2", timedelta(seconds=600))
        assert await svc.reap_stalled_tasks() == 0
        assert (await repo.get("covered2")).status == "running"
    finally:
        svc._liveness_ticks.pop("covered2", None)


@pytest.mark.asyncio
async def test_covered_stage_no_false_kill(repo, tmp_path):
    """W3 核心（08-20/21 三次误杀的机制修复）：心跳正常 + 整数 progress 冻结 2h
    （112 帧串行任务每 7~8 帧才 +1 的合法沉默放大版）-> B/C 全旁路，不收尸。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.liveness_threshold_seconds = 120
    svc.progress_stall_timeout = 1800  # P5 兜底窗口 30min
    await _mk_task(repo, "healthy", progress=77)
    svc._liveness_ticks["healthy"] = {_dummy_tick()}  # 活跃滴答
    try:
        # progress=77 自 2h 前未变（旧 C 谓词 20min 窗口必误杀的时序）
        svc._progress_baseline["healthy"] = (77, datetime.now(UTC) - timedelta(hours=2))
        # updated_at 新鲜（滴答在 ping）

        reaped = await svc.reap_stalled_tasks()
        assert reaped == 0, "覆盖期 B/C 必须全旁路（心跳正常即不收尸）"
        assert (await repo.get("healthy")).status == "running"
    finally:
        svc._liveness_ticks.pop("healthy", None)


@pytest.mark.asyncio
async def test_uncovered_stage_backstop(repo, tmp_path):
    """W3：无覆盖 + progress 冻结 30min（=P5）-> 兜底收尸；29min -> 不收。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=100000)  # A/B 缩放窗口调大，只测 C 兜底
    svc.progress_stall_timeout = 1800

    await _mk_task(repo, "frozen29", progress=40)
    svc._progress_baseline["frozen29"] = (40, datetime.now(UTC) - timedelta(minutes=29))
    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("frozen29")).status == "running"

    await _mk_task(repo, "frozen30", progress=40, retry_count=2)  # 烧尽 -> failed 终态（消息断言用）
    svc._progress_baseline["frozen30"] = (40, datetime.now(UTC) - timedelta(minutes=31))
    assert await svc.reap_stalled_tasks() == 1
    row = await repo.get("frozen30")
    assert row.status == "failed"
    assert "Reaped by watchdog (backstop" in (row.error_message or ""), row.error_message


@pytest.mark.asyncio
async def test_draft_story_ready_exempt_all_predicates(repo, tmp_path):
    """N3②：DRAFT/STORY_READY 是持久等待态（无在途协程）--A/B/C/主谓词全部豁免。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)  # A 窗口 60s
    svc.liveness_threshold_seconds = 120
    svc.progress_stall_timeout = 60

    for status in ("draft", "story_ready"):
        tid = f"wait_{status}"
        await _mk_task(repo, tid, status=status, progress=100)
        # A 场景：updated_at 2h 前过期；B/C 场景：baseline 植入 2h 前
        await _age(repo, tid, timedelta(hours=2))
        svc._progress_baseline[tid] = (100, datetime.now(UTC) - timedelta(hours=2))

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0, "等待态任意空闲时长不得收尸"
    assert (await repo.get("wait_draft")).status == "draft"
    assert (await repo.get("wait_story_ready")).status == "story_ready"


# ---------------------------------------------------------------------------
# W4：分阶段总预算（第二层，hung 死判定）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hung_with_heartbeat_reaped_by_budget(repo, tmp_path):
    """W4/WD3：心跳正常但阶段超预算 -> 预算谓词收尸，error_message 归因 stage budget。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.liveness_threshold_seconds = 120
    await _mk_task(repo, "hung", progress=50, retry_count=2)  # 烧尽 -> failed 终态（消息断言用）
    svc._liveness_ticks["hung"] = {_dummy_tick()}
    try:
        # 注册一个已超预算的阶段条目（started 500s 前，预算 100s）
        svc._liveness_stages["hung"] = {1: ("llm_stage", __import__("time").monotonic() - 500.0, 100.0)}

        reaped = await svc.reap_stalled_tasks()
        assert reaped == 1
        row = await repo.get("hung")
        assert row.status == "failed"
        msg = row.error_message or ""
        assert "stage budget" in msg and "llm_stage" in msg and "exceeded" in msg, msg
        assert "death" not in msg, "心跳正常时不得归因死亡谓词"
    finally:
        svc._liveness_ticks.pop("hung", None)
        svc._liveness_stages.pop("hung", None)


@pytest.mark.asyncio
async def test_within_budget_alive(repo, tmp_path):
    """W4：阶段在预算内（含心跳正常）-> 不收尸。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    svc.liveness_threshold_seconds = 120
    await _mk_task(repo, "inbudget", progress=50)
    svc._liveness_ticks["inbudget"] = {_dummy_tick()}
    try:
        import time as _time

        svc._liveness_stages["inbudget"] = {1: ("llm_stage", _time.monotonic() - 50.0, 100.0)}
        assert await svc.reap_stalled_tasks() == 0
        assert (await repo.get("inbudget")).status == "running"
    finally:
        svc._liveness_ticks.pop("inbudget", None)
        svc._liveness_stages.pop("inbudget", None)


# ---------------------------------------------------------------------------
# W6：收尸语义统一（failed 落库 -> 有界 cancel -> CAS 重排；cleanup 分治）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminal_task_not_requeued_by_cancel_handler(repo, tmp_path):
    """W6/P1-8：reaper 先落 failed -> cancel 传播 -> 取消处理器读到终态即尊重，
    不重排（修前竞态烧重试预算）。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    await _mk_task(repo, "victim")
    task = asyncio.create_task(svc._wrap_pipeline("victim", "u1", "demo", {}))  # 管线阻塞在 event 上（running）
    svc._active_tasks["victim"] = task
    await asyncio.sleep(0.05)
    assert (await repo.get("victim")).status == "running"

    await _age(repo, "victim", timedelta(minutes=10))
    await svc.reap_stalled_tasks()

    await asyncio.sleep(0.1)
    row = await repo.get("victim")
    # reaper 自身按预算重排一次（0->1）；取消处理器读到终态不得再叠加（否则会是 2）
    assert row.retry_count == 1, f"恰好一次重排（reaper 的），实际 {row.retry_count}"
    assert row.status in ("pending", "running"), row.status
    await drain_service_tasks(svc)


@pytest.mark.asyncio
async def test_reaper_requeues_exactly_once(repo, tmp_path):
    """W6：预算未尽 -> 重排恰好一次、retry+1、result='{}'、pending；预算烧尽 -> 保持 failed。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)

    # 场景1：retry=0 < max=2 -> 重排一次（管线阻塞在 event 上，reap 时 running）
    await _mk_task(repo, "rq1", retry_count=0)
    task = asyncio.create_task(svc._wrap_pipeline("rq1", "u1", "demo", {}))
    svc._active_tasks["rq1"] = task
    await asyncio.sleep(0.05)
    await _age(repo, "rq1", timedelta(minutes=10))
    await svc.reap_stalled_tasks()
    await asyncio.sleep(0.1)  # 给重排后的新任务一点时间（会立刻翻 running，属预期）
    row = await repo.get("rq1")
    assert row.status in ("pending", "running"), f"预算未尽须 CAS 重排（pending/running 均为重排后形态）：{row.status}"
    assert row.retry_count == 1
    assert row.result == "{}", "重排必须清空残留 result（08-20 事故：列非空约束，置空 JSON）"
    # 恰好一次：无第二个并发 reschedule 句柄
    live = [t for t in (svc._active_tasks.get("rq1"),) if t and not t.done()]
    assert len(live) <= 1
    await drain_service_tasks(svc)

    # 场景2：retry=2 = max -> 保持 failed
    await _mk_task(repo, "rq2", retry_count=2)
    task2 = asyncio.create_task(svc._wrap_pipeline("rq2", "u1", "demo", {}))  # 阻塞在 event 上
    svc._active_tasks["rq2"] = task2
    await asyncio.sleep(0.05)
    await _age(repo, "rq2", timedelta(minutes=10))
    await svc.reap_stalled_tasks()
    await asyncio.sleep(0.1)
    row2 = await repo.get("rq2")
    assert row2.status == "failed", "预算烧尽保持 failed"
    assert row2.retry_count == 2, "烧尽后不得再 +1"
    await drain_service_tasks(svc)


@pytest.mark.asyncio
async def test_bounded_cancel_timeout_no_requeue(repo, tmp_path):
    """W6/N1：协程吞取消（模拟 to_thread/同步 ffmpeg 卡死）-> 30s 有界等待超时，
    保持 failed、本次不重排、reaper 循环不被挂住（本测试自身能返回即证明）。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    svc._swallow_cancel = True

    # 管线体：捕获 CancelledError 并长期不退出（吞取消的 wedge 模拟）
    async def _wedged_pipeline(*a, **k):
        svc.pipeline_calls += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)  # 吞掉第一轮取消，继续挂
            raise

    svc._run_pipeline = _wedged_pipeline  # type: ignore[method-assign]
    await _mk_task(repo, "wedge")
    task = asyncio.create_task(svc._wrap_pipeline("wedge", "u1", "demo", {}))
    svc._active_tasks["wedge"] = task
    await asyncio.sleep(0.05)

    await _age(repo, "wedge", timedelta(minutes=10))
    import app.gateway._shared.task_framework.service as svc_mod

    orig_wait = asyncio.wait
    waited: list[float] = []

    async def _fast_wait(fs, timeout=None, **kw):
        waited.append(float(timeout) if timeout is not None else -1.0)
        # 模拟 30s 超时到期（真实等待不可能在测试里等 30s）
        await asyncio.sleep(0)
        return set(), set(fs)

    svc_mod.asyncio.wait = _fast_wait  # type: ignore[assignment]
    try:
        reaped = await svc.reap_stalled_tasks()
    finally:
        svc_mod.asyncio.wait = orig_wait  # type: ignore[assignment]
    await asyncio.sleep(0.05)

    assert reaped == 1
    assert 30.0 in waited, f"取消等待必须有界（30s），实际 {waited}"
    row = await repo.get("wedge")
    assert row.status == "failed", "超时保持 failed"
    assert row.retry_count == 0, "cancel 超时本次不得重排（防旧协程未死新管线双跑写盘）"
    # wedge 协程仍在（吞取消），测试收尾强杀
    task.cancel()
    with pytest.raises((asyncio.CancelledError, Exception)):
        await asyncio.wait_for(asyncio.shield(task), timeout=2)


@pytest.mark.asyncio
async def test_failed_terminal_cleans_dir(repo, tmp_path):
    """W6/N2④（voice_twin/PPT 规则）：终态 failed（预算烧尽）才 _cleanup_task。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    task_dir = tmp_path / "u1" / "tasks" / "term"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "artifact.bin").write_text("keep-me")

    await _mk_task(repo, "term", retry_count=2)  # 预算烧尽
    task = asyncio.create_task(svc._wrap_pipeline("term", "u1", "demo", {}))
    svc._active_tasks["term"] = task
    await asyncio.sleep(0.05)
    await _age(repo, "term", timedelta(minutes=10))
    await svc.reap_stalled_tasks()
    await asyncio.sleep(0.05)

    row = await repo.get("term")
    assert row.status == "failed"
    assert svc.cleaned == ["term"], "终态 failed 须按模块 cleanup 规则执行"


@pytest.mark.asyncio
async def test_requeue_preserves_task_dir_for_voice_twin_ppt(repo, tmp_path):
    """W6/N2④：重排路径不删目录（voice_twin/PPT 的目录只在终态 failed 清理）。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    task_dir = tmp_path / "u1" / "tasks" / "keepdir"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "partial.mp3").write_text("data")

    await _mk_task(repo, "keepdir", retry_count=0)  # 会重排
    task = asyncio.create_task(svc._wrap_pipeline("keepdir", "u1", "demo", {}))
    svc._active_tasks["keepdir"] = task
    await asyncio.sleep(0.05)
    await _age(repo, "keepdir", timedelta(minutes=10))
    await svc.reap_stalled_tasks()
    await asyncio.sleep(0.1)

    row = await repo.get("keepdir")
    assert row.status in ("pending", "running"), f"重排后形态：{row.status}"
    assert row.retry_count == 1
    assert svc.cleaned == [], "重排路径不得触发目录清理"
    assert (task_dir / "partial.mp3").exists()
    await drain_service_tasks(svc)


@pytest.mark.asyncio
async def test_reaper_requeue_cas_lost_no_reschedule(repo, tmp_path, monkeypatch):
    """W6/N2③（主重排路径的 CAS 失败分支，2026-08-25 复核补）：
    failed 落库与 CAS 重排之间任务状态被并发方改写（如用户手工干预/另一处理器）
    -> update_if_status 守卫失败 -> 保持 failed、不重排、不叠加重试。"""
    svc = _ReapSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)

    await _mk_task(repo, "cas1", retry_count=0)
    task = asyncio.create_task(svc._wrap_pipeline("cas1", "u1", "demo", {}))  # 阻塞在 event 上
    svc._active_tasks["cas1"] = task
    await asyncio.sleep(0.05)
    await _age(repo, "cas1", timedelta(minutes=10))

    # 模拟 CAS 失败：update_if_status 恒返回 False（重排窗口内状态已被改走）
    async def _cas_always_fails(*a, **kw):
        return False

    monkeypatch.setattr(svc.repo, "update_if_status", _cas_always_fails)
    reaped = await svc.reap_stalled_tasks()
    await asyncio.sleep(0.1)

    assert reaped == 1, "收尸计数不受 CAS 失败影响（failed 已落库）"
    row = await repo.get("cas1")
    assert row.status == "failed", "CAS 失败 -> 保持 failed 不重排"
    assert row.retry_count == 0, "CAS 失败不得叠加重试"
    assert "Reaped by watchdog (death" in (row.error_message or "")
    live = svc._active_tasks.get("cas1")
    assert live is None or live.done(), "CAS 失败不得重新调度新管线"
    await drain_service_tasks(svc)

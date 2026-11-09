"""W3b（2026-08-24 看门狗活性重构，N2）：composing 行的 gateway 双条件兜底。

规格四细则（v3 修订表 N2）：
① composing 行上 A/B/C/主谓词全部旁路，只有「租约失效 + updated_at 陈旧 > P1」
  双条件兜底生效（A 会对租约存活、跑长 ffmpeg 的健康 worker 收尸--concat 期
  progress_cb 无调用，updated_at 可冻结数十分钟）；
② 租约为 NULL（交棒未被 claim）不收--worker 的待领队列，worker 重启即恢复；
  仅超龄 warning 日志；
③ 收尸重排 UPDATE 带 CAS 守卫（WHERE claimed_by = 读到的值），重置
  claimed_by/lease、result='{}'，防读-写窗口内 worker 复活双跑（TOCTOU）；
④ cleanup 分治：composing 行 gateway 永不删目录（video_gen 全路径不删由
  test_video_gen_watchdog.py 锁死；本文件锁框架侧 composing 分支）。
"""

from __future__ import annotations

import logging
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


class _BackstopSvc(BaseTaskService):
    domain = "test"
    id_prefix = "ts"
    max_retries = 2
    composing_backstop_enabled = True
    liveness_threshold_seconds = 120  # P1

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cleaned: list[str] = []

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        return None

    async def _cleanup_task(self, task):
        self.cleaned.append(task.id)


async def _mk_composing(
    repo,
    task_id,
    *,
    claimed_by="worker-A",
    lease_delta: timedelta | None = timedelta(minutes=-5),  # 默认已过期 5min
    stale_delta: timedelta = timedelta(minutes=10),
    retry_count=0,
    progress=95,
):
    await repo.create(id=task_id, user_id="u1", domain="test", task_type="ai_video", payload="{}")
    lease = (datetime.now(UTC) + lease_delta) if lease_delta is not None else None
    await repo.update(
        task_id,
        status="composing",
        progress=progress,
        retry_count=retry_count,
        claimed_by=claimed_by,
        lease_expires_at=lease,
    )
    old = datetime.now(UTC) - stale_delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()
    return await repo.get(task_id)


@pytest.mark.asyncio
async def test_composing_bypasses_ab_c_main(repo, tmp_path):
    """N2①：构造满足 A/B/C/主谓词的 composing 行 -> 一律不收（谓词全旁路）。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    # 无租约（NULL）+ updated_at 过期 2h：A 的条件全满足；进度 95 冻结 2h：B/C 条件全满足
    await _mk_composing(repo, "c_bypass", claimed_by=None, lease_delta=None, stale_delta=timedelta(hours=2))
    svc._progress_baseline["c_bypass"] = (95, datetime.now(UTC) - timedelta(hours=2))
    # 「覆盖」伪造：composing 不在 GATEWAY_STAGES，主谓词本就不评估；显式登记亦不得影响

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0
    assert (await repo.get("c_bypass")).status == "composing"


@pytest.mark.asyncio
async def test_lease_alive_skipped(repo, tmp_path):
    """N2①：租约存活的 composing 行（worker 清道夫第一责任人）-> 跳过。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    # 租约还有 10min；updated_at 冻结 2h（健康 worker 跑长 concat 的真实形态）
    await _mk_composing(repo, "c_alive", lease_delta=timedelta(minutes=10), stale_delta=timedelta(hours=2))

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 0
    row = await repo.get("c_alive")
    assert row.status == "composing"
    assert row.claimed_by == "worker-A"


@pytest.mark.asyncio
async def test_lease_expired_and_stale_reaped(repo, tmp_path):
    """双条件（租约失效 + updated_at 陈旧 > P1）-> 收尸：CAS 重排释放 claim、
    retry+1、result='{}'（对齐 worker 清道夫语义，状态保持 composing 供再认领）。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    await _mk_composing(repo, "c_dead", lease_delta=timedelta(minutes=-5), stale_delta=timedelta(minutes=10))

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 1
    row = await repo.get("c_dead")
    assert row.status == "composing", "释放 claim 供 worker 再认领（不翻 gateway pending）"
    assert row.claimed_by is None
    assert row.lease_expires_at is None
    assert row.retry_count == 1
    assert row.result == "{}"
    assert "composing_backstop" in (row.error_message or ""), row.error_message


@pytest.mark.asyncio
async def test_lease_expired_but_fresh_update_skipped(repo, tmp_path):
    """双条件缺一不可：租约过期但 updated_at 新鲜（worker 可能刚写完成果）-> 不收。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    await _mk_composing(repo, "c_fresh", lease_delta=timedelta(minutes=-1), stale_delta=timedelta(seconds=10))

    assert await svc.reap_stalled_tasks() == 0
    row = await repo.get("c_fresh")
    assert row.status == "composing"
    assert row.claimed_by == "worker-A"


@pytest.mark.asyncio
async def test_lease_null_unclaimed_not_reaped(repo, tmp_path, caplog):
    """N2②：NULL 租约（交棒未被 claim）-> 不收（worker 待领队列，重启即恢复）；
    超龄（> P5）打 warning 日志做可观测。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    svc.progress_stall_timeout = 1800  # P5
    await _mk_composing(repo, "c_unclaimed", claimed_by=None, lease_delta=None, stale_delta=timedelta(hours=1))

    with caplog.at_level(logging.WARNING, logger="app.gateway._shared.task_framework.service"):
        reaped = await svc.reap_stalled_tasks()
    assert reaped == 0
    row = await repo.get("c_unclaimed")
    assert row.status == "composing"
    assert "c_unclaimed" in caplog.text, "超龄未 claim 须打 warning 日志"


@pytest.mark.asyncio
async def test_requeue_cas_guard(repo, tmp_path):
    """N2③：读-写之间 claimed_by 变化（worker 复活重新续约/换主）-> CAS 失败不重排。

    构造：list_composing 返回陈旧快照（worker-A / 租约已过期），而 DB 真值已被
    worker-B 接管（claimed_by 变化 + 租约续期）--即「读 -> 写」窗口内的并发变更。
    """
    from types import SimpleNamespace

    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    await _mk_composing(repo, "c_cas", claimed_by="worker-A", lease_delta=timedelta(minutes=-5))

    original_list = repo.list_composing

    async def _stale_list(domain):
        rows = await original_list(domain)
        # 模拟：reaper 读到旧快照后，worker-B 抢先接管（真实 DB 已变）
        await repo.update(
            "c_cas",
            claimed_by="worker-B",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        stale = []
        for r in rows:
            if r.id == "c_cas":
                snap = SimpleNamespace(**{c.name: getattr(r, c.name) for c in TaskRow.__table__.columns})
                snap.claimed_by = "worker-A"  # reaper 看到的旧值
                snap.lease_expires_at = datetime.now(UTC) - timedelta(minutes=5)
                stale.append(snap)
            else:
                stale.append(r)
        return stale

    repo.list_composing = _stale_list  # type: ignore[method-assign]
    try:
        reaped = await svc.reap_stalled_tasks()
    finally:
        repo.list_composing = original_list  # type: ignore[method-assign]

    assert reaped == 0, "claimed_by 已变 -> CAS 守卫必须拒绝重排"
    row = await repo.get("c_cas")
    assert row.claimed_by == "worker-B", "worker-B 的接管不得被覆盖"
    assert row.retry_count == 0


@pytest.mark.asyncio
async def test_composing_budget_exhausted_failed(repo, tmp_path):
    """预算烧尽的 composing 行 -> failed 终态（error_message 归因 composing_backstop）。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    await _mk_composing(repo, "c_max", lease_delta=timedelta(minutes=-5), retry_count=2)

    reaped = await svc.reap_stalled_tasks()
    assert reaped == 1
    row = await repo.get("c_max")
    assert row.status == "failed"
    assert "composing_backstop" in (row.error_message or "")
    assert row.claimed_by is None and row.lease_expires_at is None


@pytest.mark.asyncio
async def test_composing_dir_never_deleted(repo, tmp_path):
    """N2④：composing 行任何收尸路径（重排/终态 failed）都不删 task_dir。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    task_dir = tmp_path / "u1" / "tasks" / "c_dir"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "video.mp4.part").write_text("partial")

    await _mk_composing(repo, "c_dir", lease_delta=timedelta(minutes=-5))
    await svc.reap_stalled_tasks()  # 重排路径
    assert (task_dir / "video.mp4.part").exists()
    assert svc.cleaned == [], "composing 行 gateway 永不删目录"

    await _mk_composing(repo, "c_dir2", lease_delta=timedelta(minutes=-5), retry_count=2)
    await svc.reap_stalled_tasks()  # 终态 failed 路径
    row = await repo.get("c_dir2")
    assert row.status == "failed"
    assert svc.cleaned == [], "composing 终态亦不触发 gateway 目录清理"


@pytest.mark.asyncio
async def test_composing_backstop_disabled(repo, tmp_path):
    """P10（v3 小项⑥）：composing_backstop_enabled=False -> W3b 不评估（回滚开关）。"""
    svc = _BackstopSvc(repo, storage_dir=str(tmp_path), stall_timeout=60)
    svc.composing_backstop_enabled = False
    await _mk_composing(repo, "c_off", lease_delta=timedelta(minutes=-5))

    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("c_off")).claimed_by == "worker-A"

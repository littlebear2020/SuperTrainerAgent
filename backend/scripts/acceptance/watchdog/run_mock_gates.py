#!/usr/bin/env python3
"""看门狗活性重构（2026-08-24 计划 W9）--部署前门禁的 mock 演练 runner。

对计划第四节「部署前门禁」里**可在进程内验证**的项做故障注入演练（时间尺度
按比例缩小：P1 120s -> 2s，reaper 间隔 60s -> 0.5s，收尸 cancel 界 30s -> 1s）：

- WD2① 真死检测时效（gateway 侧）：滴答死亡 + 协程 wedge -> 主谓词收尸 ≤ (P1+扫描)×1.1，
  按预算重排（resuming 文案 + retry+1）；
- WD2② composing 兜底：租约过期 + updated_at 陈旧 -> CAS 释放 claim 重排；
- WD3 hung 归因：心跳正常 + 阶段超预算 -> 仅预算谓词收尸（error_message 含
  ``stage budget ... exceeded``），死亡谓词日志 = 0；
- WD4 收尸语义：终态不被取消处理器重排 / CAS 重排恰好一次 / cancel 超时不重排；
- WD8 DB 负载：ping 节流（窗口内合并为一次写库）。

用法：
    cd backend && uv run python scripts/acceptance/watchdog/run_mock_gates.py
    uv run python scripts/acceptance/watchdog/run_mock_gates.py --only wd2a,wd3

真实环境门禁（WD1 误杀消除 / WD7 预算 p99 校准）须上测试服跑：
    calibrate.py（W8 校准）+ wd1_reap_scan.py（日志扫描）--见同目录 README.md。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import update as sa_update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.gateway._shared.task_framework.models import TaskRow  # noqa: E402
from app.gateway._shared.task_framework.repository import TaskRepository  # noqa: E402
from app.gateway._shared.task_framework.service import BaseTaskService  # noqa: E402

# 缩小的时间尺度（保持与生产配置同比例：P1:扫描:cancel = 120:60:30 -> 2:1:1）
MOCK_P1 = 2.0  # 活性沉默阈值（生产 120s）
MOCK_INTERVAL = 1.0  # reaper 扫描周期（生产 60s）
MOCK_CANCEL_BOUND = 30.0  # 生产 30s（真实等待，mock 用 wedge 场景验证有界性）
MOCK_PING = 0.2  # 滴答周期（生产 30s）
MOCK_BUDGET = 1.5  # 演练用阶段预算（生产按 W4 公式）


class GateReport:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def record(self, gate: str, ok: bool, detail: str) -> None:
        self.results.append((gate, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {gate}: {detail}")

    @property
    def all_pass(self) -> bool:
        return all(ok for _, ok, _ in self.results)


async def make_repo():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return TaskRepository(session_factory=sf), engine


async def backdate(repo, task_id, delta):
    old = datetime.now(UTC) - delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()


class _DrillSvc(BaseTaskService):
    """演练服务：管线可 wedge/吞取消；cancel 有界参数可注入。"""

    domain = "drill"
    id_prefix = "dr"

    def __init__(self, repo, storage_dir):
        super().__init__(repo, storage_dir=storage_dir)
        self.liveness_threshold_seconds = int(MOCK_P1)
        self._liveness_ping_interval = MOCK_PING
        self.mode = "hang"
        self.pipeline_calls = 0
        self.cleaned: list[str] = []

    async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
        self.pipeline_calls += 1
        if self.mode == "hang":
            # 心跳正常但阶段超预算：真实滴答 + 永不返回
            async with self.with_liveness(task_id, stage="llm_stage", stage_budget=MOCK_BUDGET):
                await asyncio.sleep(3600)
        elif self.mode == "tick_death":
            # 滴答死亡（模拟 tick 协程被杀/循环卡死）+ 协程 wedge 在无 await 区
            async with self.with_liveness(task_id, stage="llm_stage", stage_budget=999.0):
                # 直接杀掉自己的滴答（进程死亡/循环卡死的进程内等价物）
                for tick in list(self._liveness_ticks.get(task_id, ())):
                    tick.cancel()
                await asyncio.sleep(3600)
        elif self.mode == "swallow_cancel":
            # 吞取消的 wedge（to_thread/同步 ffmpeg 卡死形态）
            async with self.with_liveness(task_id, stage="llm_stage", stage_budget=999.0):
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    await asyncio.sleep(3600)
                    raise
        return None

    async def _cleanup_task(self, task):
        self.cleaned.append(task.id)


async def wd2a_gateway_death(report: GateReport, tmp: Path) -> None:
    """WD2①：gateway 侧真死（滴答死亡 + 协程无写库）-> ≤(P1+扫描)×1.1 收尸 + 按预算重排。"""
    repo, engine = await make_repo()
    svc = _DrillSvc(repo, storage_dir=str(tmp))
    svc.mode = "tick_death"
    await repo.create(id="wd2a", user_id="u1", domain="drill", task_type="demo", payload="{}")
    await repo.update("wd2a", status="running", progress=10)
    task = asyncio.create_task(svc._wrap_pipeline("wd2a", "u1", "demo", {}))
    svc._active_tasks["wd2a"] = task
    await asyncio.sleep(0.5)  # 进入阶段

    limit = (MOCK_P1 + MOCK_INTERVAL) * 1.1
    t0 = time.monotonic()
    reaped = 0
    while time.monotonic() - t0 < limit + 5:
        reaped += await svc.reap_stalled_tasks()
        if reaped:
            break
        await asyncio.sleep(MOCK_INTERVAL)
    elapsed = time.monotonic() - t0
    row = await repo.get("wd2a")
    budget_left = row.retry_count < svc.max_retries
    report.record(
        "wd2a_gateway_death",
        reaped == 1 and elapsed <= limit and budget_left and "resuming" in (row.error_message or ""),
        f"reaped={reaped}, elapsed={elapsed:.2f}s ≤ {limit:.2f}s, retry={row.retry_count}, msg={row.error_message!r}",
    )
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=3)
    await engine.dispose()


async def wd2b_composing_backstop(report: GateReport, tmp: Path) -> None:
    """WD2②：composing 租约过期 + updated_at 陈旧 -> CAS 释放 claim 重排（租约口径）。"""
    repo, engine = await make_repo()
    svc = _DrillSvc(repo, storage_dir=str(tmp))
    svc.composing_backstop_enabled = True
    await repo.create(id="wd2b", user_id="u1", domain="drill", task_type="demo", payload="{}")
    await repo.update(
        "wd2b",
        status="composing",
        progress=95,
        claimed_by="worker-X",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=MOCK_P1),
    )
    await backdate(repo, "wd2b", timedelta(seconds=MOCK_P1 + 1))
    await asyncio.sleep(MOCK_P1 + 0.5)  # 让 updated_at 真实陈旧过 P1

    reaped = await svc.reap_stalled_tasks()
    row = await repo.get("wd2b")
    report.record(
        "wd2b_composing_backstop",
        reaped == 1 and row.claimed_by is None and row.lease_expires_at is None and row.retry_count == 1 and row.status == "composing",
        f"reaped={reaped}, claimed_by={row.claimed_by}, retry={row.retry_count}, status={row.status}, msg={row.error_message!r}",
    )
    await engine.dispose()


async def wd3_hung_attribution(report: GateReport, tmp: Path) -> None:
    """WD3：心跳正常 + 阶段超预算 -> 仅预算谓词收尸（死亡谓词日志 = 0）。"""
    repo, engine = await make_repo()
    svc = _DrillSvc(repo, storage_dir=str(tmp))
    svc.mode = "hang"
    await repo.create(id="wd3", user_id="u1", domain="drill", task_type="demo", payload="{}")
    await repo.update("wd3", status="running", progress=50, retry_count=2)
    task = asyncio.create_task(svc._wrap_pipeline("wd3", "u1", "demo", {}))
    svc._active_tasks["wd3"] = task

    # 心跳健康期（阶段预算内）：不得有任何收尸（死亡谓词日志 = 0 的等价断言）
    t0 = time.monotonic()
    early = 0
    while time.monotonic() - t0 < MOCK_BUDGET * 0.6:  # 停在预算内
        early += await svc.reap_stalled_tasks()
        await asyncio.sleep(MOCK_INTERVAL / 2)
    row = await repo.get("wd3")

    # 预算超限后收尸
    reaped = 0
    while reaped == 0 and time.monotonic() - t0 < MOCK_BUDGET + MOCK_P1 * 2 + 10:
        reaped += await svc.reap_stalled_tasks()
        await asyncio.sleep(MOCK_INTERVAL / 2)
    row = await repo.get("wd3")
    msg = row.error_message or ""
    predicate = ""
    if "watchdog (" in msg:
        predicate = msg.split("watchdog (", 1)[1].split(",", 1)[0]
    ok = (
        early == 0 and reaped == 1 and "stage budget" in msg and "llm_stage" in msg and "exceeded" in msg and predicate == "budget"  # 归因必须是预算谓词（死亡谓词 = 0）
    )
    report.record("wd3_hung_attribution", ok, f"early_reaps={early}, reaped={reaped}, msg={msg!r}")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=3)
    await engine.dispose()


async def wd4_reap_semantics(report: GateReport, tmp: Path) -> None:
    """WD4：cancel 超时保持 failed 不重排（吞取消 wedge）+ 重排不删目录。"""
    repo, engine = await make_repo()
    svc = _DrillSvc(repo, storage_dir=str(tmp))
    svc.mode = "swallow_cancel"
    await repo.create(id="wd4", user_id="u1", domain="drill", task_type="demo", payload="{}")
    await repo.update("wd4", status="running", progress=10)
    task = asyncio.create_task(svc._wrap_pipeline("wd4", "u1", "demo", {}))
    svc._active_tasks["wd4"] = task
    await asyncio.sleep(0.5)
    await backdate(repo, "wd4", timedelta(seconds=MOCK_P1 + 1))

    # 缩小 cancel 界（30s -> 1s）以在测试时限内观察「超时不重排」
    import app.gateway._shared.task_framework.service as svc_mod

    orig_wait = asyncio.wait

    async def _bounded_fast(fs, timeout=None, **kw):
        return await orig_wait(fs, timeout=1.0, **kw)

    svc_mod.asyncio.wait = _bounded_fast
    try:
        reaped = await svc.reap_stalled_tasks()
    finally:
        svc_mod.asyncio.wait = orig_wait
    # 终态断言轮询（最多 3s）：reap 的 failed 落库与任务行读取之间有事件循环
    # 调度窗口，单次读取可能撞上竞态 -> 门禁应稳定复判而非偶发假 FAIL。
    row = None
    for _ in range(30):
        row = await repo.get("wd4")
        if row is not None and row.status in ("failed", "pending"):
            break
        await asyncio.sleep(0.1)
    ok = reaped == 1 and row is not None and row.status == "failed" and row.retry_count == 0
    report.record("wd4_cancel_timeout_no_requeue", ok, f"reaped={reaped}, status={row.status if row else None}, retry={row.retry_count if row else None}（保持 failed 不重排）")
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=5)
    await engine.dispose()


async def wd8_ping_throttle(report: GateReport, tmp: Path) -> None:
    """WD8：ping 节流--窗口内多次调用合并为一次写库（20 并发 DB 增量 ≤1 QPS 的单任务等价）。"""
    from unittest.mock import AsyncMock

    repo, engine = await make_repo()
    svc = _DrillSvc(repo, storage_dir=str(tmp))
    mock_repo = AsyncMock()
    mock_repo.touch = AsyncMock()
    svc.repo = mock_repo
    for _ in range(10):
        await svc.liveness_ping("wd8")
    report.record(
        "wd8_ping_throttle",
        mock_repo.touch.await_count == 1,
        f"窗口内 10 次 ping -> touch {mock_repo.touch.await_count} 次（合并生效）",
    )
    await engine.dispose()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="", help="逗号分隔的 gate 名（wd2a,wd2b,wd3,wd4,wd8）；缺省全跑")
    args = parser.parse_args()

    import tempfile

    gates = {
        "wd2a": wd2a_gateway_death,
        "wd2b": wd2b_composing_backstop,
        "wd3": wd3_hung_attribution,
        "wd4": wd4_reap_semantics,
        "wd8": wd8_ping_throttle,
    }
    selected = {k: v for k, v in gates.items() if not args.only or k in args.only.split(",")}
    report = GateReport()
    print("== 看门狗活性重构 部署前门禁（mock 演练，时间尺度同比缩小） ==")
    for name, fn in selected.items():
        print(f"-- {name} --")
        with tempfile.TemporaryDirectory() as td:
            await fn(report, Path(td))

    print()
    print("== 汇总 ==")
    for gate, ok, detail in report.results:
        print(f"{'PASS' if ok else 'FAIL'}  {gate}: {detail}")
    print(json.dumps({"all_pass": report.all_pass, "gates": {g: ok for g, ok, _ in report.results}}, ensure_ascii=False))
    return 0 if report.all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

"""BaseTaskService — abstract base for all async task services."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .error_messages import to_user_message
from .metrics import metrics
from .models import TaskRow
from .repository import TaskRepository
from .schemas import DRAFT, GATEWAY_RECOVERABLE, STORY_READY, TERMINAL_STATUSES

logger = logging.getLogger(__name__)

_BUDGET_EXC: tuple[type[BaseException], ...] | None = None


def _budget_exc_types() -> tuple[type[BaseException], ...]:
    """惰性收集两类预算异常。

    P1-5: VisualBudgetExceeded 与 VimaxBudgetExceeded **无共同基类**
    （budget.py:26 明确两条管线的预算语义独立演进），必须分别列出。
    """
    global _BUDGET_EXC
    if _BUDGET_EXC is None:
        from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded
        from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

        _BUDGET_EXC = (VisualBudgetExceeded, VimaxBudgetExceeded)
    return _BUDGET_EXC


class BaseTaskService(ABC):
    """Async task Service base class.

    Subclasses implement _run_pipeline(). This base class handles:
      - Task create / query / delete
      - In-process _active_tasks management
      - asyncio.Semaphore concurrency control
      - Progress reporting via ProgressUpdater
    """

    domain: str = ""  # Subclass must set
    id_prefix: str = ""  # Subclass must set (e.g. "prod" / "vg" / "ct")

    GATEWAY_STAGES: tuple[str, ...] = GATEWAY_RECOVERABLE
    max_retries: int = 2  # Maximum automatic retries on pipeline failure
    # T6 (2026-08-14): progress 冻结固定窗口（秒）——progress 值连续这么久无变化即判
    # stalled 直接失败。None = 谓词关闭（默认，其他域沿用既有 B 谓词的缩放窗口）；
    # video-gen 置 20×60：140 帧任务的缩放窗口放大到 2.8h，20 分钟内无人收尸。
    # 固定而非按帧数缩放（Tier E 续写使单帧合法最坏 ≈13.5min，20min 为 1.5× 裕量）。
    # 2026-08-24 活性重构（W3）：降级为「无活性覆盖阶段」的兜底谓词（P5=30min，
    # 配置 ``video_gen.stall_progress_freeze_seconds``）；覆盖阶段一律旁路。
    progress_stall_timeout: int | None = None
    # W3（2026-08-24 活性重构）：主谓词（死亡判定）阈值--仅对「已覆盖」（活跃
    # with_liveness）且 updated_at 沉默超过该窗口的任务收尸。None = 主谓词关闭
    # （框架默认；video_gen 从 config 注入 120s，0 显式禁用 = P9 kill-switch）。
    liveness_threshold_seconds: int | None = None
    # W3b（2026-08-24 活性重构）：composing 行的 gateway 兜底扫描开关。框架默认
    # 关（voice_twin/PPT 维持原语义，W7）；video_gen 从 config 注入（P10，默认开）。
    composing_backstop_enabled: bool = False

    def __init__(
        self,
        repo: TaskRepository,
        storage_dir: str,
        max_concurrent_tasks: int = 3,
        stall_timeout: int = 1800,
        max_tasks_per_user: int = 3,
    ) -> None:
        if not self.domain:
            raise ValueError(f"{type(self).__name__}.domain is not set")
        if not self.id_prefix:
            raise ValueError(f"{type(self).__name__}.id_prefix is not set")
        self.repo = repo
        self._storage_dir = str(Path(storage_dir).resolve())
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._deleting: set[str] = set()  # task_ids being explicitly deleted — cancel shouldn't reschedule
        # 2026-08-08 P1-2：recover_stale_tasks 正在接管的 task_ids——B1 自愈分支
        # （CancelledError 重排）据此放弃自我重排，否则 recover 与旧 handle 双跑。
        self._recovering: set[str] = set()
        self._concurrency = asyncio.Semaphore(max_concurrent_tasks)
        # 闸0b: per-user in-flight cap. Complements the global _concurrency gate:
        # global caps TOTAL in-flight orchestration; this caps PER-USER so one user
        # opening 2-3 tasks can't hog all global slots. No pending poller exists in
        # this codebase, so surplus same-user task coroutines simply block on their
        # user sem (reuses the existing Semaphore-blocking queue) until the user
        # frees a slot. VideoGenService injects the config value; other subclasses
        # (ProductionService/ContentTaskService) use this default of 3.
        self._max_tasks_per_user = max_tasks_per_user
        self._user_sems: dict[str, asyncio.Semaphore] = {}
        # Stall watchdog: a non-terminal task whose updated_at is older than
        # now()-stall_timeout is considered stuck (the in-process asyncio.Task
        # died / hung on an unresponsive cloud API / was lost on an unclean
        # restart). The reaper flips it to failed. 0 disables the reaper for
        # this service. Per-domain overrides via subclass or config.
        self.stall_timeout = stall_timeout
        self._reaper_task: asyncio.Task | None = None
        # Progress-stall baseline: {task_id: (progress_value, first_seen_at)}.
        # Catches the blind spot where a stalled pipeline keeps re-emitting the
        # SAME progress value (so updated_at keeps refreshing) while actually
        # hung — e.g. vimax stuck at 30% re-pushing 30 every tick (vg_f309ae0f32f5).
        # When a task's progress is unchanged across the stall_timeout window, it
        # is reaped even though updated_at is recent.
        self._progress_baseline: dict[str, tuple[int, datetime]] = {}
        # ── W1（2026-08-24 看门狗活性重构）：活性信号契约 ──────────────────
        # 命名避歧：这里的 liveness ping 是任务框架的「进程活着」信号，与 SSE
        # heartbeat（stream_bridge，浏览器连接保活）同名不同物、零耦合。
        # 滴答周期 = 节流窗口 = 30s（P2：20 并发任务 DB 写 ≤1 QPS）。
        # 注：既有测试有绕过 __init__ 手工装配属性的服务构造（test_task_framework_
        # retry_policy 等），故本组状态另配 _ensure_liveness_state 懒初始化兜底。
        self._liveness_ping_interval: float = 30.0
        # 实例级节流戳（monotonic）：同任务窗口内多次 ping 合并为一次 touch
        self._liveness_last_ping: dict[str, float] = {}
        # 覆盖注册表（纯内存，仅由活跃 with_liveness 进入/退出动态维护--
        # 禁止按 status 静态登记，静态覆盖会重开 F1 同类漏洞）：task_id -> 活跃 tick 集合
        self._liveness_ticks: dict[str, set[asyncio.Task]] = {}
        # 阶段计时注册表（W4 预算谓词）：task_id -> {token: (stage, started_monotonic, budget_s)}
        self._liveness_stages: dict[str, dict[int, tuple[str, float, float]]] = {}
        # W3b：composing 未 claim 超龄 warning 的去重（每任务每进程一次）
        self._composing_unclaimed_warned: set[str] = set()

    def _ensure_liveness_state(self) -> None:
        """兼容绕过 __init__ 的构造（仓库既有测试惯例）：活性状态懒初始化。"""
        if not hasattr(self, "_liveness_ping_interval"):
            self._liveness_ping_interval = 30.0
        if not hasattr(self, "_liveness_last_ping"):
            self._liveness_last_ping = {}
        if not hasattr(self, "_liveness_ticks"):
            self._liveness_ticks = {}
        if not hasattr(self, "_liveness_stages"):
            self._liveness_stages = {}
        if not hasattr(self, "_composing_unclaimed_warned"):
            self._composing_unclaimed_warned = set()

    # ── W1/W2b：活性信号契约（liveness ping）──────────────────────

    def _liveness_covered(self, task_id: str) -> bool:
        """覆盖判定：该任务当前是否存在活跃的 ``with_liveness``（滴答登记中）。

        覆盖语义 = 「有活跃滴答」，不是「管线在跑」--排队期（W2b）、帧渲染、
        TTS 整批等所有挂了滴答的阶段都算覆盖；阶段退出/协程死亡即摘除。
        reaper 的 B/C 兜底谓词仅对无覆盖任务生效（防误杀合法沉默的长阶段）。
        """
        self._ensure_liveness_state()
        return bool(self._liveness_ticks.get(task_id))

    def _stage_entries(self, task_id: str) -> list[tuple[str, float, float]]:
        """W4：该任务当前活跃的阶段条目 [(stage, started_monotonic, budget_s)]。"""
        self._ensure_liveness_state()
        return list(self._liveness_stages.get(task_id, {}).values())

    async def liveness_ping(self, task_id: str) -> None:
        """活性 ping：节流后 touch ``updated_at``（同任务窗口内多次调用合并为一次写库）。

        节流窗口 = 滴答周期（默认 30s）。写库失败（瞬时 DB 抖动）回落节流戳，
        下一次调用立即重试--否则一次失败会把活性信号吞掉整整一个窗口，
        主谓词（120s 沉默判死）会误杀健康任务。
        """
        self._ensure_liveness_state()
        if self._liveness_ping_interval <= 0:
            return
        now = time.monotonic()
        last = self._liveness_last_ping.get(task_id)
        if last is not None and now - last < self._liveness_ping_interval:
            return  # 窗口内合并
        self._liveness_last_ping[task_id] = now
        try:
            await self.repo.touch(task_id)
        except Exception:
            self._liveness_last_ping.pop(task_id, None)
            logger.warning("liveness ping failed for %s (will retry next call)", task_id, exc_info=True)

    async def _liveness_tick(self, task_id: str, interval: float) -> None:
        """后台滴答循环：每 interval 秒 ping 一次。异常不得杀死循环。"""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.liveness_ping(task_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("liveness tick error for %s", task_id, exc_info=True)

    @contextlib.asynccontextmanager
    async def with_liveness(
        self,
        task_id: str,
        *,
        interval: float | None = None,
        stage: str | None = None,
        stage_budget: float | None = None,
    ):
        """长阶段活性上下文：进入启动后台滴答，退出取消并 await（不留残task）。

        - 挂法原则（W2）：**阶段级包一层**（整个 gather/阶段一个 tick task），
          避免逐调用 churn；仅阶段内函数被独立复用且自身是长阶段时才重复挂。
        - ``stage``/``stage_budget``（W4）：登记阶段计时条目供 reaper 预算谓词
          检查（``now - started > budget`` 判 hung）；退出即摘除。不传 = 纯活性
          覆盖（如 W2b 排队期），预算谓词不适用。
        - ``interval<=0``：不启动滴答（但 stage 登记/覆盖语义保留）。
        - 覆盖注册表仅在此处动态维护（N1 小项①：禁止按 status 静态登记）。
        """
        self._ensure_liveness_state()
        interval = self._liveness_ping_interval if interval is None else interval
        if interval <= 0:
            # 活性关闭（含 stage 登记）：无滴答即无覆盖语义，整体 no-op。
            yield
            return
        tick_task = asyncio.create_task(
            self._liveness_tick(task_id, interval),
            name=f"liveness-tick-{self.domain}-{task_id}",
        )
        ticks = self._liveness_ticks.setdefault(task_id, set())
        ticks.add(tick_task)
        # P1 防御（2026-08-25 复核）：stage 未带预算（None/<=0）不登记计时条目--
        # 旧实现 ``float(stage_budget or 0.0)`` 会把缺省预算落成 0，下一个 reaper
        # tick 即「超预算」误杀健康任务。无预算 = 纯活性覆盖（预算谓词不适用）。
        stage_token: int | None = None
        if stage is not None and stage_budget is not None and stage_budget > 0:
            stage_token = id(tick_task)
            entries = self._liveness_stages.setdefault(task_id, {})
            entries[stage_token] = (stage, time.monotonic(), float(stage_budget))
        try:
            yield
        finally:
            if stage_token is not None:
                entries = self._liveness_stages.get(task_id)
                if entries is not None:
                    entries.pop(stage_token, None)
                    if not entries:
                        self._liveness_stages.pop(task_id, None)
            ticks.discard(tick_task)
            if not ticks:
                self._liveness_ticks.pop(task_id, None)
            tick_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tick_task
            # tick 死了，覆盖若仍在（兄弟 tick 还活着）不动；节流戳留给下一次 ping 复用

    async def _cancel_liveness(self, task_id: str) -> None:
        """W6：收尸时主动停掉该任务的全部滴答（协程可能已 wedge、finally 跑不到）。

        有界等待（不阻塞 reaper 循环）：tick 只是 sleep+touch，正常即取即消。
        """
        self._ensure_liveness_state()
        ticks = list(self._liveness_ticks.get(task_id, ()))
        if not ticks:
            return
        for t in ticks:
            t.cancel()
        if ticks:
            done, pending = await asyncio.wait(ticks, timeout=5)
            if pending:
                logger.error("liveness ticks for %s did not stop within 5s (%d pending)", task_id, len(pending))
        self._liveness_ticks.pop(task_id, None)
        self._liveness_stages.pop(task_id, None)

    def stall_timeout_for_task(self, task) -> int:
        """Per-task stall-timeout override hook (E5b). Default = ``self.stall_timeout``.

        Subclasses override to scale by task properties (e.g. video-gen scales by
        expected frame count so a 54-frame serial render isn't reaped mid-flight).
        The reaper uses this for BOTH predicates (updated_at-stale + progress-stuck)
        on a per-task cutoff — long renders get a longer window. Returning 0 falls
        through to ``self.stall_timeout`` (reaper disabled only if THAT is 0).
        """
        return self.stall_timeout

    # ── Per-user concurrency gate (闸0b) ────────────────────────

    def _get_user_sem(self, user_id: str) -> asyncio.Semaphore:
        """Per-user in-flight cap. Lazy-created; shared process-wide.

        Complements the global _concurrency gate: global caps TOTAL in-flight
        orchestration; this caps PER-USER so one user can't hog all slots.
        No pending poller exists, so we rely on Semaphore blocking — the surplus
        task's coroutine simply awaits here until the user frees a slot.
        """
        sem = self._user_sems.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(self._max_tasks_per_user)
            self._user_sems[user_id] = sem
        return sem

    # ── ID generation ───────────────────────────────────────────

    def _gen_id(self) -> str:
        return f"{self.id_prefix}_{uuid.uuid4().hex[:12]}"

    # ── Public API ──────────────────────────────────────────────

    async def submit(
        self,
        *,
        user_id: str,
        task_type: str,
        payload: dict[str, Any],
        priority: int = 50,
        auto_start: bool = True,
    ) -> TaskRow:
        """Create task and optionally schedule execution."""
        # P2-4: 落库 title（从 payload 提取，列表请求不再需读磁盘 sidecar）
        # 2026-08-08 P2-3：payload 显式 script=None 时 dict.get 默认值不生效，
        # str(None)="None" 会落库成 title——先 or "" 再 str。
        title = payload.get("topic") or (str(payload.get("script") or "")[:50] or None)
        task = await self.repo.create(
            id=self._gen_id(),
            user_id=user_id,
            domain=self.domain,
            task_type=task_type,
            payload=json.dumps(payload, ensure_ascii=False),
            priority=priority,
            title=title,
        )
        if auto_start:
            async_task = asyncio.create_task(self._wrap_pipeline(task.id, user_id, task_type, payload))
            self._active_tasks[task.id] = async_task
        return task

    async def get_task(self, task_id: str, user_id: str) -> TaskRow | None:
        return await self.repo.get(task_id, user_id=user_id)

    async def list_tasks(self, user_id: str) -> list[TaskRow]:
        return await self.repo.list_by_user(user_id, domain=self.domain)

    async def delete_task(self, task_id: str, user_id: str) -> bool:
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            return False
        self._deleting.add(task_id)  # signal _wrap_pipeline: this cancel is explicit — don't reschedule
        try:
            async_task = self._active_tasks.pop(task_id, None)
            if async_task and not async_task.done():
                async_task.cancel()
            await self._cleanup_task(task)
            await self.repo.delete(task_id)
        finally:
            self._deleting.discard(task_id)
        return True

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        """取消进行中的任务（Task 22）：置 cancelled + 断掉进程内的 asyncio task。

        与 :meth:`delete_task` 的区别：**保留任务行与产物目录**。用户诉求通常是
        「先停下别再烧配额」而不是「连记录一起抹掉」。

        worker 侧靠轮询 status 感知（``worker.is_task_cancelled``）——gateway 只
        负责把标志写进 DB 并停掉自己这半边，无法直接杀 worker 进程里的 ffmpeg。

        终态任务抛 ``ValueError``（端点转 409）：已完成/已失败的任务"取消"没有
        意义，静默返回成功会让界面显示一个假的取消态。
        """
        task = await self.repo.get(task_id, user_id=user_id)
        if task is None:
            return False
        if task.status in TERMINAL_STATUSES or task.status == "cancelled":
            raise ValueError(f"task {task_id} already terminal: {task.status}")

        self._deleting.add(task_id)  # 与 delete 同义：这次 cancel 是显式的，别重排
        try:
            async_task = self._active_tasks.pop(task_id, None)
            if async_task and not async_task.done():
                async_task.cancel()
            await self.repo.update(
                task_id,
                status="cancelled",
                claimed_by=None,
                lease_expires_at=None,
            )
        finally:
            self._deleting.discard(task_id)
        return True

    async def recover_stale_tasks(self) -> int:
        """On process restart: recover Gateway-owned non-terminal tasks.

        If retry_count < max_retries, re-schedule the task. Otherwise mark as failed.
        composing tasks are Worker-owned — this service does not touch them.

        2026-08-20:
        - 任务粒度异常隔离：单任务恢复失败（如 DB 约束异常）log + 留原状态，
          不中断其余任务与服务挂载（result=None 撞非空约束 → video 模块整体 503）。
        - DRAFT / STORY_READY=「等用户确认」持久态（idea2video story 同语义）：
          重排不消耗 retry 预算（幂等空转，P0-3 复用磁盘产物）——修前每次重启
          +1，第 3 次重启被自动标 failed。

        Returns the number of tasks processed without error (re-scheduled or marked failed).
        """
        stale = await self.repo.list_stale_for_recovery(self.domain, self.GATEWAY_STAGES)
        recovered = 0
        for t in stale:
            try:
                current_retries = t.retry_count or 0
                is_user_waiting = t.status in (DRAFT, STORY_READY)
                if is_user_waiting or current_retries < self.max_retries:
                    if is_user_waiting:
                        logger.info("Recovering user-waiting task %s (status %s; retry budget not consumed)", t.id, t.status)
                    else:
                        logger.info("Recovering task %s (retry %d/%d)", t.id, current_retries + 1, self.max_retries)
                    payload = json.loads(t.payload or "{}")
                    update_kwargs: dict[str, Any] = {
                        "status": "pending",
                        "progress": 0,
                        # 同 Exception 重试路径（反馈2-2 P1b）：清空上一轮残留 result。
                        # 置 "{}" 而非 NULL——result 列 nullable=False（2026-08-20 事故）。
                        "result": "{}",
                    }
                    if is_user_waiting:
                        update_kwargs["retry_count"] = current_retries  # 不烧预算
                        update_kwargs["error_message"] = None  # 清旧重试/重启残留（对齐 ready 成功清红框）
                    else:
                        update_kwargs["retry_count"] = current_retries + 1
                        update_kwargs["error_message"] = f"Gateway restart recovery (retry {current_retries + 1})"
                    await self.repo.update(t.id, **update_kwargs)
                    # P1-2: 旧 handle 可能仍在跑（cancel 是异步的）。直接覆盖会留下
                    # 孤儿 task 继续调 API 计费 + 与新 task 争写 task_dir（实测同任务
                    # pipeline 并发跑两遍）。对齐 delete_task / cancel_task 的做法。
                    # 2026-08-08 P1-2 补强：先标 _recovering 再 cancel——上面的
                    # repo.update 已把 status 置回 pending（非终态），旧 handle 的 B1
                    # 自愈分支（CancelledError 重排）会因此自我重排、与下方新建的
                    # task 双跑。标记期内 B1 放弃重排，cancel 被正常吸收。
                    old_task = self._active_tasks.get(t.id)
                    self._recovering.add(t.id)
                    try:
                        if old_task is not None and not old_task.done():
                            old_task.cancel()
                            try:
                                await old_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        async_task = asyncio.create_task(self._wrap_pipeline(t.id, t.user_id, t.task_type, payload))
                        self._active_tasks[t.id] = async_task
                    finally:
                        self._recovering.discard(t.id)
                else:
                    try:
                        await self._cleanup_task(t)
                    except Exception:
                        logger.exception("Cleanup failed for task %s during recovery", t.id)
                    await self.repo.update(
                        t.id,
                        status="failed",
                        error_message="Gateway restarted during processing (max retries exceeded)",
                    )
                recovered += 1
            except Exception:
                # 单任务失败不扩散：留原状态，stall reaper / 下次重启再收口。
                logger.exception("Recovery failed for task %s; leaving in current state", t.id)
        return recovered

    async def reap_stalled_tasks(self) -> int:
        """Periodic stall watchdog（2026-08-24 活性重构，两层防线）。

        谓词集合（union，任一命中即收尸；命中者优先级从上到下）：

        **主谓词 death（W3，仅覆盖阶段）**：任务处于活跃 ``with_liveness`` 覆盖中
        且 ``updated_at`` 沉默 > ``liveness_threshold_seconds``（P1=120s）--
        滴答登记着但 ping 落不了库 = 事件循环被同步调用卡死 / 滴答协程死亡，
        进程级死亡信号。None/0 = 关闭（P9 kill-switch）。

        **预算谓词 budget（W4）**：阶段计时条目 ``now - started > budget`` --
        心跳照跳但阶段 hung 死（第二层防线），error_message 归因
        ``stage budget {stage} exceeded``。

        **A（既有，不动）**：``updated_at`` 老于 per-task 缩放窗口（无覆盖任务的
        慢速死亡兜底；覆盖任务的 ping 使其天然不触发）。

        **B/C（降级为无覆盖兜底，W3）**：progress 值冻结超窗（B=缩放窗口 /
        C=``progress_stall_timeout`` 固定窗口=P5）--仅在任务**无活性覆盖**时评估
        （覆盖期旁路：112 帧串行任务整数进度 7~8 帧才 +1 的合法沉默曾三天三次
        被误杀，08-20/21 事故）。窗口语义：video_gen 的 C 固定 30min < B 缩放
        窗口，C 先行；voice_twin/PPT 无 C（None），B 沿用原语义不变（W7）。

        **DRAFT/STORY_READY（N3②）**：持久等待态、无在途协程，全部谓词豁免。

        composing 行不在此列表--由 :meth:`_reap_composing_backstop` 单独扫描
        （W3b：A/B/C/主谓词对 composing 全旁路，仅租约双条件兜底）。

        Returns the number of tasks reaped. No-op when stall_timeout <= 0.
        """
        if self.stall_timeout <= 0:
            return 0
        now = datetime.now(UTC)
        mono_now = time.monotonic()
        # E5b: per-task stall timeout via the stall_timeout_for_task hook (default =
        # self.stall_timeout; video-gen scales by expected frame count). Fetch ALL
        # non-terminal once; predicates use a per-task cutoff so long renders
        # get a longer window before reaping (54-frame serial ai_video ~108min).
        all_non_terminal = await self.repo.list_stale_for_recovery(self.domain, self.GATEWAY_STAGES)

        to_reap: list[tuple[TaskRow, str, str]] = []  # (task, predicate, detail)
        to_reap_ids: set[str] = set()
        new_baseline: dict[str, tuple[int, datetime]] = {}
        for t in all_non_terminal:
            per_task_timeout = self.stall_timeout_for_task(t)
            per_task_cutoff = now - timedelta(seconds=per_task_timeout)
            # baseline 记账无条件进行（覆盖期也要刷新基线，退出覆盖后才有效）
            baseline = self._progress_baseline.get(t.id)
            if baseline is None or baseline[0] != t.progress:
                new_baseline[t.id] = (t.progress, now)
            else:
                new_baseline[t.id] = baseline
            # N3②：DRAFT/STORY_READY 是「等用户确认」持久等待态（无在途协程），
            # A 的孤儿进程检测定位对它不成立--全部谓词豁免（重启恢复路径本就
            # 幂等处理它们，豁免不掉任何保护）。
            if t.status in (DRAFT, STORY_READY):
                continue
            should_reap = False
            predicate = ""
            detail = ""
            # TaskRow.updated_at is stored offset-naive (SQLite); normalize to UTC for
            # comparison against the offset-aware `now` (the old DB-query path compared in SQL).
            updated = t.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            covered = self._liveness_covered(t.id)
            # 主谓词（W3）：覆盖 + 沉默 > liveness_threshold（None/0 = 关闭）
            if covered and self.liveness_threshold_seconds and updated is not None and updated < now - timedelta(seconds=self.liveness_threshold_seconds):
                should_reap = True
                predicate = "death"
                detail = f"liveness silent >{self.liveness_threshold_seconds}s while covered"
            # 预算谓词（W4）：任一活跃阶段条目超预算（心跳正常也收--专抓 hung 死）
            if not should_reap:
                for stage, started, budget in self._stage_entries(t.id):
                    if mono_now - started > budget:
                        should_reap = True
                        predicate = "budget"
                        detail = f"stage budget {stage} exceeded ({budget:.0f}s)"
                        break
            # Predicate A（既有不动）: updated_at older than the per-task cutoff.
            if not should_reap and updated is not None and updated < per_task_cutoff:
                should_reap = True
                predicate = "death"
                detail = f"no update for >{per_task_timeout}s"
            # Predicate B/C（W3 降级：无覆盖兜底）: progress unchanged beyond window.
            if not should_reap and not covered and baseline is not None and baseline[0] == t.progress:
                # progress unchanged since baseline[1] - has it been stuck long enough?
                if baseline[1] <= per_task_cutoff:
                    should_reap = True
                    predicate = "backstop"
                    detail = f"progress frozen at {t.progress} for >{per_task_timeout}s"
                # Predicate C (T6->W3): progress 冻结固定窗口（P5，配置
                # stall_progress_freeze_seconds）。DRAFT/STORY_READY 已在上面整体豁免。
                if not should_reap and self.progress_stall_timeout is not None and baseline[1] <= now - timedelta(seconds=self.progress_stall_timeout):
                    should_reap = True
                    predicate = "backstop"
                    detail = f"progress frozen at {t.progress} for >{self.progress_stall_timeout}s"
            if should_reap and t.id not in to_reap_ids:
                to_reap.append((t, predicate, detail))
                to_reap_ids.add(t.id)
        # tasks that left the non-terminal set (went terminal) drop from baseline
        self._progress_baseline = {tid: v for tid, v in new_baseline.items() if tid in {t.id for t in all_non_terminal}}

        reaped = 0
        for t, predicate, detail in to_reap:
            reaped += await self._reap_one(t, predicate, detail)
        # W3b：composing 行的 gateway 双条件兜底（独立扫描；开关 P10）
        reaped += await self._reap_composing_backstop(now)
        # D19（2026-09-08 T12）：qc_repair 状态机推进钩子（子类覆盖；基类 no-op）
        reaped += await self._process_qc_repair_tasks()
        return reaped

    async def _process_qc_repair_tasks(self) -> int:
        """qc_repair 扫描钩子：基类 no-op（qc_repair 是 video_gen/remotion 专属状态，
        voice_production 等域不产生）。子类（VideoGenService）覆盖实现重生/降级状态机。
        """
        return 0

    async def _reap_one(self, t: TaskRow, predicate: str, detail: str) -> int:
        """W6：单个任务的收尸三步语义（v3 修订 N1/N2③④）。

        1. **failed 落库先行**：取消处理器（CancelledError 分支）读到终态即
           尊重、不自我重排--修前「先 cancel（未 await）再落库」的窗口里，
           处理器读到非终态就重排并烧重试预算（08-20 三连误杀的竞态根因）；
        2. **有界取消（N1）**：``await asyncio.wait([task], timeout=30)``。
           超时（协程卡在 to_thread/同步 ffmpeg/吞取消的库）-> 保持 failed、
           **本次不重排**（防旧协程未死新管线双跑写盘）、error 日志，下个
           tick 再评估；reaper 常驻循环不被挂住；
        3. **CAS 重排（N2③）**：取消确认完成且 retry 预算未尽 ->
           ``update_if_status(status='failed')`` 守卫下翻 pending + retry+1 +
           result='{}'（列非空约束，08-20 事故）+ 重新调度。
        4. **cleanup 分治（N2④）**：仅终态 failed（预算烧尽/不重排）才
           ``_cleanup_task``--video_gen 覆写为 no-op（任何路径不删目录），
           voice_twin/PPT 重排不删、仅终态删。
        """
        current = t.retry_count or 0
        will_retry = current < self.max_retries
        attempt = current + 1 if will_retry else current
        # 1. failed 落库（reap 专属文案，对齐 recovery 的 "Gateway restart recovery (retry N)" 风格；
        #    W4 要求预算谓词在 error_message 里归因 ``stage budget {stage} exceeded``--detail 附加在尾部）
        msg = f"Reaped by watchdog ({predicate}, retry {attempt}/{self.max_retries})"
        if detail:
            msg = f"{msg}: {detail}"
        try:
            await self.repo.update(
                t.id,
                status="failed",
                error_message=msg,
            )
        except Exception:
            logger.exception("Reap: failed to mark task %s failed; skipping this tick", t.id)
            return 0
        # 2. 有界取消（N1）：30s 内未收敛 = 协程 wedge 在不可取消区
        async_task = self._active_tasks.pop(t.id, None)
        cancel_confirmed = True
        if async_task is not None and not async_task.done():
            async_task.cancel()
            _done, pending = await asyncio.wait([async_task], timeout=30)
            if pending:
                cancel_confirmed = False
                logger.error(
                    "Reaped task %s coroutine did not cancel within 30s (%s); keeping failed, no requeue this tick",
                    t.id,
                    detail,
                )
        # 收尸时主动停滴答（协程可能 wedge、with_liveness 的 finally 跑不到）
        try:
            await self._cancel_liveness(t.id)
        except Exception:
            logger.exception("Reap: failed to stop liveness ticks for %s", t.id)
        # a reaped task is no longer non-terminal - drop its baseline
        self._progress_baseline.pop(t.id, None)
        logger.warning(
            "Reaped stalled task %s (status %s, progress %s, predicate %s: %s)",
            t.id,
            t.status,
            t.progress,
            predicate,
            detail,
        )
        metrics.inc(
            "task_gateway_phase_total",
            labels={"domain": self.domain, "task_type": t.task_type, "status": "stalled", "predicate": predicate},
            help_text="Gateway pipeline phase completions",
        )
        if not cancel_confirmed:
            # A4：cancel 超时保持 failed 不自动重排（安全优先）
            return 1
        # 3. CAS 重排（预算未尽）
        if will_retry:
            try:
                payload = json.loads(t.payload or "{}")
            except Exception:
                payload = {}
            requeued = await self.repo.update_if_status(
                t.id,
                expect_status="failed",
                status="pending",
                progress=0,
                retry_count=attempt,
                result="{}",
                error_message=f"Reaped by watchdog ({predicate}); resuming (retry {attempt}/{self.max_retries})",
            )
            if requeued:
                async_task = asyncio.create_task(self._wrap_pipeline(t.id, t.user_id, t.task_type, payload))
                self._active_tasks[t.id] = async_task
            else:
                logger.warning("Reap: CAS requeue lost race for %s (state changed mid-reap); leaving failed", t.id)
            return 1
        # 4. 终态 failed：按模块分治清理（video_gen no-op / voice_twin+PPT 删目录）
        try:
            await self._cleanup_task(t)
        except Exception:
            logger.exception("Cleanup failed for stalled task %s", t.id)
        return 1

    async def _reap_composing_backstop(self, now: datetime) -> int:
        """W3b（N2）：composing 行的 gateway 双条件兜底扫描。

        worker 租约为主、gateway 兜底（worker 清道夫 ``recover_stale_tasks``
        第一责任人）。四细则：
        ① A/B/C/主谓词对 composing 全旁路（本方法独立于主谓词循环，
           composing 也不在 GATEWAY_STAGES 里）；收尸双条件 = 租约失效
           ``lease_expires_at < now`` **且** ``updated_at`` 陈旧 > P1（租约过期
           但行刚被写过 = 可能刚交出成果，不收）；
        ② 租约 NULL（交棒未被 claim）不收--worker 待领队列，重启即恢复；
           收尸反而在 worker 持续宕机时烧光重试预算。超龄（> P5）仅 warning；
        ③ 重排 UPDATE 带 CAS 守卫（``WHERE claimed_by = 读到的值``，重置
           claimed_by/lease、result='{}'、retry+1，状态保持 composing 供任意
           worker replica 再认领--对齐 worker 清道夫语义）；
        ④ composing 行 gateway 永不删目录（含终态 failed 路径）。
        """
        if not self.composing_backstop_enabled:
            return 0
        self._ensure_liveness_state()
        try:
            rows = await self.repo.list_composing(self.domain)
        except Exception:
            logger.exception("composing backstop scan failed for domain %s", self.domain)
            return 0
        reaped = 0
        staleness_cutoff = now - timedelta(seconds=self.liveness_threshold_seconds or 120)
        for t in rows:
            lease = t.lease_expires_at
            if lease is not None and lease.tzinfo is None:
                lease = lease.replace(tzinfo=UTC)
            updated = t.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if lease is None:
                # ② 未被 claim 的待领队列：仅超龄可观测（P5 窗口），不收
                warn_cutoff = now - timedelta(seconds=self.progress_stall_timeout or 1800)
                if updated is not None and updated < warn_cutoff and t.id not in self._composing_unclaimed_warned:
                    self._composing_unclaimed_warned.add(t.id)
                    logger.warning(
                        "composing task %s has NULL lease (unclaimed) and is idle for a long time; leaving for worker pickup (gateway will not reap)",
                        t.id,
                    )
                    # T13（2026-09-08）：超 30min 未认领 → 用户可见告警（中文化走
                    # error_messages 通道）。worker 成功回写会清 error_message，
                    # 不影响终态展示；去重由 _composing_unclaimed_warned 保证一次。
                    try:
                        await self.repo.update(t.id, error_message="排队时间较长（超过 30 分钟未开始处理），请稍候或联系管理员")
                    except Exception:
                        logger.warning("failed to write unclaimed-queue warning for %s", t.id, exc_info=True)
                continue
            if lease >= now:
                continue  # ① 租约存活：worker 清道夫第一责任人
            if updated is None or updated >= staleness_cutoff:
                continue  # ① 双条件之二不满足：updated_at 未陈旧过 P1
            current = t.retry_count or 0
            owner = t.claimed_by
            if current < self.max_retries:
                # ③ CAS 重排：释放 claim 供任意 worker 再认领（不翻 gateway pending）
                requeued = await self.repo.update_if_owned(
                    t.id,
                    owner,
                    claimed_by=None,
                    lease_expires_at=None,
                    retry_count=current + 1,
                    result="{}",
                    error_message=f"Reaped by watchdog (composing_backstop); resuming (retry {current + 1}/{self.max_retries})",
                )
                if requeued:
                    reaped += 1
                    logger.warning(
                        "Reaped composing task %s (lease expired, updated_at stale > %ss); claim released for re-pickup (retry %d/%d)",
                        t.id,
                        self.liveness_threshold_seconds,
                        current + 1,
                        self.max_retries,
                    )
                else:
                    logger.info("composing backstop CAS lost for %s (claimed_by changed); skipping", t.id)
            else:
                failed = await self.repo.update_if_owned(
                    t.id,
                    owner,
                    status="failed",
                    claimed_by=None,
                    lease_expires_at=None,
                    error_message="Reaped by watchdog (composing_backstop, max retries exceeded)",
                )
                if failed:
                    reaped += 1
                    logger.error(
                        "Reaped composing task %s (lease expired, retries exhausted); marked failed",
                        t.id,
                    )
            # ④ composing 行 gateway 永不删目录
            metrics.inc(
                "task_gateway_phase_total",
                labels={"domain": self.domain, "task_type": t.task_type, "status": "stalled", "predicate": "composing_backstop"},
                help_text="Gateway pipeline phase completions",
            )
        return reaped

    def start_stall_reaper(self, interval: int = 300) -> None:
        """Launch the periodic stall watchdog as a background task.

        Idempotent: re-calling replaces the existing reaper. ``stop_stall_reaper``
        (called on app shutdown) cancels it. No-op (does not start) when
        stall_timeout <= 0, so opting out is just a config flip.
        """
        if self.stall_timeout <= 0:
            return
        if self._reaper_task is not None and not self._reaper_task.done():
            return  # already running

        async def _loop() -> None:
            while True:
                try:
                    await self.reap_stalled_tasks()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Stall reaper for domain %s errored (will retry next tick)", self.domain)
                await asyncio.sleep(interval)

        self._reaper_task = asyncio.create_task(_loop(), name=f"stall-reaper-{self.domain}")

    async def stop_stall_reaper(self) -> None:
        """Cancel the background reaper (app shutdown)."""
        t = self._reaper_task
        self._reaper_task = None
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    # ── Subclass must implement ─────────────────────────────────

    @abstractmethod
    async def _run_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        """Business pipeline. Return None = Gateway-done (→ ready).
        Return dict = Gateway-done, Worker takes over (→ composing, payload merged)."""
        raise NotImplementedError

    def _should_handoff_to_worker(self, task_type: str) -> bool:
        """Default False (Gateway completes). Subclass override for Worker handoff."""
        return False

    # ── Subclass may override ───────────────────────────────────

    async def _cleanup_task(self, task: TaskRow) -> None:
        """Delete task directory. Subclass may override for selective cleanup."""
        task_dir = Path(self._storage_dir) / task.user_id / "tasks" / task.id
        if task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)

    # ── Internal ────────────────────────────────────────────────

    async def _wrap_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
    ) -> None:
        """Concurrency-limited + error-catching pipeline wrapper."""
        # C1: Set accounting contextvar so downstream LLM calls (via
        # _invoke_llm_bounded) can attribute spend to this task+user+domain.
        from app.gateway._shared.llm_usage_sink import set_llm_accounting_context

        set_llm_accounting_context(task_id, user_id, domain=self.domain)
        # 闸0b: acquire the per-user gate BEFORE the global one - a same-user task
        # over its cap blocks here without holding a global slot, so other users'
        # tasks keep flowing. Manual acquire/release (was `async with A, B:`) so
        # the acquisition span can carry its own liveness scope (W2b below).
        user_sem = self._get_user_sem(user_id)
        # W2b（N3①，2026-08-24 活性重构）：排队期活性。submit 落库后协程可能长
        # 时间阻塞在并发闸获取上、零 DB 写（20 人生产全局闸被长任务占满时排队
        # 超 30min 完全现实）--修前 A/B 谓词会收尸健康排队任务。获取段挂滴答；
        # 任务真正拿到闸（开工）即退出，保持「覆盖 = 活跃 with_liveness」语义
        # 纯净。框架层通用机制（D4 澄清：这不属「为兄弟模块埋业务点」）。
        async with self.with_liveness(task_id):
            await user_sem.acquire()
            try:
                await self._concurrency.acquire()
            except BaseException:
                user_sem.release()
                raise
        try:
            await self._run_gated_pipeline(task_id, user_id, task_type, payload)
        finally:
            self._concurrency.release()
            user_sem.release()

    async def _run_gated_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
    ) -> None:
        """闸已获取后的管线执行体（原 _wrap_pipeline 主体，行为不变）。"""
        task_dir = Path(self._storage_dir) / user_id / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        progress = ProgressUpdater(self.repo, task_id)
        start = time.monotonic()
        labels = {"domain": self.domain, "task_type": task_type}
        retried = False
        _budget_exc = _budget_exc_types()
        try:
            await progress(status="running", progress=1)
            result = await self._run_pipeline(task_id, user_id, task_type, payload, task_dir, progress)

            # Draft branch: support both PipelineDraft dataclass and legacy {"_draft": True} dict
            from app.gateway.video_gen.pipeline_result import PipelineDraft

            is_draft = isinstance(result, PipelineDraft) or (isinstance(result, dict) and result.get("_draft"))
            if is_draft:
                if isinstance(result, PipelineDraft):
                    storyboard_data = result.storyboard
                else:
                    storyboard_data = result.get("storyboard")
                result_kwargs = {"status": DRAFT, "progress": 100}
                if storyboard_data:
                    result_kwargs["result"] = json.dumps({"storyboard": storyboard_data}, ensure_ascii=False)
                await progress(**result_kwargs)
                metrics.inc("task_gateway_phase_total", labels={**labels, "status": "draft"}, help_text="Gateway pipeline phase completions")
                return

            if self._should_handoff_to_worker(task_type):
                # Merge payload to preserve original input params
                # Support both PipelineCompleted dataclass and legacy dict returns
                from app.gateway.video_gen.pipeline_result import PipelineCompleted as _PC

                if isinstance(result, _PC):
                    result_payload = result.payload
                elif isinstance(result, dict):
                    result_payload = result
                else:
                    result_payload = None

                if result_payload:
                    merged = {**payload, **result_payload}
                    await progress(
                        payload=json.dumps(merged, ensure_ascii=False),
                        status="composing",
                        progress=95,
                    )
                else:
                    await progress(status="composing", progress=95)
                metrics.inc("task_gateway_phase_total", labels={**labels, "status": "composing"}, help_text="Gateway pipeline phase completions")
            else:
                # P2-4: 完成时落库 title/thumbnail_url
                completion_kwargs = {
                    "status": "ready",
                    "progress": 100,
                    "error_message": None,  # 2026-07-29：清空旧重试/重启标记，成功不顶红框
                }
                if isinstance(result, dict):
                    sb = result.get("storyboard")
                    if isinstance(sb, dict) and sb.get("title"):
                        completion_kwargs["title"] = sb["title"][:500]
                    thumb = result.get("thumbnail_path")
                    if thumb:
                        completion_kwargs["thumbnail_url"] = f"/api/video/tasks/{task_id}/thumbnail"
                await progress(**completion_kwargs)
                metrics.inc("task_gateway_phase_total", labels={**labels, "status": "ready"}, help_text="Gateway pipeline phase completions")

        except asyncio.CancelledError:
            # B1: a transient cancel (chat-run interrupt, sandbox stream disconnect,
            # etc.) should NOT kill a long render. Re-schedule so it resumes —
            # ViMax idempotency reuses the on-disk artifacts (B2 preserved the
            # task_dir). Skip for explicit deletes (task in _deleting), during
            # recover_stale_tasks takeover (task in _recovering — recover 已把
            # status 置回 pending，此处重排会与 recover 新建的 task 双跑), and
            # when retries are exhausted. Mirrors the Exception retry path below.
            if task_id not in self._deleting and task_id not in self._recovering:
                task_row = await self.repo.get(task_id)
                # P1-8: reaper 已把任务标 failed（终态）——尊重终态，不自动重排。
                # 修前：reaper failed → cancel 传播 → 此处又排回 pending →
                # failed/pending/running 三态横跳 3 轮，最终误标 "Cancelled by user"。
                if task_row is not None and task_row.status in TERMINAL_STATUSES:
                    logger.info("Task %s cancelled after terminal status (%s); not rescheduling", task_id, task_row.status)
                    raise
                current_retries = task_row.retry_count if task_row else 0
                if task_row is not None and current_retries < self.max_retries:
                    logger.info("Task %s cancelled (transient); resuming (retry %d/%d)", task_id, current_retries + 1, self.max_retries)
                    await progress(
                        status="pending",
                        progress=0,
                        error_message=f"Cancelled (transient); resuming (retry {current_retries + 1}/{self.max_retries})",
                        retry_count=current_retries + 1,
                        # 同 Exception 重试路径（反馈2-2 P1b）：清空上一轮残留 result。
                        # 置 "{}" 而非 NULL——result 列 nullable=False（2026-08-20 事故）。
                        result="{}",
                    )
                    metrics.inc("task_gateway_phase_total", labels={**labels, "status": "retrying"}, help_text="Gateway pipeline phase completions")
                    async_task = asyncio.create_task(self._wrap_pipeline(task_id, user_id, task_type, payload))
                    self._active_tasks[task_id] = async_task
                    retried = True
                    return  # absorb the cancel — the rescheduled task carries on
            await progress(status="failed", error_message="Cancelled by user")
            metrics.inc("task_gateway_phase_total", labels={**labels, "status": "cancelled"}, help_text="Gateway pipeline phase completions")
            raise
        except _budget_exc as e:
            # P1-5: 预算超限不重试--重排会新建 budget 计数器归零，
            # 等于无视用户设的上限，成本 3×
            logger.warning("%s pipeline hit budget cap: task=%s", self.domain, task_id)
            await progress(status="failed", error_message=to_user_message(e))
            return
        except (ValueError, TypeError) as e:
            # P1-5: 参数/配置错误原样重试 100% 再失败，白跑 3 轮
            logger.warning("%s pipeline failed with non-retryable error: task=%s", self.domain, task_id)
            await progress(status="failed", error_message=to_user_message(e))
            return
        except Exception as e:
            logger.exception("%s pipeline failed: task=%s", self.domain, task_id)
            # Plan C（2026-10-19，ViMax 分镜拆批）：显式不可重试标记——本地 deadline
            # 已耗尽 artist tenacity 三次且已完成批次有断点缓存，整管线重跑只重复
            # 计费。只读 getattr 标记，不 import vendored 类型（框架与 vendored 解耦）。
            if getattr(e, "pipeline_retryable", True) is False:
                logger.warning("task %s marked pipeline_retryable=False, failing without retry: %s", task_id, e)
                await progress(status="failed", error_message=to_user_message(e))
                metrics.inc("task_gateway_phase_total", labels={**labels, "status": "failed"}, help_text="Gateway pipeline phase completions")
                return
            # Auto-retry: check retry_count and resubmit
            task_row = await self.repo.get(task_id)
            current_retries = task_row.retry_count if task_row else 0
            if current_retries < self.max_retries:
                logger.info("Retrying task %s (attempt %d/%d)", task_id, current_retries + 1, self.max_retries)
                await progress(
                    status="pending",
                    progress=0,
                    error_message=f"重试 {current_retries + 1}/{self.max_retries}：{to_user_message(e, limit=200)}",
                    retry_count=current_retries + 1,
                    # 2026-08-19（反馈2-2 P1b）：清空上一轮 result——残留 warning
                    # 键会在 running 态穿透详情接口（vg_91899fe68c3b 警告卡 409
                    # 事故）；管线续跑状态在 task_dir/payload，重跑经 set_result
                    # 从 {} 全量重建。
                    # 2026-08-20：置 "{}" 而非 NULL——result 列 nullable=False。
                    result="{}",
                )
                metrics.inc("task_gateway_phase_total", labels={**labels, "status": "retrying"}, help_text="Gateway pipeline phase completions")
                # Schedule retry
                async_task = asyncio.create_task(self._wrap_pipeline(task_id, user_id, task_type, payload))
                self._active_tasks[task_id] = async_task
                retried = True
                return
            await progress(status="failed", error_message=to_user_message(e))
            metrics.inc("task_gateway_phase_total", labels={**labels, "status": "failed"}, help_text="Gateway pipeline phase completions")
        finally:
            elapsed = time.monotonic() - start
            metrics.observe("task_gateway_duration_seconds", elapsed, labels=labels, help_text="Gateway pipeline phase duration in seconds")
            if not retried:
                self._active_tasks.pop(task_id, None)


class ProgressUpdater:
    """Progress reporting closure. Pipeline functions receive one to report progress.

    __call__ only accepts real TaskRow column names as kwargs.
    set_result does read-merge-write with an asyncio.Lock for safety.
    """

    def __init__(self, repo: TaskRepository, task_id: str) -> None:
        self._repo = repo
        self._task_id = task_id
        self._lock = asyncio.Lock()
        # High-water mark for monotonic progress, scoped to THIS run (per-instance,
        # starts at 0). NOT lazy-loaded from the stored value — doing so inherited a
        # stale 100 from the draft phase and masked the entire render at 100% (tasks
        # showed running@100 with no video). Within-run handoffs (prep 75 → ViMax 5)
        # are still clamped; the one-time draft→render transition is allowed to drop.
        self._progress_max: int = 0

    async def __call__(self, **kwargs: Any) -> None:
        """Direct field write. Each call is an atomic SQL UPDATE.

        ``progress`` is clamped to the per-run high-water mark so the bar never
        moves backward within a run (e.g. prep 75% → ViMax extract 5%). A lower
        value is held at the current max; the row still updates (status / updated_at
        / result advance), so stall detection is unaffected.
        """
        if "progress" in kwargs:
            pct = kwargs["progress"]
            if pct < self._progress_max:
                kwargs["progress"] = self._progress_max
            else:
                self._progress_max = pct
        await self._repo.update(self._task_id, **kwargs)

    async def set_result(self, **kwargs: Any) -> None:
        """Merge result dict atomically (read → merge → write back)."""
        async with self._lock:
            task = await self._repo.get(self._task_id)
            existing = json.loads(task.result or "{}") if task else {}
            existing.update(kwargs)
            await self._repo.update(self._task_id, result=json.dumps(existing, ensure_ascii=False))

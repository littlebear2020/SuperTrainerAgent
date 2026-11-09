"""T2 (2026-08-14): gather 无进展看门狗——以「活性」而非「耗时」判 hang。

固定时长预算会误杀 >50 帧串行 + Tier E 续写的健康长任务（单帧合法最坏 ≈13.5min：
生成 poll 300s + 续写 poll 300s + 本地化 180s + probe），任何 elapsed 上限都会在
140 帧任务上误杀。正确信号是活性：timeout 内没有任何一帧 fill 结束（成功/失败都算
结束）→ 上游 hang/限流 → 取消全部并抛可读 RuntimeError。

耦合声明：若 ``video_extend_max_segments`` 未来调大或 provider max_wait 调整，
须同步重推 ``DEFAULT_NO_PROGRESS_TIMEOUT``（20min = 单帧合法最坏 13.5min 的 1.5×）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

DEFAULT_NO_PROGRESS_TIMEOUT = 20 * 60  # 秒


async def gather_with_no_progress_watchdog(
    fill: Callable[[object], Awaitable[None]],
    target: Iterable[object],
    *,
    timeout: float = DEFAULT_NO_PROGRESS_TIMEOUT,
    message: str,
) -> None:
    """``asyncio.gather`` + 无进展看门狗。

    - timeout 内没有任何一个 fill 结束 → 取消全部未完成 fill、抛 ``RuntimeError(message)``
    - fill 持续有结束（哪怕慢——每帧结束都重置窗口）→ 看门狗永不触发
    - 任一 fill 抛异常（``VisualBudgetExceeded`` 等）→ 立即传播、取消其余
      （对齐原 ``gather`` 首异常传播语义；看门狗只包取消与超时路径，不改异常类型）
    """
    progress_event = asyncio.Event()

    async def _tracked(item) -> None:
        try:
            await fill(item)
        finally:
            progress_event.set()  # 成功/失败都算「结束」——失败快速收敛 ≠ hang

    fill_tasks = [asyncio.create_task(_tracked(it)) for it in target]
    if not fill_tasks:
        return

    async def _watchdog() -> None:
        while True:
            progress_event.clear()
            try:
                await asyncio.wait_for(progress_event.wait(), timeout=timeout)
            except TimeoutError:
                logger.error("%s（%.0fs 内无帧完成）", message, timeout)
                raise RuntimeError(message) from None

    watchdog_task = asyncio.create_task(_watchdog())
    # 逐步收窄 pending：只在「尚未结束」的任务上等。看门狗永不正常结束，若用
    # FIRST_EXCEPTION 等全集，「全部完成」永不成立（看门狗永远 pending）；
    # 用 FIRST_COMPLETED 等全集又会在慢速推进时对已结束任务热自旋。
    pending: set = set(fill_tasks) | {watchdog_task}
    try:
        while True:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()  # 重新抛出首个异常（fill 的或看门狗的 RuntimeError）
            if all(t.done() for t in fill_tasks):
                break
    finally:
        for t in [*fill_tasks, watchdog_task]:
            if not t.done():
                t.cancel()
        await asyncio.gather(*fill_tasks, watchdog_task, return_exceptions=True)

"""ai_image / ai_video 成本闸（Task 13 / E1 + E6）。

核实发现严重不对称：ViMax 有 ``vimax_max_image_gen`` / ``vimax_max_video_gen`` /
``vimax_budget_cny`` 三重闸并真实拦截（``VimaxBudgetExceeded``），而
**ai_image / ai_video 零拦截**。

配合 schema 上限量化最坏情况：``n_scenes ≤ 200`` × ``video_count ≤ 5`` =
**单次请求最多 1000 次付费生成，无任何阻拦**。

本模块提供与 ViMax 同构的最小实现。设计取舍：
- **超预算抛错而非静默停止**——静默停止会产出短片并标成功，正是 Task 11 要消灭的
  假成功。
- **已生成帧保留**（调用方不回滚）——让用户至少拿到部分成果，配合明确错误信息
  自行决定是否提高预算重试。
- 断路器/限流是**并发**问题，与本模块的**总量**问题正交，互不复用。
"""

from __future__ import annotations

import threading


class VisualBudgetExceeded(RuntimeError):
    """单任务的生图/生视频调用数或金额超出预算。

    与 ``VimaxBudgetExceeded`` 平行（不共享继承，两条管线的预算语义独立演进）。
    """


class VisualBudget:
    """单任务视觉生成预算：调用数上限 + 金额上限。

    ``max_calls`` / ``max_cost_cny`` 任一为 ``None`` 表示该维度不限。两者都为
    ``None`` 时完全不限（向后兼容：未配置预算的调用方行为不变）。

    线程/协程安全：``charge()`` 在锁内做「检查+扣费」，避免并发渲染多帧时
    check-then-act 竞态导致超支（ai_image 默认 concurrency=3）。
    """

    def __init__(
        self,
        *,
        max_calls: int | None = None,
        max_cost_cny: float | None = None,
        unit_cost_cny: float = 0.0,
    ) -> None:
        self._max_calls = max_calls
        self._max_cost_cny = max_cost_cny
        self._unit_cost_cny = unit_cost_cny
        self._call_count = 0
        self._cost_cny = 0.0
        self._lock = threading.Lock()

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def cost_cny(self) -> float:
        return self._cost_cny

    def charge(self) -> None:
        """为一次即将发生的付费调用扣费；超预算则抛 ``VisualBudgetExceeded``。

        必须在**发起调用之前**调用——先扣后用，宁可少生成一帧也不超支。
        """
        with self._lock:
            if self._max_calls is not None and self._call_count >= self._max_calls:
                raise VisualBudgetExceeded(f"visual generation budget exceeded: {self._call_count}/{self._max_calls} calls")
            next_cost = self._cost_cny + self._unit_cost_cny
            if self._max_cost_cny is not None and next_cost > self._max_cost_cny:
                raise VisualBudgetExceeded(f"visual generation cost exceeded: ¥{self._cost_cny:.2f}+{self._unit_cost_cny:.2f} > ¥{self._max_cost_cny:.2f}")
            self._call_count += 1
            self._cost_cny = next_cost

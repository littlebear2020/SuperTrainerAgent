"""ViMax 结构化全局镜头预算派生（边界收口 Task 6 P2，2026-10-19）。

公式单一事实源（闭合 OCR「engine/service 预算公式逐字双写漂移」finding）：
``max_scenes = ceil(target/90)``、``max_shots_per_scene = max(3, ceil(target/scenes/8))``、
``global_shot_budget = scenes * shots``。engine 的 UI 路径 fallback 与
service 的 ``plan_storyboard_for_tool`` 写入共用本 helper。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


def derive_vimax_storyboard_budget(target_duration: int | float) -> tuple[int, int, int]:
    """target_duration（秒）→ (max_scenes, max_shots_per_scene, global_shot_budget)。

    非正时长 raise（显式门控，不静默给默认值）。
    """
    if not isinstance(target_duration, (int, float)) or target_duration <= 0:
        raise ValueError(f"derive_vimax_storyboard_budget: target_duration must be positive, got {target_duration!r}")
    max_scenes = max(1, math.ceil(target_duration / 90))
    max_shots_per_scene = max(3, math.ceil(target_duration / max_scenes / 8))
    return max_scenes, max_shots_per_scene, max_scenes * max_shots_per_scene


@dataclass(frozen=True)
class ResolvedVimaxBudget:
    """三层预算解析结果（2026-10-20 已拍板口径）。

    ``source`` 是稳定字符串契约：``explicit`` / ``target_duration`` /
    ``storyboard_duration``，同时被 ``resume_draft`` 持久化为
    ``payload["vimax_budget_source"]`` 与测试断言消费。
    ``duration_seconds`` 只在按时长派生时有值（显式预算无时长语义 → None）。
    """

    shot_budget: int
    source: Literal["explicit", "target_duration", "storyboard_duration"]
    duration_seconds: float | None


def _positive_number(value: object) -> float | None:
    """bool 是 int 子类，显式排除；非数值/≤0 静默忽略（不抛异常）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return float(value)


def resolve_vimax_budget(
    *,
    explicit_budget: object = None,
    target_duration: object = None,
    storyboard_duration: object = None,
) -> ResolvedVimaxBudget | None:
    """显式预算 > target_duration > 草稿 total_duration；全部非法 → None。

    缺失时**不**默认 180（已拍板）：保持单批 + warning，由调用方决定文案。
    非法/≤0 静默忽略，不抛异常——UI 存量 payload 可能带字符串或 null。
    """
    explicit = _positive_number(explicit_budget)
    if explicit is not None:
        return ResolvedVimaxBudget(shot_budget=int(explicit), source="explicit", duration_seconds=None)

    target = _positive_number(target_duration)
    if target is not None:
        return ResolvedVimaxBudget(
            shot_budget=derive_vimax_storyboard_budget(target)[2],
            source="target_duration",
            duration_seconds=target,
        )

    storyboard = _positive_number(storyboard_duration)
    if storyboard is not None:
        return ResolvedVimaxBudget(
            shot_budget=derive_vimax_storyboard_budget(storyboard)[2],
            source="storyboard_duration",
            duration_seconds=storyboard,
        )
    return None

"""legacy 方言（回滚位 + 兼容位）：现行 compose_video_prompt 透传，语义逐字节不变。

P0-5 不变量的落点：手改帧 / 旧 storyboard（无 visual_ir）/ 拆帧子帧 / 未知 provider /
env=legacy（默认，Phase 5 A/B 达标前）--全部走这里。
"""

from __future__ import annotations

from ..orchestration.storyboard_planner import compose_video_prompt


def translate(frame, *, reference_count: int = 0, reference_kind: str | None = None) -> str:  # noqa: ANN001, ARG001
    """现行行为：image_prompt + Task 8 镜头语言拼接（无参考图指代--现状语义）。"""
    return compose_video_prompt(frame)

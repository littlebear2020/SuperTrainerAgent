"""MixedVisualSource — render frames with different visual source types in one video.

Routes each frame to the appropriate VisualSource based on its visual_source field
(or the storyboard config default if not set).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..storyboard import Storyboard

logger = logging.getLogger(__name__)


class MixedVisualSource:
    """Manages rendering of frames with heterogeneous visual source types."""

    def __init__(self, sources: dict[str, Any]):
        """Initialize with a mapping of source_type → VisualSource instance.

        Args:
            sources: e.g. {"ai_image": AIImageVisualSource, "ai_video": AIVideoVisualSource, ...}
        """
        self._sources = sources

    async def render(
        self,
        storyboard: Storyboard,
        *,
        only_frames: list[int] | None = None,
        on_frame_done: Callable[[int], Awaitable[None]] | None = None,
        task_dir: Path | None = None,
        reference_image_paths: list[str] | None = None,
        style_anchor_image_paths: list[str] | None = None,
        character_roster: dict | None = None,  # M3: name→三视图路径（roster 任务级下发）
        prop_roster: dict | None = None,  # M3: name→道具图路径
    ) -> None:
        """Render all frames, routing each to the appropriate visual source.

        Frames are grouped by visual_source type and rendered in parallel batches.
        HR-T1: task_dir 透传给子源（ai_video 部分失败时写占位）。
        Task 12: reference_image_paths 透传给子源（ai_image 用，
        ai_video 接受但忽略——signature 对齐以便 mixed 统一转发）。
        C4（决策 13）: style_anchor_image_paths 原样透传给子源——仅 ai_image 消费
        （ai_video 接收但忽略，视频帧风格经既有预览图继承机制传导）。
        M3: character_roster/prop_roster 透传给 ai_image（帧级选择 + 逐帧预算）；
        ai_video 接收但忽略（视频侧多资产属 M5，本计划外）。
        """
        default_type = storyboard.config.video_type
        only_set = set(only_frames) if only_frames is not None else None

        # Group frames by their visual source type
        groups: dict[str, list[int]] = defaultdict(list)
        for frame in storyboard.frames:
            if only_set is not None and frame.index not in only_set:
                continue
            vs = getattr(frame, "visual_source", None) or default_type
            if vs == "graphic":
                # graphic-native-text（2026-08-17）：文字由 AI 生图画进画面——graphic 帧
                # 归并到 ai_image 源渲染（帧标记保留：护栏/徽标/一键关闭仍按 graphic 判定）。
                vs = "ai_image"
            groups[vs].append(frame.index)

        # Render each group in parallel
        # E12 修正：用 (source_type, task) 并行列表对齐 results，避免未注册 source 被 continue
        # 跳过后 tasks 比 groups 短、`list(groups.keys())[i]` 索引错位误报失败源。
        # C4：有锚图才透传该参数（条件转发——既有子源实现/测试桩的窄签名天然兼容）。
        _anchor_kwargs = {"style_anchor_image_paths": style_anchor_image_paths} if style_anchor_image_paths else {}
        # M3：roster 同款条件转发（空 roster = 通道不生效，不传参——旧签名兼容）
        _roster_kwargs = {}
        if character_roster or prop_roster:
            _roster_kwargs = {"character_roster": character_roster, "prop_roster": prop_roster}
        task_specs: list[tuple[str, Any]] = []
        for source_type, frame_indices in groups.items():
            source = self._sources.get(source_type)
            if source is None:
                logger.warning("No visual source registered for type '%s', skipping %d frames", source_type, len(frame_indices))
                continue
            task_specs.append(
                (
                    source_type,
                    source.render(
                        storyboard,
                        only_frames=frame_indices,
                        on_frame_done=on_frame_done,
                        task_dir=task_dir,
                        reference_image_paths=reference_image_paths,
                        **_anchor_kwargs,
                        **_roster_kwargs,
                    ),
                )
            )

        if task_specs:
            results = await asyncio.gather(*[t for _, t in task_specs], return_exceptions=True)
            # E12: 子源失败须 fail-loud（与 ai_image 一致），不得仅 logger.error 静默丢帧
            failures = []
            for (source_type, _), result in zip(task_specs, results):
                if isinstance(result, Exception):
                    logger.error("Visual source '%s' failed: %s", source_type, result)
                    failures.append((source_type, result))
            if failures:
                src_types = ", ".join(s for s, _ in failures)
                first = failures[0][1]
                raise RuntimeError(f"mixed 视觉源渲染失败 [{src_types}]：{first}") from first

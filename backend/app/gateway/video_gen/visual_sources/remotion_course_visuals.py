"""remotion_course 槽位过滤视觉源（2026-09-10 独立管线计划 T4）。

设计（计划 §4.3）：内部持有既有 ``AIImageVisualSource`` 与 ``AIVideoVisualSource``
（provider 调用层/审核重试/预算计费全部复用，``ai_image.py`` / ``ai_video.py`` 零修改），
``render()`` 按 §3.4 条件过滤目标帧后委托：

- **AI 生图**——图槽版式（IMAGE_SLOT_LAYOUTS）+ ``visual_source=="ai_image"`` +
  ``image_prompt`` 非空 + 无 ``local_visual_path``（有嵌入图/上传图的帧不生 AI 图），
  与 ``only_frames`` 取交集后委托 ai_image fill；
- **AI 视频**——仅用户显式设置 ``visual_source="ai_video"`` 的 VideoClipPage 帧
  （决策 9：parser 永不产出；已上传视频的帧不重生），委托 ai_video fill；
- 其余帧 ``visual`` 保持 none（模板渲染占位/播放键占位，用户在编辑器补素材）。

AI 管线专属通道（reference_image_paths / style_anchor_image_paths /
character_roster / prop_roster）显式接收并**语义丢弃**——课程管线不吃角色/锚图/
名册注入（``_regenerate_frame`` 与主管线都会传这些 kwargs，必须兼容签名）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from ..orchestration.course_script import IMAGE_SLOT_LAYOUTS
from ..storyboard import Storyboard

logger = logging.getLogger(__name__)


def image_slot_target(frame) -> bool:  # noqa: ANN001
    """§3.4 AI 生图条件（四者同时满足）。"""
    return getattr(frame, "layout", None) in IMAGE_SLOT_LAYOUTS and getattr(frame, "visual_source", None) == "ai_image" and bool((getattr(frame, "image_prompt", None) or "").strip()) and not getattr(frame, "local_visual_path", None)


def video_clip_target(frame) -> bool:  # noqa: ANN001
    """§3.4 AI 视频条件（四者同时满足；ai_video 只能用户显式设置——决策 9）。"""
    return getattr(frame, "layout", None) == "VideoClipPage" and getattr(frame, "visual_source", None) == "ai_video" and bool((getattr(frame, "image_prompt", None) or "").strip()) and not getattr(frame, "local_visual_path", None)


# 条目 3（2026-09-12 缺陷批）：残留旧图帧「重新生成视频」守卫文案——service 层
# ValueError 与 router 同步 400 预检两层同一文案（前端 readErrorDetail 直接透出）。
RESIDUAL_VISUAL_BLOCK_MESSAGE = "请先移除旧配图再生成视频"
_EMBEDDED_PREFIX = "visuals/embedded/"
_USER_UPLOAD_PREFIX = "visuals/frame_"


def _frame_field(frame, key: str):  # noqa: ANN001, ANN202
    """帧字段读取：StoryboardFrame（attr）与 storyboard.json 原始帧双兼容。"""
    if isinstance(frame, dict):
        return frame.get(key)
    return getattr(frame, key, None)


def residual_visual_regen_block_reason(task_type: str, frame) -> str | None:  # noqa: ANN001
    """残留旧配图帧拒绝视觉重生的判定矩阵（条目 3，service/router 两层共用）。

    | 场景 | 条件 | 行为 |
    |---|---|---|
    | 缺陷 A | VideoClipPage + ai_video + 有 local + visual_type=="image"（残留预览图） | 拒 |
    | 正常视频重生 | VideoClipPage + ai_video + local + visual_type=="video"（已本地化视频，ai_video.py:607,879） | 放行 |
    | 变体 B | VideoClipPage + ai_image + 有 local（残留 AI 图 visuals/img_） | 拒 |
    | 嵌入/上传帧 | local 前缀 visuals/embedded/ 或 visuals/frame_ | 跳过（P6 既有守卫拦） |

    visual_type 缺失但 local 存在的病态态视同残留（宁可误拒一次，不让
    _keep_preview_for_i2v=False 静默清掉素材再空目标 return——变体 B 的素材丢失路径）。
    其余（非 remotion_course / 非 VideoClipPage / 无 local）→ None 放行。
    """
    if task_type != "remotion_course":
        return None
    local = _frame_field(frame, "local_visual_path")
    if not local:
        return None
    lv = str(local)
    if lv.startswith(_EMBEDDED_PREFIX) or lv.startswith(_USER_UPLOAD_PREFIX):
        return None  # P6 既有守卫（嵌入/上传素材帧）负责，不重复
    if _frame_field(frame, "layout") != "VideoClipPage":
        return None
    source = _frame_field(frame, "visual_source")
    if source == "ai_video" and _frame_field(frame, "visual_type") != "video":
        return RESIDUAL_VISUAL_BLOCK_MESSAGE  # 缺陷 A：残留预览图
    if source == "ai_image":
        return RESIDUAL_VISUAL_BLOCK_MESSAGE  # 变体 B：残留 AI 图
    return None


class RemotionCourseVisualSource:
    """槽位过滤编排层：机制（provider 调用/审核/计费）复用，编排全新。"""

    def __init__(self, *, image_source, video_source) -> None:  # noqa: ANN001
        self._image_source = image_source
        self._video_source = video_source

    async def render(
        self,
        storyboard: Storyboard,
        *,
        only_frames: list[int] | None = None,
        on_frame_done: Callable[[int], Awaitable[None]] | None = None,
        task_dir: Path | None = None,
        reference_image_paths: list[str] | None = None,
        style_anchor_image_paths: list[str] | None = None,
        character_roster: dict | None = None,
        prop_roster: dict | None = None,
    ) -> None:
        # AI 管线专属通道显式接收并忽略（兼容 _regenerate_frame/主管线宽签名转发）
        if reference_image_paths or style_anchor_image_paths or character_roster or prop_roster:
            logger.info(
                "remotion_course visual source: ignoring AI-pipeline-only injection channels (%d refs / %d anchors / %d chars / %d props)",
                len(reference_image_paths or []),
                len(style_anchor_image_paths or []),
                len(character_roster or {}),
                len(prop_roster or {}),
            )

        only_set = set(only_frames) if only_frames is not None else None

        def _in_scope(idx: int) -> bool:
            return only_set is None or idx in only_set

        img_targets = [f.index for f in storyboard.frames if image_slot_target(f) and _in_scope(f.index)]
        vid_targets = [f.index for f in storyboard.frames if video_clip_target(f) and _in_scope(f.index)]
        logger.info("remotion_course visuals: %d image targets, %d video targets (of %d frames)", len(img_targets), len(vid_targets), len(storyboard.frames))

        def _filtered_view(indices: list[int]) -> Storyboard:
            """真过滤视图（M1，2026-09-11 审查）：覆盖率闸按"全 storyboard 中
            visual_source 命中本源的帧"计分母（ai_image._coverage_scope 不认
            only_frames）——直接透传整表时，用户上传替换/清空配图词后 visual_source
            残留的帧永远无 visual_url，会被计成失败把生成全成功的任务误报
            "coverage too low" 打挂。浅拷贝只留目标帧（帧对象共享引用，生成产物
            写回原 storyboard 不受影响），分母=目标集。
            """
            wanted = set(indices)
            return replace(storyboard, frames=[f for f in storyboard.frames if f.index in wanted])

        tasks = []
        if img_targets:
            tasks.append(self._image_source.render(_filtered_view(img_targets), on_frame_done=on_frame_done, task_dir=task_dir))
        if vid_targets:
            tasks.append(self._video_source.render(_filtered_view(vid_targets), on_frame_done=on_frame_done, task_dir=task_dir))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            raise RuntimeError(f"remotion_course 视觉源渲染失败：{failures[0]}") from failures[0]

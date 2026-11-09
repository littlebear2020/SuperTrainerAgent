"""Engine layer contracts (plan §4.1 / 阶段 1.1).

All video engines (remotion / vimax) implement the VideoEngine
Protocol. The composition layer (service.py) calls engine.render(ctx) and
branches on EngineOutcome.mode:
  - "segments"  → reserved for future segments engines
  - "finished"  → service takes the成品 mp4 directly (remotion/vimax)

These are duck-typed contracts; engines need not inherit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class SegmentArtifact(BaseModel):
    """A single frame/shot artifact. Only used in segments mode."""

    frame_index: int
    visual_path: str  # local path (mp4/png)
    visual_type: Literal["video", "image"]
    duration: float = 0.0
    review_score: float | None = None  # 0-1 quality review score, nullable
    review_notes: str | None = None


class EngineOutcome(BaseModel):
    """Engine result. mode is one of segments/finished (two distinct species)."""

    mode: Literal["segments", "finished"]
    segments: list[SegmentArtifact] | None = None  # mode=segments
    finished_video_path: str | None = None  # mode=finished (remotion/vimax)
    thumbnail_path: str | None = None
    engine_metadata: dict[str, Any] = Field(default_factory=dict)
    # e.g. ViMax character portrait paths, reference-image map, review scores


class EngineContext(BaseModel):
    """Engine run context.

    v3: does NOT carry storyboard JSON — only task_id; the engine reads the
    on-disk task_dir/storyboard.json itself (reuses the稳健 draft→render path).
    v3 vimax knobs (pipeline旋钮① + style旋钮②) are ctx fields, collected by
    the course-video agent at blueprint confirmation (§3.2 / §6.4).
    """

    user_id: str
    task_id: str
    task_dir: Path
    voice_id: str = "zh-CN-YunjianNeural"
    subtitle_style: str = "default"  # B6c: 字幕样式（B2 从项目解析进 storyboard.config，ctx 携带供 vimax overlay 烧录）
    # T7b（2026-10-14 配音修复批）：service 层三级解析后的语速（任务显式 > 项目 >
    # config 缺省，storyboard.config.tts_speed）。修前 engine 只读 ctx.request 的
    # 原始 payload tts_speed → 项目级设置在 vimax 管线静默失效。None = 兼容旧调用
    # 方/测试（engine 回落 request 再落 1.2）。
    tts_speed: float | None = None
    style: str | None = None  # vimax旋钮②: "realistic movie style" / "Cartoon" / ...
    pipeline: str | None = None  # vimax旋钮①: "idea2video"|"script2video"（"novel2movie" 已于 2026-10-16 拆除，传入即 RuntimeError）
    # T3（Lane 2, 2026-10-15）：任务画幅（"16:9"/"9:16"/...）——engine 推导字幕
    # orientation 与 ASS PlayRes（修前 vimax 硬编码竖屏，横屏任务折行/PlayRes 失真）。
    # None → 竖屏（存量默认）。来源 = request payload aspect（service 组装时透传）。
    aspect: str | None = None
    progress: Any = None  # ProgressUpdater — engine reports progress via it
    # The task's create-request payload (script/topic/aspect/mode/target_duration...).
    # Engines read request-specific fields from here; storyboard-derived fields
    # (total_duration, frames) from task_dir/storyboard.json. Set by the
    # composition layer (service.py).
    request: dict[str, Any] | None = None


@runtime_checkable
class VideoEngine(Protocol):
    """Unified contract all video engines implement (duck-typed)."""

    name: str  # "remotion" | "vimax"

    async def render(self, ctx: EngineContext) -> EngineOutcome:
        """Read ctx.task_dir/storyboard.json, run the engine, produce outcome.

        Must report progress periodically via ctx.progress (0-100), keep all
        intermediate artifacts under ctx.task_dir, and raise on failure (the
        composition layer catches and marks the task failed).
        """
        ...

    async def review_frame(
        self,
        ctx: EngineContext,
        frame_index: int,
        visual_path: str,
    ) -> tuple[float, str] | None:
        """(Optional) quality review of a single frame → (score, notes).

        Engines without review capability return None. ViMax has its own
        BestImageSelector internally and need not implement this.
        """
        ...

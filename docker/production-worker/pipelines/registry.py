"""Worker-side (domain, task_type) → pipeline function registry.

Each pipeline function signature:
    fn(task_dir: Path, payload: dict, options: dict, progress_cb: Callable[[int], None]) -> Path
Returns output file path.

Supports pipeline_version routing for mixed visual sources.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .video_gen_ai_visual import compose_ai_visual
from .video_gen_mixed import compose_mixed
from .video_gen_segmented import compose_segmented
from .voice_production_ppt_to_video import compose_ppt_video
from .compose_remotion import compose_remotion

PipelineFn = Callable[..., Path]

PIPELINES: dict[tuple[str, str], PipelineFn] = {
    ("voice_production", "ppt_to_video"): compose_ppt_video,
    ("video_gen", "ai_image"): compose_ai_visual,
    ("video_gen", "ai_visual"): compose_ai_visual,  # legacy
    ("video_gen", "ai_video"): compose_ai_visual,
    ("video_gen", "mixed"): compose_mixed,
    # T3（2026-09-08 remotion-course）：remotion 池管线注册——缺此步认领后
    # resolve_pipeline 抛 UnsupportedTaskType 直接标 failed。ffmpeg 池靠
    # worker.py 认领谓词（registry 派生白名单扣除 remotion 类型）不认领它。
    ("video_gen", "remotion_course"): compose_remotion,
    ("video_gen", "remotion_still"): compose_remotion,  # T7 静帧预览（同管线，payload 分支）
}


class UnsupportedTaskType(Exception):
    pass


def resolve_pipeline(domain: str, task_type: str, payload: dict | None = None) -> PipelineFn:
    """Resolve pipeline function. If payload contains pipeline_version='v2_mixed', use mixed pipeline."""
    if payload and payload.get("pipeline_version") == "v3_segmented":
        return compose_segmented

    if payload and payload.get("pipeline_version") == "v2_mixed":
        return compose_mixed

    fn = PIPELINES.get((domain, task_type))
    if fn is None:
        raise UnsupportedTaskType(f"No pipeline registered for ({domain}, {task_type})")
    return fn

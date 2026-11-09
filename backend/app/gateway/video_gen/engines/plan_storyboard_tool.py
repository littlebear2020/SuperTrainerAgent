"""plan_storyboard tool (阶段 1.8 补件) — create a draft video task + blueprint from chat.

Lives in the APP layer (same P0-1 reason as generate_video: it calls
app.gateway.video_gen.service, which would violate test_harness_boundary.py if
placed in packages/harness). Registered via config.yaml tools[]:
    - name: plan_storyboard
      group: video_engine
      use: app.gateway.video_gen.engines.plan_storyboard_tool:plan_storyboard_tool

The vimax-video agent calls this AFTER ask_clarification confirms the video
type + content, and BEFORE present_files / generate_video: it produces a
task_id with storyboard.json on disk that the agent presents for confirmation,
then feeds to generate_video to render.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class _PlanStoryboardInput(BaseModel):
    video_type: str = Field(description="视频类型: vimax")
    script: str | None = Field(None, description="固定脚本模式：完整旁白文本（与 topic 二选一）")
    topic: str | None = Field(None, description="生成模式：一句话主题，由 agent 扩写成旁白（与 script 二选一）")
    aspect: str | None = Field(None, description="画面比例: 9:16 | 16:9 | 1:1")
    voice_id: str | None = Field(None, description="配音音色 ID（留空按语言选默认）")
    n_scenes: int | None = Field(None, description="分镜数量（生成模式有效，默认 5）")
    style: str | None = Field(None, description="画面风格（如 default / realistic）")
    pipeline: str | None = Field(
        None,
        description="vimax 内容来源（旋钮①）：idea2video（一句话想法→扩写故事→分镜）或 script2video（默认）。idea2video 会先扩写故事让你确认，再渲染。",
    )
    target_duration: int | None = Field(None, description="目标视频时长（秒），用于自动限制场景数和镜头数。60=1分钟，180=3分钟")


class PlanStoryboardTool(BaseTool):
    """Plan a video storyboard blueprint and persist it; returns task_id + summary.

    execute_fn is the service-layer draft creator (DI for testability; wired at
    app startup via wire_plan_storyboard_executor). Blocks until the blueprint is
    on disk (DRAFT status) so the agent can immediately present/confirm it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "plan_storyboard"
    description: str = (
        "为视频规划分镜蓝图并落盘，返回 task_id 与蓝图内容，供后续 generate_video 渲染。"
        "前置：已用 ask_clarification 与用户确认视频类型与内容。"
        "参数：video_type (vimax)、script（固定脚本）或 topic（一句话主题，二选一）、"
        "可选 aspect/voice_id/n_scenes/style/target_duration（目标时长秒数，用于自动限制场景/镜头数）。"
        "返回 {task_id, status, video_type, n_frames, total_duration, blueprint, story}——"
        "blueprint 是每镜的旁白+画面描述（draft 状态），story 是扩写的故事全文（idea2video 的 story_ready 状态）。"
        "调用后必须先把 blueprint/story 的内容贴给用户看，再用 ask_clarification 请求用户确认蓝图"
        "（只报'共 N 镜'不算展示，只展示不确认也不算完成前置），收到肯定答复后才调 generate_video。"
    )
    args_schema: type = _PlanStoryboardInput

    execute_fn: Callable[..., Awaitable[dict]] | None = None

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("plan_storyboard is async-only; use _arun")

    async def _arun(
        self,
        video_type: str,
        script: str | None = None,
        topic: str | None = None,
        aspect: str | None = None,
        voice_id: str | None = None,
        n_scenes: int | None = None,
        style: str | None = None,
        pipeline: str | None = None,
        target_duration: int | None = None,
        **kwargs: Any,
    ) -> dict:
        execute = self.execute_fn
        if execute is None:
            # 1.8 wires the real executor at app startup. Until then, fail loudly.
            return {"status": "error", "error": "plan_storyboard tool executor not wired (see 1.8)"}
        try:
            return await execute(
                video_type=video_type,
                script=script,
                topic=topic,
                aspect=aspect,
                voice_id=voice_id,
                n_scenes=n_scenes,
                style=style,
                pipeline=pipeline,
                target_duration=target_duration,
            )
        except Exception as exc:
            logger.warning("plan_storyboard executor failed", exc_info=True)
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def wire_plan_storyboard_executor(tool: PlanStoryboardTool, service: Any) -> None:
    """阶段 1.8: bind plan_storyboard's executor to the service draft creator.

    Called from app.py lifespan once VideoGenService is initialized. Idempotent.
    """
    tool.execute_fn = service.plan_storyboard_for_tool


# Singleton referenced by config.yaml tools[] `use:`.
plan_storyboard_tool = PlanStoryboardTool()

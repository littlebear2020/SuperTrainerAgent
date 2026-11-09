"""generate_video tool (阶段 1.6 + 1.9 wall-clock timeout).

Lives in the APP layer (P0-1: NOT in packages/harness — the tool calls
app.gateway.video_gen engines/registry, which would violate
test_harness_boundary.py). Registered via config.yaml tools[]:
    - name: generate_video
      group: video_engine
      use: app.gateway.video_gen.engines.tool:generate_video_tool
tools.py:65 resolve_variable reflects the singleton — zero harness code.

The tool is a thin adapter: parse args → call an injected executor
(service-layer pipeline, wired in 1.8) → apply a wall-clock timeout (1.9:
vimax 15-30min blocking has no other timeout; remotion_agent_timeout:600 is
RemotionAgentRunner-only) → return a result dict. Progress flows via the
service-layer SSE (P0-2), NOT through this tool's return value.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class _GenerateVideoInput(BaseModel):
    video_type: str = Field(description="视频类型: vimax")
    task_id: str = Field(description="视频任务 ID（蓝图已落盘在该任务的 task_dir/storyboard.json）")
    pipeline: str | None = Field(None, description="vimax 管线: idea2video | script2video（默认 script2video；novel2movie 已于 2026-10-16 拆除，传入即报错）")
    style: str | None = Field(None, description="vimax 画面风格: realistic movie style | Cartoon | Anime Style | ...")


class GenerateVideoTool(BaseTool):
    """Agent→engine bridge. Fire-and-forget (M1.2): the service registers the
    pipeline as a background asyncio task and returns {mode: 'submitted'}
    immediately; the 1800s wait_for below is a moot backstop since the executor
    returns instantly.

    execute_fn is the service-layer pipeline runner (DI for testability; 1.8
    wires the real one to VideoGenService via app.state). Returns a result dict;
    progress is reported through the service SSE, not the return value.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "generate_video"
    description: str = (
        "为已确认的视频蓝图调用引擎开始渲染（异步：触发后立即返回，后台执行，进度由服务端推送）。"
        "前置：已用 ask_clarification 确认视频类型、plan_storyboard 规划并落盘 storyboard.json、并已用 ask_clarification 让用户确认蓝图（收到肯定答复）。"
        "参数：video_type (vimax)、task_id；vimax 还可传 pipeline 与 style。"
        "返回 {task_id, status, mode: 'submitted'}。"
        "重要：本工具返回的是'已提交'，不是'渲染进度'——真实的进度/成功/失败状态由服务端推送，你不要描述渲染细节"
        "（如分镜数量、GPU 节点、音画对齐等），调用后只需如实告诉用户'已提交渲染'。"
        "若用户随后追问进度，必须调用 check_video_status 获取真实状态后再回答，禁止凭'已提交'推断仍在渲染。"
    )
    args_schema: type = _GenerateVideoInput

    execute_fn: Callable[..., Awaitable[dict]] | None = None
    timeout: float = 1800.0  # 1.9: vimax default 30 min wall-clock (no other timeout covers it)

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("generate_video is async-only; use _arun")

    async def _arun(self, video_type: str, task_id: str, pipeline: str | None = None, style: str | None = None, **kwargs: Any) -> dict:
        execute = self.execute_fn
        if execute is None:
            # 1.8 wires the real executor (app.state → VideoGenService). Until then,
            # fail loudly so an unconfigured tool isn't silently invoked.
            return {"task_id": task_id, "status": "error", "error": "generate_video tool executor not wired (see 1.8)"}
        try:
            result = await asyncio.wait_for(
                execute(video_type=video_type, task_id=task_id, pipeline=pipeline, style=style),
                timeout=self.timeout,
            )
        except TimeoutError:
            return {"task_id": task_id, "status": "timeout", "error": f"generate_video exceeded {self.timeout}s wall-clock (1.9)"}
        except Exception as exc:  # surface executor failures as a status, not a crash
            logger.warning("generate_video executor failed for task %s", task_id, exc_info=True)
            return {"task_id": task_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
        return {"task_id": task_id, "status": "done", **result}


def wire_generate_video_executor(tool: GenerateVideoTool, service: Any) -> None:
    """阶段 1.8: bind the generate_video tool's executor to the service pipeline.

    Called from app.py lifespan once VideoGenService is initialized. Binds
    ``tool.execute_fn`` to ``service.run_pipeline_for_tool`` so an agent invoking
    generate_video runs the full video-gen pipeline (progress via task SSE, status
    transitions, retry, cancel+drain on the tool's wall-clock timeout). Idempotent:
    re-wiring simply rebinds the same bound method.
    """
    tool.execute_fn = service.run_pipeline_for_tool


# Singleton referenced by config.yaml tools[] `use:` — reflected by tools.py:65.
# execute_fn is wired to VideoGenService during app startup (1.8 integration).
generate_video_tool = GenerateVideoTool()

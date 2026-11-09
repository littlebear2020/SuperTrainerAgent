"""check_video_status tool — let the agent query a video task's REAL status.

Lives in the APP layer (same P0-1 reason as generate_video/plan_storyboard:
it calls app.gateway.video_gen.service, which would violate
test_harness_boundary.py if placed in packages/harness). Registered via
config.yaml tools[]:
    - name: check_video_status
      group: video_engine
      use: app.gateway.video_gen.engines.check_status_tool:check_video_status_tool

Why this exists: ``generate_video`` is fire-and-forget — it returns
{mode: "submitted"} the instant the render is scheduled and never looks back.
So an agent that only remembers "I submitted a render" will keep saying
"rendering..." forever, even after the task has FAILED (root cause of the
vg_7477487923ef incident where the agent narrated "ViMax GPU is aligning
audio/video" while the task had been `failed` for 10 minutes). This tool
reads the ground-truth row from the DB so the agent can report status
truthfully instead of from memory.

2026-07-21: description 触发条件收紧——旧文案"对话中涉及任何 vg_ ID 时必须先调用"
导致 thread 历史出现任务 ID 后 agent 每轮（甚至还没规划脚本）都被逼着先调本工具。
限定为：用户明确询问/准备报告结果前。工具本身保留（防凭记忆编造进度）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class _CheckVideoStatusInput(BaseModel):
    task_id: str = Field(description="视频任务 ID（plan_storyboard/generate_video 返回的 vg_ 开头 ID）")


class CheckVideoStatusTool(BaseTool):
    """Query a video task's real status from the DB.

    execute_fn is the service-layer status reader (DI for testability; wired at
    app startup via wire_check_status_executor). Returns ground-truth status —
    NOT a guess from memory — so the agent can truthfully report ready /
    failed (+ error_message) / progress.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str = "check_video_status"
    description: str = (
        "查询视频任务的真实状态（从数据库读取，不是猜测）。"
        "仅在以下时机调用：用户明确询问视频进度/追问历史任务状态，或你准备向用户报告渲染结果之前。"
        "不要因为对话历史中出现过 vg_ 任务 ID 就主动调用。"
        "禁止凭记忆或'已提交渲染'就编造进度（如分镜数、GPU 渲染、音画对齐等渲染细节）——"
        "本工具返回的 status/progress/error_message 才是事实。"
        "参数：task_id。返回 {task_id, status, progress, error_message?, output_url?, updated_at}。"
        "status 取值：pending|running|draft（蓝图待确认）|story_ready（idea2video 故事待确认）|rendering"
        "|composing（已交 Worker 合成）|ready（成功，含 output_url）|failed（失败，含 error_message）"
        "|not_found|polling_rate_limited（同一任务刚查过，见 retry_after_seconds，等待后再查，勿刷屏轮询）。"
    )
    args_schema: type = _CheckVideoStatusInput

    execute_fn: Callable[..., Awaitable[dict]] | None = None

    # D3 (2026-07-15 split): per-task polling rate limit. generate_video is
    # fire-and-forget and this tool is polling_exempt, so without a cap the agent
    # polls in a tight loop until the tool-call total limit (刷屏, split §一
    # problem 3). A too-soon same-task call returns a wait hint without a DB hit.
    MIN_POLL_INTERVAL: float = 5.0
    last_polled_at: dict[str, float] = Field(default_factory=dict)

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("check_video_status is async-only; use _arun")

    async def _arun(self, task_id: str, **kwargs: Any) -> dict:
        execute = self.execute_fn
        if execute is None:
            # wired at app startup (app.py). Until then, fail loudly so an
            # unconfigured tool isn't silently invoked.
            return {"task_id": task_id, "status": "error", "error": "check_video_status tool executor not wired"}
        # D3: rate-limit rapid same-task polling — no DB hit, no status to brainstorm from.
        now = time.monotonic()
        last = self.last_polled_at.get(task_id)
        if last is not None and (now - last) < self.MIN_POLL_INTERVAL:
            elapsed = now - last
            return {
                "task_id": task_id,
                "status": "polling_rate_limited",
                "retry_after_seconds": round(self.MIN_POLL_INTERVAL - elapsed, 1),
                "message": f"刚查过此任务（{elapsed:.1f}s 前），频繁轮询不加快渲染，请稍候再查",
            }
        try:
            result = await execute(task_id=task_id)
            self.last_polled_at[task_id] = time.monotonic()
            return result
        except Exception as exc:
            logger.warning("check_video_status failed for task %s", task_id, exc_info=True)
            return {"task_id": task_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


def wire_check_status_executor(tool: CheckVideoStatusTool, service: Any) -> None:
    """Bind check_video_status's executor to the service status reader.

    Called from app.py lifespan once VideoGenService is initialized. Idempotent.
    """
    tool.execute_fn = service.check_task_status_for_tool


# Singleton referenced by config.yaml tools[] `use:`.
check_video_status_tool = CheckVideoStatusTool()

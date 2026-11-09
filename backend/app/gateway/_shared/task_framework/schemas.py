"""Status constants and shared Pydantic response models."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import BaseModel, field_validator

# ── Status constants ────────────────────────────────────────────────
# Terminal statuses
READY = "ready"
FAILED = "failed"
# Task 22：用户主动取消。与 failed 区分——不是故障，不写 error_message。
CANCELLED = "cancelled"

# Non-terminal statuses (Gateway-owned)
PENDING = "pending"
RUNNING = "running"

# Domain-specific sub-statuses (still "running" category)
DRAFT = "draft"
EXTRACTING = "extracting"
CONVERTING = "converting"
SYNTHESIZING = "synthesizing"
SCRIPTING = "scripting"
GENERATING = "generating"
RENDERING = "rendering"
STORY_READY = "story_ready"  # idea2video: idea expanded into story, awaiting user confirm

# Worker-owned status
COMPOSING = "composing"

# 审查发现 3：CANCELLED 必须算终态，两个消费者都依赖：
# ① _wrap_pipeline 的 CancelledError 分支用它判「尊重终态、不自动重排」——
#    不含 cancelled 时，取消会被重排回 pending，用户的取消被推翻、继续烧配额；
# ② SSE 的 _is_terminal_status 用它决定何时发终止事件——不含 cancelled 时
#    取消后流不结束，前端一直转圈。
TERMINAL_STATUSES = frozenset({READY, FAILED, CANCELLED})

# Gateway recoverable statuses (excludes COMPOSING — Worker territory)
GATEWAY_RECOVERABLE = (
    PENDING,
    RUNNING,
    DRAFT,
    EXTRACTING,
    CONVERTING,
    SYNTHESIZING,
    SCRIPTING,
    GENERATING,
    RENDERING,
    STORY_READY,
)


def ensure_utc(dt: datetime | str | None) -> datetime | None:
    """Naive datetime/ISO string → UTC aware; already-aware → pass through.

    Accepts ISO-8601 strings (e.g. SQLite or FakeRow created_at) in addition to
    datetime/None. ``TaskBrief._ensure_utc`` guards with isinstance(datetime) so
    this str branch is exercised mainly by ``ProductionTaskResponse.from_task_row``,
    which calls ensure_utc directly on the row value.
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


# ── Shared response models ─────────────────────────────────────────


class TaskBrief(BaseModel):
    """Brief task info for list endpoints (no payload/result)."""

    id: str
    user_id: str
    domain: str
    task_type: str
    status: str
    progress: int
    title: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    dirty_frames: list[int] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None
    thumbnail_url: str | None = None
    # 2026-09-13 死链修复：产物缺失派生标注（响应层计算、不落库）——仅 video_gen
    # 的 _task_with_thumbnail 在 status=="ready" 时按 result.output_path 存在性赋值；
    # 其他域（voice_twin/writer）共享本模型但永不赋值，恒 False。
    artifact_missing: bool = False
    # 批次 C (2026-08-13)：每任务花费。TaskRow 无 cost 列——由端点事后赋值
    # （task_cost.attach_task_costs），None = 无法计价（前端显示 "—"）。
    cost: float | None = None
    currency: str | None = None
    # C4（2026-08-27 成本可信化）：花费来源标注——real（供应商真账）/
    # estimated（价目估算）/ mixed（两者皆有）/ None（未计价）。前端"估"标依据。
    amount_source: str | None = None
    # T13（2026-09-08 remotion-course）：composing 未认领任务的排队队位（1 基，
    # 按认领顺序 priority DESC / created_at ASC）；其余状态 None。详情端点事后赋值。
    queue_position: int | None = None
    # 2026-10-20 Task 4：分镜帧与 ViMax 渲染镜头分源摘要（仅任务详情端点计算；
    # 列表端点不读盘，保持 None/False）。
    frame_count: int | None = None
    total_duration: float | None = None
    shot_count: int | None = None
    # manifest 非空即可逐镜重做——与 ready/failed 任务状态解耦。
    shots_ready: bool = False

    @field_validator("dirty_frames", mode="before")
    @classmethod
    def _parse_dirty_frames(cls, v: object) -> object:
        """TaskRow stores dirty_frames as a JSON string ('[]' or '[0, 3]');
        parse to list[int] for the API response."""
        if isinstance(v, str):
            return json.loads(v)
        return v

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _ensure_utc(cls, v: object) -> object:
        """SQLite CURRENT_TIMESTAMP stores naive UTC datetimes; normalize to aware UTC."""
        if isinstance(v, datetime):
            return ensure_utc(v)
        return v


class TaskDetail(TaskBrief):
    """Full task detail including payload and result.

    payload/result are stored as JSON strings in the DB (TaskRow),
    so we auto-parse them via field_validator.
    """

    payload: dict = {}
    result: dict = {}

    @field_validator("payload", "result", mode="before")
    @classmethod
    def _parse_json_str(cls, v: object) -> dict:
        if isinstance(v, str):
            try:
                return json.loads(v) if v else {}
            except (json.JSONDecodeError, ValueError):
                return {}
        if isinstance(v, dict):
            return v
        return {}

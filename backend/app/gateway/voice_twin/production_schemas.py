"""Pydantic schemas for voice production task API endpoints."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from app.gateway._shared.task_framework.schemas import ensure_utc

# --- Requests ---


class ScriptToAudioRequest(BaseModel):
    """Request body for submitting a script-to-audio task."""

    voice_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1, max_length=100000)
    speed_ratio: float = Field(default=1.0, ge=0.5, le=2.0)
    output_format: str = Field(default="mp3", pattern="^(mp3|wav|ogg)$")
    title: str | None = None


class PptToVideoRequest(BaseModel):
    """Request body (form fields) for submitting a PPT-to-video task."""

    voice_id: str = Field(..., min_length=1)
    preset: str = Field(default="none")


# --- Responses ---


class ProductionTaskResponse(BaseModel):
    """Production task record returned to frontend."""

    model_config = {"from_attributes": True}

    id: str
    user_id: str
    task_type: str
    status: str
    progress: int
    voice_id: str | None = None
    voice_name: str | None = None
    title: str | None = None
    options: dict = Field(default_factory=dict)
    input_path: str | None = None
    slide_count: int | None = None
    output_path: str | None = None
    file_size: int | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 批次 C (2026-08-13)：每任务花费。TaskRow 无 cost 列——from_task_row 是同步的，
    # 由端点异步算完后赋值（task_cost.attach_task_costs）。
    cost: float | None = None
    currency: str | None = None
    # C4（2026-08-27 成本可信化）：花费来源标注（real/estimated/mixed/None）。
    # attach_task_costs 对 TaskBrief 与本模型一律事后赋值——缺此字段时赋值即
    # ValueError（pydantic 禁写未声明字段），voice_twin 列表/详情 500。
    amount_source: str | None = None

    @classmethod
    def from_task_row(cls, task: object) -> ProductionTaskResponse:
        """Build response from a TaskRow (unified_tasks)."""
        payload = json.loads(getattr(task, "payload", None) or "{}")
        result = json.loads(getattr(task, "result", None) or "{}")
        return cls(
            id=task.id,
            user_id=task.user_id,
            task_type=task.task_type,
            status=task.status,
            progress=task.progress,
            voice_id=payload.get("voice_id"),
            voice_name=payload.get("voice_name"),
            title=payload.get("title"),
            options=payload.get("options") or {},
            input_path=payload.get("input_path") or payload.get("pptx_path"),
            slide_count=result.get("slide_count"),
            output_path=result.get("output_path"),
            file_size=result.get("file_size"),
            error_message=task.error_message,
            created_at=ensure_utc(task.created_at),
            updated_at=ensure_utc(task.updated_at),
        )

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.gateway._shared.personal_usage.service import get_personal_usage
from app.gateway._shared.personal_usage.types import UsageCapability, UsageCostSource, UsageStatus, UsageTask
from app.gateway.authz import require_permission
from deerflow.persistence.engine import get_session_factory
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/usage", tags=["personal-usage"])


class UsageWindowResponse(BaseModel):
    days: int
    start_date: str
    end_date: str


class UsageSummaryResponse(BaseModel):
    cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None


class UsageModelResponse(BaseModel):
    name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    total_tokens: int
    cost: float | None
    cost_source: UsageCostSource


class UsageTaskResponse(BaseModel):
    task_key: str
    source_id: str
    capability: UsageCapability
    title: str
    status: UsageStatus
    cost: float | None
    llm_cost: float | None
    media_cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None
    models: list[UsageModelResponse]
    updated_at: datetime | None


class PersonalUsageResponse(BaseModel):
    window: UsageWindowResponse
    summary: UsageSummaryResponse
    tasks: list[UsageTaskResponse]
    total_tasks: int
    has_more: bool


def _task_response(task: UsageTask) -> UsageTaskResponse:
    return UsageTaskResponse(
        task_key=task.task_key,
        source_id=task.source_id,
        capability=task.capability,
        title=task.title,
        status=task.status,
        cost=task.cost,
        llm_cost=task.llm_cost,
        media_cost=task.media_cost,
        currency=task.currency,
        cost_source=task.cost_source,
        total_tokens=task.total_tokens,
        models=[
            UsageModelResponse(
                name=model.name,
                input_tokens=model.input_tokens,
                output_tokens=model.output_tokens,
                cache_read_tokens=model.cache_read_tokens,
                total_tokens=model.total_tokens,
                cost=model.cost,
                cost_source=model.cost_source,
            )
            for model in task.models
        ],
        updated_at=task.updated_at,
    )


@router.get("", response_model=PersonalUsageResponse)
@require_permission("runs", "read")
async def personal_usage_endpoint(
    request: Request,
    days: int = Query(default=7),
    capability: UsageCapability | Literal["all"] = Query(default="all"),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> PersonalUsageResponse:
    if days not in {7, 14, 30}:
        raise HTTPException(status_code=422, detail="days must be one of 7, 14, 30")
    session_factory = get_session_factory()
    if session_factory is None:
        raise HTTPException(
            status_code=503,
            detail="Personal usage requires a SQL database backend; set database.backend to sqlite or postgres in config.yaml.",
        )
    auth = getattr(request.state, "auth", None)
    user = getattr(auth, "user", None) if auth is not None else getattr(request.state, "user", None)
    user_id = str(user.id) if user is not None else get_effective_user_id()
    result = await get_personal_usage(
        session_factory,
        user_id=user_id,
        days=days,
        tz_offset_minutes=tz_offset_minutes,
        capability=capability,
        offset=offset,
        limit=limit,
    )
    return PersonalUsageResponse(
        window=UsageWindowResponse(days=result.window.days, start_date=result.window.start_date, end_date=result.window.end_date),
        summary=UsageSummaryResponse(
            cost=result.summary.cost,
            currency=result.summary.currency,
            cost_source=result.summary.cost_source,
            total_tokens=result.summary.total_tokens,
        ),
        tasks=[_task_response(task) for task in result.tasks],
        total_tasks=result.total_tasks,
        has_more=result.has_more,
    )

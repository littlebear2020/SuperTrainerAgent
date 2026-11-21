from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.gateway._shared.media_pricing_resolver import _load_media_pricing
from app.gateway._shared.personal_usage.agent import _aggregate_llm_rows, _aggregate_media_rows, _combine_costs
from app.gateway._shared.personal_usage.types import UsageCapability, UsageTask, UsageTaskEntry, UsageWindow, merge_cost_source, normalize_usage_status
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.routers.console import _as_utc, _build_pricing_map
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.run.model import RunRow


async def aggregate_video_usage(session, *, user_id: str, window: UsageWindow, capability: UsageCapability | str) -> list[UsageTask]:
    if capability in {"course-video", "creative-studio", "ppt-master", "podcast-studio"}:
        return []
    task_types = ["ai_image", "ai_video"] if capability == "all" else [capability]
    tasks = (
        (
            await session.execute(
                select(TaskRow).where(
                    TaskRow.user_id == user_id,
                    TaskRow.task_type.in_(task_types),
                )
            )
        )
        .scalars()
        .all()
    )
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    llm_rows = (
        (
            await session.execute(
                select(RunRow).where(
                    RunRow.thread_id.in_(task_ids),
                    RunRow.operation_kind == "llm_bookkeeping",
                    RunRow.user_id == user_id,
                    RunRow.created_at >= window.start_utc,
                    RunRow.created_at < window.end_utc,
                )
            )
        )
        .scalars()
        .all()
    )
    media_rows = (
        (
            await session.execute(
                select(ExternalApiCallRow).where(
                    ExternalApiCallRow.task_id.in_(task_ids),
                    ExternalApiCallRow.user_id == user_id,
                    ExternalApiCallRow.created_at >= window.start_utc,
                    ExternalApiCallRow.created_at < window.end_utc,
                )
            )
        )
        .scalars()
        .all()
    )
    llm_by_task: dict[str, list[RunRow]] = defaultdict(list)
    media_by_task: dict[str, list[ExternalApiCallRow]] = defaultdict(list)
    for row in llm_rows:
        llm_by_task[row.thread_id].append(row)
    for row in media_rows:
        if row.task_id is not None:
            media_by_task[row.task_id].append(row)

    selected = [task for task in tasks if (window.start_utc <= (_as_utc(task.created_at) or task.created_at) < window.end_utc) or llm_by_task.get(task.id) or media_by_task.get(task.id)]
    if not selected:
        return []
    pricing = await _build_pricing_map(get_session_factory())
    if media_rows:
        await _load_media_pricing()

    result: list[UsageTaskEntry] = []
    for task in selected:
        llm = _aggregate_llm_rows(llm_by_task[task.id], pricing)
        media_cost, media_currency, media_source, media_mixed = _aggregate_media_rows(media_by_task[task.id])
        cost, currency = _combine_costs(llm.cost, llm.currency, media_cost, media_currency, media_mixed)
        activity_candidates = [task.created_at]
        activity_candidates.extend(row.created_at for row in llm_by_task[task.id])
        activity_candidates.extend(row.created_at for row in media_by_task[task.id])
        activity_at = max(_as_utc(value) or value for value in activity_candidates)
        result.append(
            UsageTaskEntry(
                task_key=f"video:{task.id}",
                source_id=task.id,
                capability=task.task_type,
                title=task.title or task.id,
                status=normalize_usage_status(task.status),
                cost=cost,
                llm_cost=llm.cost,
                media_cost=media_cost,
                currency=currency,
                cost_source=merge_cost_source(llm.cost_source, media_source),
                total_tokens=llm.total_tokens,
                models=llm.models,
                updated_at=_as_utc(task.updated_at),
                activity_at=activity_at,
            )
        )
    result.sort(key=lambda item: (item.activity_at, item.task_key), reverse=True)
    return result

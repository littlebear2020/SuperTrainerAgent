from __future__ import annotations

from typing import Literal

from app.gateway._shared.personal_usage.agent import aggregate_agent_usage
from app.gateway._shared.personal_usage.types import (
    PersonalUsageResult,
    UsageCapability,
    UsageCostSource,
    UsageSummary,
    UsageTask,
    make_usage_window,
    merge_cost_source,
)
from app.gateway._shared.personal_usage.video import aggregate_video_usage


def _summarize(tasks: list[UsageTask]) -> UsageSummary:
    known_tokens = [task.total_tokens for task in tasks if task.total_tokens is not None]
    known_costs = [task for task in tasks if task.cost is not None]
    currencies = {task.currency for task in known_costs if task.currency is not None}
    cost = None
    currency = None
    if known_costs and len(currencies) <= 1:
        cost = sum(task.cost or 0.0 for task in known_costs)
        currency = next(iter(currencies), None)
    cost_source: UsageCostSource = None
    for task in tasks:
        cost_source = merge_cost_source(cost_source, task.cost_source)
    return UsageSummary(
        cost=cost,
        currency=currency,
        cost_source=cost_source,
        total_tokens=sum(known_tokens) if known_tokens else None,
    )


async def get_personal_usage(
    session_factory,
    *,
    user_id: str,
    days: int,
    tz_offset_minutes: int,
    capability: UsageCapability | Literal["all"] = "all",
    offset: int = 0,
    limit: int = 20,
) -> PersonalUsageResult:
    window = make_usage_window(days, tz_offset_minutes)
    async with session_factory() as session:
        agent_tasks = await aggregate_agent_usage(session, user_id=user_id, window=window, capability=capability)
        video_tasks = await aggregate_video_usage(session, user_id=user_id, window=window, capability=capability)
    all_tasks = [*agent_tasks, *video_tasks]
    all_tasks.sort(key=lambda task: (task.activity_at, task.task_key), reverse=True)
    total_tasks = len(all_tasks)
    page = all_tasks[offset : offset + limit]
    return PersonalUsageResult(
        window=window,
        summary=_summarize(all_tasks),
        tasks=page,
        total_tasks=total_tasks,
        has_more=offset + len(page) < total_tasks,
    )

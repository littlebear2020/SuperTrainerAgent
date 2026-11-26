from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.gateway._shared.media_pricing_resolver import _load_media_pricing
from app.gateway._shared.personal_usage.agent import _aggregate_llm_rows, _aggregate_media_rows, _combine_costs
from app.gateway._shared.personal_usage.tombstones import load_tombstones
from app.gateway._shared.personal_usage.types import UsageCapability, UsageStatus, UsageTask, UsageTaskEntry, UsageWindow, merge_cost_source, normalize_usage_status
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.routers.console import _as_utc, _build_pricing_map
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.run.model import RunRow


@dataclass
class _Subject:
    """现存任务或删除快照的统一视图：后面的计价与聚合不区分来源。"""

    id: str
    capability: str
    title: str | None
    status: UsageStatus
    created_at: datetime
    updated_at: datetime | None
    deleted: bool


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
    # 删除快照只补「没有现存行」的对象：同一 id 现存行优先（快照写入后删除失败时不重复计）。
    live_ids = {task.id for task in tasks}
    orphans = [row for row in await load_tombstones(session, user_id=user_id, kind="video", capabilities=task_types) if row.subject_id not in live_ids]
    if not tasks and not orphans:
        return []
    task_ids = [*live_ids, *(row.subject_id for row in orphans)]
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

    subjects = [
        _Subject(
            id=task.id,
            capability=task.task_type,
            title=task.title or None,
            status=normalize_usage_status(task.status),
            created_at=task.created_at,
            updated_at=_as_utc(task.updated_at),
            deleted=False,
        )
        for task in tasks
        if (window.start_utc <= (_as_utc(task.created_at) or task.created_at) < window.end_utc) or llm_by_task.get(task.id) or media_by_task.get(task.id)
    ]
    # 快照对象没有「窗口内创建」这条入口：只有窗口内确有账本行才列出。
    subjects.extend(
        _Subject(
            id=row.subject_id,
            capability=row.capability,
            title=row.title,
            status=normalize_usage_status(row.status),
            created_at=row.subject_created_at,
            updated_at=_as_utc(row.deleted_at),
            deleted=True,
        )
        for row in orphans
        if llm_by_task.get(row.subject_id) or media_by_task.get(row.subject_id)
    )
    if not subjects:
        return []
    pricing = await _build_pricing_map(get_session_factory())
    if media_rows:
        await _load_media_pricing()

    result: list[UsageTaskEntry] = []
    for subject in subjects:
        llm = _aggregate_llm_rows(llm_by_task[subject.id], pricing)
        media_cost, media_currency, media_source, media_mixed, unpriced_media = _aggregate_media_rows(media_by_task[subject.id])
        cost, currency = _combine_costs(llm.cost, llm.currency, media_cost, media_currency, media_mixed)
        activity_candidates = [subject.created_at]
        activity_candidates.extend(row.created_at for row in llm_by_task[subject.id])
        activity_candidates.extend(row.created_at for row in media_by_task[subject.id])
        activity_at = max(_as_utc(value) or value for value in activity_candidates)
        result.append(
            UsageTaskEntry(
                task_key=f"video:{subject.id}",
                source_id=subject.id,
                capability=subject.capability,
                title=subject.title,
                status=subject.status,
                cost=cost,
                llm_cost=llm.cost,
                media_cost=media_cost,
                unpriced_media_calls=unpriced_media,
                currency=currency,
                cost_source=merge_cost_source(llm.cost_source, media_source),
                total_tokens=llm.total_tokens,
                models=llm.models,
                updated_at=subject.updated_at,
                deleted=subject.deleted,
                activity_at=activity_at,
            )
        )
    result.sort(key=lambda item: (item.activity_at, item.task_key), reverse=True)
    return result

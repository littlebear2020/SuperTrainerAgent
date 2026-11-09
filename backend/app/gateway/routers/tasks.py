"""Generic cross-domain task API router — unified query/delete across all domains.

Framework doc §6.3: /api/tasks provides domain-agnostic access to unified_tasks.
Each domain's Service handles cancel + cleanup on delete.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request

from app.gateway._shared.task_framework.schemas import TaskBrief, TaskDetail
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

# Maps domain → app.state attribute name for the corresponding Service
_DOMAIN_SERVICE_MAP = {
    "voice_production": "production_service",
    "video_gen": "video_gen_service",
    "content": "content_task_service",
}


def _get_repo(request: Request):

    repo = getattr(request.app.state, "task_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Task repository not available")
    return repo


@router.get("", response_model=list[TaskBrief])
async def list_tasks(
    request: Request,
    domain: str | None = Query(None, description="Filter by domain"),
    status: str | None = Query(None, description="Filter by status"),
    limit: int = Query(50, le=200),
):
    """List tasks for the current user across all domains."""
    repo = _get_repo(request)
    user_id = get_effective_user_id()
    rows = await repo.list_by_user(user_id, domain=domain, status=status, limit=limit)
    briefs = [TaskBrief.model_validate(r, from_attributes=True) for r in rows]
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs(briefs)
    return briefs


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(request: Request, task_id: str):
    """Get full task detail including payload and result."""
    repo = _get_repo(request)
    user_id = get_effective_user_id()
    task = await repo.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    result = json.loads(task.result or "{}")
    # P0-6: 读 subtitle_status（与 video_gen 路由对齐，voice_twin/PPT 也走此端点）
    svc_attr = _DOMAIN_SERVICE_MAP.get(task.domain)
    if svc_attr and isinstance(result, dict):
        svc = getattr(request.app.state, svc_attr, None)
        storage_dir = getattr(svc, "_storage_dir", None) if svc else None
        if storage_dir and task.user_id:
            from pathlib import Path

            task_dir = Path(storage_dir) / task.user_id / "tasks" / task.id
            try:
                # 2026-08-08 P0-1 修：_extract_subtitle_status 唯一定义在 service.py，
                # 此前误从 subtitle_filter 导入（该模块无此名）→ ImportError 被裸
                # except 吞掉，subtitle_status 永久缺失。except 至少留 warning，
                # 防止下一次同类失效再静默。
                from app.gateway.video_gen.service import _extract_subtitle_status

                result.setdefault("subtitle_status", _extract_subtitle_status(task_dir))
            except Exception:
                logger.warning("subtitle_status read failed for task %s", task_id, exc_info=True)
    detail = TaskDetail(
        **TaskBrief.model_validate(task, from_attributes=True).model_dump(),
        payload=json.loads(task.payload or "{}"),
        result=result,
    )
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs([detail])
    return detail


@router.delete("/{task_id}")
async def delete_task(request: Request, task_id: str):
    """Delete a task by routing to the correct domain Service.

    The Service handles cancelling the asyncio.Task + cleaning up the task directory.
    """
    repo = _get_repo(request)
    user_id = get_effective_user_id()
    task = await repo.get(task_id, user_id=user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    svc_attr = _DOMAIN_SERVICE_MAP.get(task.domain)
    if svc_attr is None:
        raise HTTPException(status_code=400, detail=f"Unknown domain: {task.domain}")

    svc = getattr(request.app.state, svc_attr, None)
    if svc is None:
        raise HTTPException(status_code=503, detail=f"Service for domain '{task.domain}' not available")

    deleted = await svc.delete_task(task_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}

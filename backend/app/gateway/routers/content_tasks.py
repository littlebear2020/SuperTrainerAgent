"""Content tasks API router — PPT generation, task status polling, download."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.gateway._shared.task_framework.schemas import TaskBrief, TaskDetail
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/content/tasks", tags=["Content Tasks"])


def _get_service(request: Request):

    svc = getattr(request.app.state, "content_task_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Content task service not available")
    return svc


def _get_writer_service(request: Request):

    svc = getattr(request.app.state, "writer_doc_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Writer service not available")
    return svc


# ── Request models ──────────────────────────────────────────────────────


class CreatePptTaskRequest(BaseModel):
    task_type: str = Field(default="ppt_gen", pattern="^(ppt_gen|pdf_gen)$")
    payload: dict


# ── Endpoints ───────────────────────────────────────────────────────────


@router.post("", response_model=TaskBrief)
async def create_task(request: Request, body: CreatePptTaskRequest):
    svc = _get_service(request)
    user_id = get_effective_user_id()

    payload = dict(body.payload)

    # Resolve doc_json from doc_id if not already provided
    if "doc_json" not in payload and "doc_id" in payload:
        ws = _get_writer_service(request)
        doc = await ws.get(payload["doc_id"], user_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        payload["doc_json"] = doc.content_json

    task = await svc.submit(
        user_id=user_id,
        task_type=body.task_type,
        payload=payload,
    )
    return TaskBrief.model_validate(task, from_attributes=True)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(request: Request, task_id: str):
    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    detail = TaskDetail.model_validate(task, from_attributes=True)
    from app.gateway._shared.task_cost import attach_task_costs

    await attach_task_costs([detail])
    return detail


@router.delete("/{task_id}")
async def delete_task(request: Request, task_id: str):
    """R2-4（2026-10-07）：content 任务补 DELETE——口径与 video task delete
    （``video_gen.delete_video_task``）一致：取消在跑 asyncio task + 清产物
    目录（BaseTaskService.delete_task → _cleanup_task rmtree task_dir）+
    删任务行；无权/不存在返回 404。修前 content 任务只能等其自灭，产物
    （ppt_gen/pdf_gen 输出）无任何清除通道。"""
    svc = _get_service(request)
    user_id = get_effective_user_id()
    deleted = await svc.delete_task(task_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.get("/{task_id}/download")
async def download_task_output(request: Request, task_id: str):
    """Download the generated file (PPT) for a completed task."""
    import json
    from pathlib import Path

    svc = _get_service(request)
    user_id = get_effective_user_id()
    task = await svc.get_task(task_id, user_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status != "ready":
        raise HTTPException(status_code=409, detail="Task not ready")

    result = json.loads(task.result or "{}")
    output_path = result.get("output_path", "")
    if not output_path or not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    filename = Path(output_path).name
    # B4-T2: map media type by task_type (pdf_gen -> application/pdf, etc.)
    from app.gateway.content.pdf_pipeline import task_media_type

    return FileResponse(
        output_path,
        media_type=task_media_type(task.task_type),
        filename=filename,
    )

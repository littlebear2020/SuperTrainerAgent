"""Writer document API router — CRUD for Writer documents + sharing."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from app.gateway.content.schemas import (
    CreateShareRequest,
    SharedDocResponse,
    ShareResponse,
    VersionBrief,
    VersionDetail,
    WriterDocCreate,
    WriterDocListResponse,
    WriterDocResponse,
    WriterDocUpdate,
)
from app.gateway.content.writer_service import _CONFLICT, WriterDocService
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writer/docs", tags=["Writer"])
share_router = APIRouter(tags=["Writer Share"])


def _get_service(request: Request) -> WriterDocService:
    svc = getattr(request.app.state, "writer_doc_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Writer service not available")
    return svc


def _get_user_id() -> str:
    return get_effective_user_id()


@router.post("", response_model=WriterDocResponse)
async def create_doc(request: Request, body: WriterDocCreate):
    svc = _get_service(request)
    user_id = _get_user_id()
    doc = await svc.create(user_id=user_id, title=body.title, content_json=body.content_json, doc_type=body.doc_type)
    return doc


@router.get("", response_model=WriterDocListResponse)
async def list_docs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
    doc_type: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
):
    """B5-T1: paginated document list with search/filter/sort.

    Breaking contract: returns ``{items, has_more}`` instead of ``list[WriterDocResponse]``.
    """
    svc = _get_service(request)
    user_id = _get_user_id()
    # Clamp inputs
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    if search and len(search) > 100:
        search = search[:100]
    if sort_by not in ("updated_at", "created_at", "title"):
        sort_by = "updated_at"
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"
    items, has_more = await svc.list_docs(
        user_id,
        limit=limit,
        offset=offset,
        search=search,
        doc_type=doc_type,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return WriterDocListResponse(
        items=[WriterDocResponse.model_validate(d, from_attributes=True) for d in items],
        has_more=has_more,
    )


@router.get("/{doc_id}", response_model=WriterDocResponse)
async def get_doc(request: Request, doc_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    doc = await svc.get(doc_id, user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.put("/{doc_id}", response_model=WriterDocResponse)
async def update_doc(request: Request, doc_id: str, body: WriterDocUpdate):
    svc = _get_service(request)
    user_id = _get_user_id()
    # force_snapshot signals a destructive content replacement - bypass the
    # 30s auto-throttle so an undo point is guaranteed (reason="destructive").
    force_snapshot = body.force_snapshot
    updates = {k: v for k, v in body.model_dump(exclude={"force_snapshot", "base_version"}).items() if v is not None}
    if not updates:
        doc = await svc.get(doc_id, user_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc

    # B2-T2: CAS via integer version (preferred path). base_version is checked
    # atomically inside the UPDATE statement -- no TOCTOU window.
    if body.base_version is not None:
        # Auto-save version snapshot before content changes
        if "content_json" in updates:
            reason = "destructive" if force_snapshot else "auto"
            await svc.create_version(doc_id, user_id, reason=reason)
        # B2-T3 fix: snapshot on title/doc_type changes (rename/retype) --
        # was missing from the CAS path, only existed in the legacy path.
        if "title" in updates or "doc_type" in updates:
            snapshot_reason = "rename" if "title" in updates and "doc_type" not in updates else "retype"
            await svc.create_version(doc_id, user_id, reason=snapshot_reason)
        doc = await svc.update(doc_id, user_id, expected_version=body.base_version, **updates)
        if doc is _CONFLICT:
            # Distinguish 404 vs 409: if the doc exists, it's a version conflict
            existing = await svc.get(doc_id, user_id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Document not found")
            raise HTTPException(status_code=409, detail="文档已在其他窗口被修改，请刷新后再保存")
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc

    # Auto-save version snapshot before content changes
    if "content_json" in updates:
        reason = "destructive" if force_snapshot else "auto"
        await svc.create_version(doc_id, user_id, reason=reason)
    # B2-T3: snapshot on title/doc_type changes (rename/retype)
    if "title" in updates or "doc_type" in updates:
        snapshot_reason = "rename" if "title" in updates and "doc_type" not in updates else "retype"
        await svc.create_version(doc_id, user_id, reason=snapshot_reason)
    doc = await svc.update(doc_id, user_id, **updates)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{doc_id}")
async def delete_doc(request: Request, doc_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    deleted = await svc.delete(doc_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"ok": True}


# ── Version History ─────────────────────────────────────────────────────


@router.get("/{doc_id}/versions", response_model=list[VersionBrief])
async def list_versions(request: Request, doc_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    versions = await svc.list_versions(doc_id, user_id)
    return [VersionBrief.model_validate(v, from_attributes=True) for v in versions]


@router.get("/{doc_id}/versions/{version_id}", response_model=VersionDetail)
async def get_version(request: Request, doc_id: str, version_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    version = await svc.get_version(version_id, doc_id, user_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return VersionDetail.model_validate(version, from_attributes=True)


@router.post("/{doc_id}/versions/{version_id}/restore", response_model=WriterDocResponse)
async def restore_version(request: Request, doc_id: str, version_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    doc = await svc.restore_version(version_id, doc_id, user_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return WriterDocResponse.model_validate(doc, from_attributes=True)


# ── Sharing ──────────────────────────────────────────────────────────────


@router.post("/{doc_id}/share", response_model=ShareResponse)
async def create_share(request: Request, doc_id: str, body: CreateShareRequest):
    svc = _get_service(request)
    user_id = _get_user_id()
    try:
        share = await svc.create_share(
            doc_id,
            user_id,
            expires_days=body.expires_days,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found")
    return ShareResponse.model_validate(share, from_attributes=True)


@router.get("/{doc_id}/share", response_model=ShareResponse | None)
async def get_share(request: Request, doc_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    share = await svc.get_share(doc_id, user_id)
    if share is None:
        return None
    return ShareResponse.model_validate(share, from_attributes=True)


@router.delete("/{doc_id}/share")
async def revoke_share(request: Request, doc_id: str):
    svc = _get_service(request)
    user_id = _get_user_id()
    revoked = await svc.revoke_share(doc_id, user_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Share not found")
    return {"ok": True}


@share_router.get("/api/writer/share/{share_token}", response_model=SharedDocResponse)
async def get_shared_doc(request: Request, share_token: str):
    svc = _get_service(request)
    doc = await svc.get_by_share_token(share_token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Shared document not found or expired")
    return SharedDocResponse.model_validate(doc, from_attributes=True)

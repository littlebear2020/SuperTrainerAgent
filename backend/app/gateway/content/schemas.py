"""Schemas for WriterDoc CRUD and content task APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["article", "case", "script", "podcast", "course", "general"]


class WriterDocCreate(BaseModel):
    title: str = Field(default="未命名文档", max_length=200)
    # 2026-09-02 C2:正文上限 20 万字(正常教案远达不到;防超长粘贴/失控生成打爆内存与 token)
    content_json: str = Field(default="{}", max_length=200_000)
    doc_type: DocType = "general"


class WriterDocUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    content_json: str | None = Field(None, max_length=200_000)
    doc_type: DocType | None = None
    force_snapshot: bool = False
    # B2-T2: optimistic lock via integer version.
    # When present, the PUT 409s if the stored version doesn't match.
    base_version: int | None = None


class WriterDocResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    user_id: str
    title: str
    content_json: str = "{}"
    doc_type: str = "general"
    # B2-T2: version field for CAS optimistic locking
    version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WriterDocListResponse(BaseModel):
    """B5-T1: paginated list response (breaking contract change).

    Replaces the old ``list[WriterDocResponse]`` with a wrapper that carries
    ``has_more`` for pagination. No ``total`` count (saves a query, matches
    console.py pattern).
    """

    items: list[WriterDocResponse]
    has_more: bool


class ShareResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    doc_id: str
    owner_id: str
    share_token: str
    expires_at: datetime | None = None
    created_at: datetime | None = None


class CreateShareRequest(BaseModel):
    expires_days: int | None = Field(default=None, ge=1, le=365)


class SharedDocResponse(BaseModel):
    """Public share view — never exposes owner user_id (P2-29)."""

    model_config = {"from_attributes": True}

    id: str
    title: str
    content_json: str = "{}"
    doc_type: str = "general"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class VersionBrief(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    doc_id: str
    version_number: int
    title: str
    snapshot_reason: str = "auto"
    created_at: datetime | None = None


class VersionDetail(VersionBrief):
    content_json: str = ""

"""ORM model for external API call tracking."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ExternalApiCallRow(Base):
    """One external pay-per-call API invocation (F1).

    Tracks ai_image / ai_video / ViMax bridge / TTS calls for the
    operations console. ``metadata_json`` (B1) stores sparse fields like
    ``amount`` (provider real-bill cost) without schema migration churn.
    """

    __tablename__ = "external_api_calls"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # B1: 稀疏 JSON——只有网关返回 amount 时才有值（alembic g9c3d5e7f2a4 补列）。
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # 批次 C Phase C (2026-08-13)：每任务媒体成本归因。补列走 alembic revision
    # b3d4e5f6a7c8（ensure_*_schema 自愈机制已退役）。迁移前存量行 task_id 为
    # NULL——历史媒体花费无法归因到任务（前向有效，符合预期）。
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_external_api_calls_created_at", "created_at"),)

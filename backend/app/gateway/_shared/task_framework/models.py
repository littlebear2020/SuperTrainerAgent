"""TaskRow — unified task table replacing voice_production_tasks."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class TaskRow(Base):
    """All Gateway-side async tasks (voice production, video gen, content) in one table.

    Worker claims composing tasks from this table.
    """

    __tablename__ = "unified_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Task routing
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    # voice_production | video_gen | content
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # script_to_audio | ppt_to_video | stock_footage | ai_visual | remotion | ...

    # Scheduling state
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=50)

    # Input/output (JSON strings, heterogeneous across domains)
    payload: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # B2: per-frame dirty tracking for batch recompose (segments-type engines only).
    # JSON array of frame indices the user edited since last render: "[0, 3, 5]"
    dirty_frames: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Worker claim & lease
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False)
    # P2-4: 落库 title/thumbnail_url，避免每次列表请求都从 sidecar 文件读
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        Index("ix_unified_tasks_user_domain_created", "user_id", "domain", "created_at"),
        Index("ix_unified_tasks_claim", "status", "claimed_by", "domain"),
    )

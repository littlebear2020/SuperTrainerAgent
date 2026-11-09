"""WriterDoc - user document model for TipTap Writer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class WriterDoc(Base):
    __tablename__ = "writer_docs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    doc_type: Mapped[str] = mapped_column(String(20), default="general", nullable=False)
    # B2-T1: optimistic lock version. CAS update increments it; frontend
    # sends base_version to detect stale writes. Default 0 for existing rows.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)

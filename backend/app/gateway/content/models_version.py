"""WriterDocVersion - document version snapshot model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class WriterDocVersion(Base):
    __tablename__ = "writer_doc_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, default="")
    snapshot_reason: Mapped[str] = mapped_column(String(50), default="auto")
    # B2-T3: doc_type snapshot. Nullable because old version rows have
    # unknown type (NOT backfilled -- restore must not revert article->general).
    doc_type: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("doc_id", "version_number", name="uq_wdv_doc_version"),)

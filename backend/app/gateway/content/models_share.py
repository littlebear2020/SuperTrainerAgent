"""WriterDocShare — document sharing token model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class WriterDocShare(Base):
    __tablename__ = "writer_doc_shares"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    share_token: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(20), default="view")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

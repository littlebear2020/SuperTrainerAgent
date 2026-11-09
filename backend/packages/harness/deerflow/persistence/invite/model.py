"""ORM model for the invite_config table.

Single-row table storing the global invite code configuration.
When ``enabled`` is False, registration proceeds without an invite code.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class InviteConfigRow(Base):
    __tablename__ = "invite_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    current_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")

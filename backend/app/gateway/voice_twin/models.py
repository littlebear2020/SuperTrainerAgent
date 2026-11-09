"""SQLAlchemy model for voice twin records."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class VoiceTwinRow(Base):
    """Persisted voice clone record tracking training state and metadata."""

    __tablename__ = "voice_twins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    voice_name: Mapped[str] = mapped_column(String(100), nullable=False)
    speaker_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)

    # Clone parameters
    model_type: Mapped[int] = mapped_column(Integer, default=5)
    language: Mapped[int] = mapped_column(Integer, default=0)
    extra_params: Mapped[str | None] = mapped_column(String, default="{}")

    # State: uploading -> training -> ready / failed
    status: Mapped[str] = mapped_column(String(20), default="uploading", nullable=False)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    demo_audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Source audio metadata
    source_audio_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_audio_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_audio_duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<VoiceTwinRow id={self.id!r} name={self.voice_name!r} status={self.status!r}>"

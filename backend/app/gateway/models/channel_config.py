"""Channel configuration models for user-level IM integration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ChannelConfig(Base):
    """User-level channel configuration with encrypted credentials.

    Stores per-user IM channel configurations (Feishu, WeChat, etc.)
    with AES-256-GCM encrypted credentials.
    """

    __tablename__ = "channel_configs"
    __table_args__ = (UniqueConstraint("user_id", "channel_type", name="uq_user_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # AES-256-GCM encrypted JSON: {"app_id": "...", "app_secret": "..."}
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)

    # Channel-specific config (not encrypted, not sensitive)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    # Status
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<ChannelConfig user_id={self.user_id!r} channel_type={self.channel_type!r} enabled={self.enabled}>"


class ChannelThreadMapping(Base):
    """Maps channel conversations to DeerFlow threads.

    Tracks which channel conversation (chat_id + topic_id) maps to
    which DeerFlow thread_id for a specific user and channel type.
    """

    __tablename__ = "channel_thread_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # Platform-specific identifiers
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # DeerFlow thread
    thread_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<ChannelThreadMapping user_id={self.user_id!r} channel_type={self.channel_type!r} chat_id={self.chat_id!r} thread_id={self.thread_id!r}>"

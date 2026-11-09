"""SQL-backed user channel store for multi-user thread mapping."""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.models.channel_config import ChannelThreadMapping


class UserChannelStore:
    """Manages thread mappings for a specific user's channel conversations.

    Each user gets isolated storage for their IM conversation to thread mappings.
    """

    def __init__(self, user_id: str, session_factory: async_sessionmaker[AsyncSession]):
        """Initialize user channel store.

        Args:
            user_id: User ID string
            session_factory: SQLAlchemy async session factory
        """
        self.user_id = user_id
        self.session_factory = session_factory

    async def get_thread_id(self, channel_type: str, chat_id: str, topic_id: str | None = None) -> str | None:
        """Get thread ID for a channel conversation.

        Args:
            channel_type: Type of channel (feishu, wechat, etc.)
            chat_id: Chat/group ID from IM platform
            topic_id: Optional topic/thread ID within the chat

        Returns:
            Thread ID if mapping exists, None otherwise
        """
        async with self.session_factory() as session:
            stmt = select(ChannelThreadMapping).where(
                ChannelThreadMapping.user_id == str(self.user_id),
                ChannelThreadMapping.channel_type == channel_type,
                ChannelThreadMapping.chat_id == chat_id,
                ChannelThreadMapping.topic_id == topic_id,
            )
            result = await session.execute(stmt)
            mapping = result.scalar_one_or_none()
            return mapping.thread_id if mapping else None

    async def set_thread_id(
        self,
        channel_type: str,
        chat_id: str,
        thread_id: str,
        topic_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Set or update thread ID for a channel conversation.

        Args:
            channel_type: Type of channel (feishu, wechat, etc.)
            chat_id: Chat/group ID from IM platform
            thread_id: DeerFlow thread ID to map to
            topic_id: Optional topic/thread ID within the chat
            metadata: Optional metadata to store (not persisted in current schema)
        """
        async with self.session_factory() as session:
            # Try to find existing mapping
            stmt = select(ChannelThreadMapping).where(
                ChannelThreadMapping.user_id == str(self.user_id),
                ChannelThreadMapping.channel_type == channel_type,
                ChannelThreadMapping.chat_id == chat_id,
                ChannelThreadMapping.topic_id == topic_id,
            )
            result = await session.execute(stmt)
            mapping = result.scalar_one_or_none()

            if mapping:
                # Update existing mapping
                mapping.thread_id = thread_id
            else:
                # Create new mapping
                mapping = ChannelThreadMapping(
                    user_id=str(self.user_id),
                    channel_type=channel_type,
                    chat_id=chat_id,
                    topic_id=topic_id,
                    thread_id=thread_id,
                )
                session.add(mapping)

            await session.commit()

    async def remove(self, channel_type: str, chat_id: str, topic_id: str | None = None) -> bool:
        """Remove thread mapping for a channel conversation.

        Args:
            channel_type: Type of channel
            chat_id: Chat/group ID from IM platform
            topic_id: Optional topic/thread ID within the chat

        Returns:
            True if mapping was removed, False if not found
        """
        async with self.session_factory() as session:
            stmt = delete(ChannelThreadMapping).where(
                ChannelThreadMapping.user_id == str(self.user_id),
                ChannelThreadMapping.channel_type == channel_type,
                ChannelThreadMapping.chat_id == chat_id,
                ChannelThreadMapping.topic_id == topic_id,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def list_mappings(self, channel_type: str | None = None) -> list[dict[str, Any]]:
        """List all thread mappings for this user.

        Args:
            channel_type: Optional filter by channel type

        Returns:
            List of mapping dictionaries
        """
        async with self.session_factory() as session:
            stmt = select(ChannelThreadMapping).where(
                ChannelThreadMapping.user_id == str(self.user_id),
            )
            if channel_type:
                stmt = stmt.where(ChannelThreadMapping.channel_type == channel_type)

            result = await session.execute(stmt)
            mappings = result.scalars().all()

            return [
                {
                    "channel_type": m.channel_type,
                    "chat_id": m.chat_id,
                    "topic_id": m.topic_id,
                    "thread_id": m.thread_id,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                }
                for m in mappings
            ]

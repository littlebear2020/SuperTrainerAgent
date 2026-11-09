"""Tests for UserChannelStore."""

from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from app.channels.user_store import UserChannelStore


def test_user_channel_store_init():
    sf = MagicMock()
    store = UserChannelStore(user_id="user-1", session_factory=sf)
    assert store.user_id == "user-1"
    assert store.session_factory is sf


@pytest_asyncio.fixture(loop_scope="function")
async def session_factory(tmp_path):
    """Real async SQLAlchemy session factory over a scratch sqlite file."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.base import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user_store.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_set_then_get_thread_id_round_trip(session_factory):
    """set→get 行为闭环：miss → set → hit → overwrite；并钉用户间隔离。"""
    store = UserChannelStore(user_id="user-1", session_factory=session_factory)

    assert await store.get_thread_id("feishu", "chat-1") is None

    await store.set_thread_id("feishu", "chat-1", "thread-abc")
    assert await store.get_thread_id("feishu", "chat-1") == "thread-abc"

    # Update path: same (user, channel, chat) mapping is overwritten, not duplicated.
    await store.set_thread_id("feishu", "chat-1", "thread-def")
    assert await store.get_thread_id("feishu", "chat-1") == "thread-def"
    mappings = await store.list_mappings()
    assert len(mappings) == 1

    # Per-user isolation: another user's store sees no mapping.
    other = UserChannelStore(user_id="user-2", session_factory=session_factory)
    assert await other.get_thread_id("feishu", "chat-1") is None
    assert await other.list_mappings() == []

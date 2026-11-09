"""Tests for FeedbackRepository and follow-up association.

Uses temp SQLite DB for ORM tests.
"""

import pytest

from deerflow.persistence.feedback import FeedbackRepository


@pytest.fixture()
async def feedback_repo(tmp_path):
    """Init a temp SQLite engine per test; always close it afterwards."""
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        yield FeedbackRepository(get_session_factory())
    finally:
        await close_engine()


# -- FeedbackRepository --


class TestFeedbackRepository:
    @pytest.mark.anyio
    async def test_create_positive(self, feedback_repo):
        record = await feedback_repo.create(run_id="r1", thread_id="t1", rating=1)
        assert record["feedback_id"]
        assert record["rating"] == 1
        assert record["run_id"] == "r1"
        assert record["thread_id"] == "t1"
        assert "created_at" in record

    @pytest.mark.anyio
    async def test_create_negative_with_comment(self, feedback_repo):
        record = await feedback_repo.create(
            run_id="r1",
            thread_id="t1",
            rating=-1,
            comment="Response was inaccurate",
        )
        assert record["rating"] == -1
        assert record["comment"] == "Response was inaccurate"

    @pytest.mark.anyio
    async def test_create_with_message_id(self, feedback_repo):
        record = await feedback_repo.create(run_id="r1", thread_id="t1", rating=1, message_id="msg-42")
        assert record["message_id"] == "msg-42"

    @pytest.mark.anyio
    async def test_create_with_owner(self, feedback_repo):
        record = await feedback_repo.create(run_id="r1", thread_id="t1", rating=1, user_id="user-1")
        assert record["user_id"] == "user-1"

    @pytest.mark.anyio
    async def test_create_invalid_rating_zero(self, feedback_repo):
        with pytest.raises(ValueError):
            await feedback_repo.create(run_id="r1", thread_id="t1", rating=0)

    @pytest.mark.anyio
    async def test_create_invalid_rating_five(self, feedback_repo):
        with pytest.raises(ValueError):
            await feedback_repo.create(run_id="r1", thread_id="t1", rating=5)

    @pytest.mark.anyio
    async def test_get(self, feedback_repo):
        created = await feedback_repo.create(run_id="r1", thread_id="t1", rating=1)
        fetched = await feedback_repo.get(created["feedback_id"])
        assert fetched is not None
        assert fetched["feedback_id"] == created["feedback_id"]
        assert fetched["rating"] == 1

    @pytest.mark.anyio
    async def test_get_nonexistent(self, feedback_repo):
        assert await feedback_repo.get("nonexistent") is None

    @pytest.mark.anyio
    async def test_list_by_run(self, feedback_repo):
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=1, user_id="user-1")
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=-1, user_id="user-2")
        await feedback_repo.create(run_id="r2", thread_id="t1", rating=1, user_id="user-1")
        results = await feedback_repo.list_by_run("t1", "r1", user_id=None)
        assert len(results) == 2
        assert all(r["run_id"] == "r1" for r in results)

    @pytest.mark.anyio
    async def test_list_by_thread(self, feedback_repo):
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=1)
        await feedback_repo.create(run_id="r2", thread_id="t1", rating=-1)
        await feedback_repo.create(run_id="r3", thread_id="t2", rating=1)
        results = await feedback_repo.list_by_thread("t1")
        assert len(results) == 2
        assert all(r["thread_id"] == "t1" for r in results)

    @pytest.mark.anyio
    async def test_delete(self, feedback_repo):
        created = await feedback_repo.create(run_id="r1", thread_id="t1", rating=1)
        deleted = await feedback_repo.delete(created["feedback_id"])
        assert deleted is True
        assert await feedback_repo.get(created["feedback_id"]) is None

    @pytest.mark.anyio
    async def test_delete_nonexistent(self, feedback_repo):
        deleted = await feedback_repo.delete("nonexistent")
        assert deleted is False

    @pytest.mark.anyio
    async def test_aggregate_by_run(self, feedback_repo):
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=1, user_id="user-1")
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=1, user_id="user-2")
        await feedback_repo.create(run_id="r1", thread_id="t1", rating=-1, user_id="user-3")
        stats = await feedback_repo.aggregate_by_run("t1", "r1")
        assert stats["total"] == 3
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["run_id"] == "r1"

    @pytest.mark.anyio
    async def test_aggregate_empty(self, feedback_repo):
        stats = await feedback_repo.aggregate_by_run("t1", "r1")
        assert stats["total"] == 0
        assert stats["positive"] == 0
        assert stats["negative"] == 0

    @pytest.mark.anyio
    async def test_upsert_creates_new(self, feedback_repo):
        record = await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=1, user_id="u1")
        assert record["rating"] == 1
        assert record["feedback_id"]
        assert record["user_id"] == "u1"

    @pytest.mark.anyio
    async def test_upsert_updates_existing(self, feedback_repo):
        first = await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=1, user_id="u1")
        second = await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=-1, user_id="u1", comment="changed my mind")
        assert second["feedback_id"] == first["feedback_id"]
        assert second["rating"] == -1
        assert second["comment"] == "changed my mind"

    @pytest.mark.anyio
    async def test_upsert_different_users_separate(self, feedback_repo):
        r1 = await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=1, user_id="u1")
        r2 = await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=-1, user_id="u2")
        assert r1["feedback_id"] != r2["feedback_id"]
        assert r1["rating"] == 1
        assert r2["rating"] == -1

    @pytest.mark.anyio
    async def test_upsert_invalid_rating(self, feedback_repo):
        with pytest.raises(ValueError):
            await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=0, user_id="u1")

    @pytest.mark.anyio
    async def test_delete_by_run(self, feedback_repo):
        await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=1, user_id="u1")
        deleted = await feedback_repo.delete_by_run(thread_id="t1", run_id="r1", user_id="u1")
        assert deleted is True
        results = await feedback_repo.list_by_run("t1", "r1", user_id="u1")
        assert len(results) == 0

    @pytest.mark.anyio
    async def test_delete_by_run_nonexistent(self, feedback_repo):
        deleted = await feedback_repo.delete_by_run(thread_id="t1", run_id="r1", user_id="u1")
        assert deleted is False

    @pytest.mark.anyio
    async def test_list_by_thread_grouped(self, feedback_repo):
        await feedback_repo.upsert(run_id="r1", thread_id="t1", rating=1, user_id="u1")
        await feedback_repo.upsert(run_id="r2", thread_id="t1", rating=-1, user_id="u1")
        await feedback_repo.upsert(run_id="r3", thread_id="t2", rating=1, user_id="u1")
        grouped = await feedback_repo.list_by_thread_grouped("t1", user_id="u1")
        assert "r1" in grouped
        assert "r2" in grouped
        assert "r3" not in grouped
        assert grouped["r1"]["rating"] == 1
        assert grouped["r2"]["rating"] == -1

    @pytest.mark.anyio
    async def test_list_by_thread_grouped_empty(self, feedback_repo):
        grouped = await feedback_repo.list_by_thread_grouped("t1", user_id="u1")
        assert grouped == {}


# -- Follow-up association --


class TestFollowUpAssociation:
    @pytest.mark.anyio
    async def test_run_records_follow_up_via_memory_store(self):
        """MemoryRunStore stores follow_up_to_run_id in kwargs."""
        from deerflow.runtime.runs.store.memory import MemoryRunStore

        store = MemoryRunStore()
        await store.put("r1", thread_id="t1", status="success")
        # MemoryRunStore doesn't have follow_up_to_run_id as a top-level param,
        # but it can be passed via metadata
        await store.put("r2", thread_id="t1", metadata={"follow_up_to_run_id": "r1"})
        run = await store.get("r2")
        assert run["metadata"]["follow_up_to_run_id"] == "r1"

    @pytest.mark.anyio
    async def test_human_message_has_follow_up_metadata(self):
        """human_message event metadata includes follow_up_to_run_id."""
        from deerflow.runtime.events.store.memory import MemoryRunEventStore

        event_store = MemoryRunEventStore()
        await event_store.put(
            thread_id="t1",
            run_id="r2",
            event_type="human_message",
            category="message",
            content="Tell me more about that",
            metadata={"follow_up_to_run_id": "r1"},
        )
        messages = await event_store.list_messages("t1")
        assert messages[0]["metadata"]["follow_up_to_run_id"] == "r1"

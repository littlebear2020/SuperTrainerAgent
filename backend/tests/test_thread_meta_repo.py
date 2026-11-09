"""Tests for ThreadMetaRepository (SQLAlchemy-backed)."""

import pytest

from deerflow.persistence.thread_meta import ThreadMetaRepository


async def _make_repo(tmp_path):
    from deerflow.persistence.engine import get_session_factory, init_engine

    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    await init_engine("sqlite", url=url, sqlite_dir=str(tmp_path))
    return ThreadMetaRepository(get_session_factory())


async def _cleanup():
    from deerflow.persistence.engine import close_engine

    await close_engine()


class TestThreadMetaRepository:
    @pytest.mark.anyio
    async def test_create_and_get(self, tmp_path):
        repo = await _make_repo(tmp_path)
        record = await repo.create("t1")
        assert record["thread_id"] == "t1"
        assert record["status"] == "idle"
        assert "created_at" in record

        # Verify timestamps include UTC timezone info so browsers parse
        # them correctly (aiosqlite strips tzinfo on read).
        assert "+00:00" in record["created_at"], f"Missing UTC offset: {record['created_at']}"
        assert "+00:00" in record["updated_at"], f"Missing UTC offset: {record['updated_at']}"

        fetched = await repo.get("t1")
        assert fetched is not None
        assert fetched["thread_id"] == "t1"
        await _cleanup()

    @pytest.mark.anyio
    async def test_create_with_assistant_id(self, tmp_path):
        repo = await _make_repo(tmp_path)
        record = await repo.create("t1", assistant_id="agent1")
        assert record["assistant_id"] == "agent1"
        await _cleanup()

    @pytest.mark.anyio
    async def test_create_with_owner_and_display_name(self, tmp_path):
        repo = await _make_repo(tmp_path)
        record = await repo.create("t1", user_id="user1", display_name="My Thread")
        assert record["user_id"] == "user1"
        assert record["display_name"] == "My Thread"
        await _cleanup()

    @pytest.mark.anyio
    async def test_create_with_metadata(self, tmp_path):
        repo = await _make_repo(tmp_path)
        record = await repo.create("t1", metadata={"key": "value"})
        assert record["metadata"] == {"key": "value"}
        await _cleanup()

    @pytest.mark.anyio
    async def test_get_nonexistent(self, tmp_path):
        repo = await _make_repo(tmp_path)
        assert await repo.get("nonexistent") is None
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_no_record_allows(self, tmp_path):
        repo = await _make_repo(tmp_path)
        assert await repo.check_access("unknown", "user1") is True
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_owner_matches(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1", user_id="user1")
        assert await repo.check_access("t1", "user1") is True
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_owner_mismatch(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1", user_id="user1")
        assert await repo.check_access("t1", "user2") is False
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_no_owner_allows_all(self, tmp_path):
        repo = await _make_repo(tmp_path)
        # Explicit user_id=None to bypass the new AUTO default that
        # would otherwise pick up the test user from the autouse fixture.
        await repo.create("t1", user_id=None)
        assert await repo.check_access("t1", "anyone") is True
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_strict_missing_row_denied(self, tmp_path):
        """require_existing=True flips the missing-row case to *denied*.

        Closes the delete-idempotence cross-user gap: after a thread is
        deleted, the row is gone, and the permissive default would let any
        caller "claim" it as untracked. The strict mode demands a row.
        """
        repo = await _make_repo(tmp_path)
        assert await repo.check_access("never-existed", "user1", require_existing=True) is False
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_strict_owner_match_allowed(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1", user_id="user1")
        assert await repo.check_access("t1", "user1", require_existing=True) is True
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_strict_owner_mismatch_denied(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1", user_id="user1")
        assert await repo.check_access("t1", "user2", require_existing=True) is False
        await _cleanup()

    @pytest.mark.anyio
    async def test_check_access_strict_null_owner_still_allowed(self, tmp_path):
        """Even in strict mode, a row with NULL user_id stays shared.

        The strict flag tightens the *missing row* case, not the *shared
        row* case — legacy pre-auth rows that survived a clean migration
        without an owner are still everyone's.
        """
        repo = await _make_repo(tmp_path)
        await repo.create("t1", user_id=None)
        assert await repo.check_access("t1", "anyone", require_existing=True) is True
        await _cleanup()

    @pytest.mark.anyio
    async def test_update_status(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1")
        await repo.update_status("t1", "busy")
        record = await repo.get("t1")
        assert record["status"] == "busy"
        await _cleanup()

    @pytest.mark.anyio
    async def test_delete(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1")
        await repo.delete("t1")
        assert await repo.get("t1") is None
        await _cleanup()

    @pytest.mark.anyio
    async def test_delete_nonexistent_is_noop(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.delete("nonexistent")  # should not raise
        await _cleanup()

    @pytest.mark.anyio
    async def test_update_metadata_merges(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1", metadata={"a": 1, "b": 2})
        await repo.update_metadata("t1", {"b": 99, "c": 3})
        record = await repo.get("t1")
        # Existing key preserved, overlapping key overwritten, new key added
        assert record["metadata"] == {"a": 1, "b": 99, "c": 3}
        await _cleanup()

    @pytest.mark.anyio
    async def test_update_metadata_on_empty(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.create("t1")
        await repo.update_metadata("t1", {"k": "v"})
        record = await repo.get("t1")
        assert record["metadata"] == {"k": "v"}
        await _cleanup()

    @pytest.mark.anyio
    async def test_update_metadata_nonexistent_is_noop(self, tmp_path):
        repo = await _make_repo(tmp_path)
        await repo.update_metadata("nonexistent", {"k": "v"})  # should not raise
        await _cleanup()


class TestServerSideMetadataFilter:
    """R-12 (2026-08-13): metadata filters are pushed into SQL via json_match
    — the old client-side window (limit * 5 + offset rows then Python filter)
    silently short-paged when matches sat outside the window."""

    @pytest.mark.anyio
    async def test_window_outside_matches_not_short_paged(self, tmp_path):
        repo = await _make_repo(tmp_path)
        try:
            # 3 matching rows created FIRST (oldest — they sort to the tail of
            # the recency window), then 60 non-matching newer rows. The old
            # client-side filter only scanned limit * 5 = 50 newest rows, so
            # the matchers sat outside the window and the page came back empty.
            for i in range(3):
                await repo.create(f"m-{i}", metadata={"kind": "target"})
            import time

            time.sleep(0.01)  # ensure updated_at ordering puts matchers at the tail
            for i in range(60):
                await repo.create(f"t-{i:03d}", metadata={"kind": "other"})

            rows = await repo.search(metadata={"kind": "target"}, limit=10, offset=0, user_id=None)
            assert sorted(r["thread_id"] for r in rows) == ["m-0", "m-1", "m-2"], f"short page: {[r['thread_id'] for r in rows]}"
        finally:
            await _cleanup()

    @pytest.mark.anyio
    async def test_unsafe_filter_keys_rejected(self, tmp_path):
        repo = await _make_repo(tmp_path)
        try:
            await repo.create("t1")
            with pytest.raises(ValueError, match="rejected as unsafe"):
                await repo.search(metadata={"bad key!": "x"}, limit=10, offset=0, user_id=None)
        finally:
            await _cleanup()

    @pytest.mark.anyio
    async def test_pinned_filter_matches_true(self, tmp_path):
        repo = await _make_repo(tmp_path)
        try:
            await repo.create("pinned", metadata={"deerflow_pinned": True})
            await repo.create("plain", metadata={})
            rows = await repo.search(metadata={"deerflow_pinned": True}, limit=10, offset=0, user_id=None)
            assert [r["thread_id"] for r in rows] == ["pinned"]
        finally:
            await _cleanup()


class TestConcurrentMetadataMerge:
    """R-12 (2026-08-13): update_metadata locks the row before the
    read-modify-write merge — concurrent callers must not drop each other's
    keys (SQLite: BEGIN IMMEDIATE; others: SELECT FOR UPDATE)."""

    @pytest.mark.anyio
    async def test_concurrent_updates_do_not_lose_keys(self, tmp_path):
        import asyncio

        repo = await _make_repo(tmp_path)
        try:
            await repo.create("t1", user_id=None, metadata={"base": 1})

            async def merge(key: str, value: int) -> None:
                await repo.update_metadata("t1", {key: value}, user_id=None)

            # 10 concurrent merges of distinct keys. Without the lock, SQLite's
            # deferred transactions let two writers read the same base and the
            # last commit drops the other's key.
            await asyncio.gather(*(merge(f"k{i}", i) for i in range(10)))

            final = await repo.get("t1", user_id=None)
            assert final is not None
            for i in range(10):
                assert final["metadata"].get(f"k{i}") == i, f"lost k{i}: {final['metadata']}"
        finally:
            await _cleanup()

    # ── claim_unowned（P1-S1，2026-09-30 上游移植；生产 SQL 路径验收）──

    @pytest.mark.anyio
    async def test_claim_unowned_only_changes_none_owner(self, tmp_path):
        repo = await _make_repo(tmp_path)
        try:
            await repo.create("legacy", user_id=None)
            await repo.create("owned", user_id=None)  # 先建后改，绕开 create 的 user 语义
            await repo.claim_unowned("owned", "original-owner")

            assert await repo.claim_unowned("missing", "owner-a") is False
            assert await repo.claim_unowned("owned", "owner-a") is False
            assert await repo.claim_unowned("legacy", "owner-a") is True
            assert await repo.claim_unowned("legacy", "owner-b") is False

            assert (await repo.get("owned", user_id=None))["user_id"] == "original-owner"
            assert (await repo.get("legacy", user_id=None))["user_id"] == "owner-a"
        finally:
            await _cleanup()

    @pytest.mark.anyio
    async def test_concurrent_claim_unowned_has_exactly_one_winner(self, tmp_path):
        import asyncio

        repo = await _make_repo(tmp_path)
        try:
            await repo.create("legacy-race", user_id=None)

            outcomes = await asyncio.gather(
                repo.claim_unowned("legacy-race", "owner-a"),
                repo.claim_unowned("legacy-race", "owner-b"),
            )

            assert sorted(outcomes) == [False, True]
            record = await repo.get("legacy-race", user_id=None)
            assert record["user_id"] in {"owner-a", "owner-b"}
        finally:
            await _cleanup()

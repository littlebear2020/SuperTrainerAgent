"""Pinned-thread semantics for the ThreadMetaStore backends.

Covers the ``deerflow_pinned`` metadata key:
- constant exported from the thread_meta package,
- pinned-first ordering in ``search()`` (both SQL and memory backends),
- ``update_metadata(touch=False)`` so pin flips don't bump ``updated_at``.
"""

import pytest
from _db_helpers import sqlite_repo_fixture
from langgraph.store.memory import InMemoryStore

from deerflow.persistence.thread_meta import (
    THREAD_PINNED_METADATA_KEY,
    MemoryThreadMetaStore,
    ThreadMetaRepository,
)


def test_pinned_metadata_key_value() -> None:
    # Keep in sync with frontend/src/core/threads/utils.ts.
    assert THREAD_PINNED_METADATA_KEY == "deerflow_pinned"


# Per-test SQLite engine + ThreadMetaRepository; engine closed in ``finally``
# even when an assertion fails (see tests/_db_helpers.py).
repo = sqlite_repo_fixture(ThreadMetaRepository)


class TestSqlPinnedOrdering:
    @pytest.mark.anyio
    async def test_search_orders_pinned_threads_first(self, repo):
        # t-old is updated first, t-new later; pinning t-old must lift it
        # above t-new in search results.
        await repo.create("t-old")
        await repo.create("t-new")
        await repo.update_metadata("t-old", {THREAD_PINNED_METADATA_KEY: True}, touch=False)

        results = await repo.search()

        assert [r["thread_id"] for r in results] == ["t-old", "t-new"]

    @pytest.mark.anyio
    async def test_search_unpinned_keeps_recency_order(self, repo):
        await repo.create("t-old")
        await repo.create("t-new")

        results = await repo.search()

        assert [r["thread_id"] for r in results] == ["t-new", "t-old"]

    @pytest.mark.anyio
    async def test_unpin_drops_thread_back_to_recency_order(self, repo):
        await repo.create("t-old")
        await repo.create("t-new")
        await repo.update_metadata("t-old", {THREAD_PINNED_METADATA_KEY: True}, touch=False)
        await repo.update_metadata("t-old", {THREAD_PINNED_METADATA_KEY: False}, touch=False)

        results = await repo.search()

        assert [r["thread_id"] for r in results] == ["t-new", "t-old"]

    @pytest.mark.anyio
    async def test_update_metadata_touch_false_preserves_updated_at(self, repo):
        created = await repo.create("t1")

        await repo.update_metadata("t1", {THREAD_PINNED_METADATA_KEY: True}, touch=False)
        untouched = await repo.get("t1")
        assert untouched["updated_at"] == created["updated_at"]
        assert untouched["metadata"][THREAD_PINNED_METADATA_KEY] is True

        await repo.update_metadata("t1", {"other": 1})
        touched = await repo.get("t1")
        assert touched["updated_at"] != created["updated_at"]


class TestMemoryStorePinnedOrdering:
    @pytest.fixture
    def store(self):
        return MemoryThreadMetaStore(InMemoryStore())

    @pytest.mark.anyio
    async def test_search_orders_pinned_threads_first(self, store):
        await store.create("t-old")
        await store.create("t-new")
        await store.update_metadata("t-old", {THREAD_PINNED_METADATA_KEY: True}, touch=False)

        results = await store.search()

        assert [r["thread_id"] for r in results] == ["t-old", "t-new"]

    @pytest.mark.anyio
    async def test_search_unpinned_keeps_recency_order(self, store):
        await store.create("t-old")
        await store.create("t-new")

        results = await store.search()

        assert [r["thread_id"] for r in results] == ["t-new", "t-old"]

    @pytest.mark.anyio
    async def test_search_applies_limit_offset_after_pinned_sort(self, store):
        await store.create("t-1")
        await store.create("t-2")
        await store.create("t-3")
        await store.update_metadata("t-1", {THREAD_PINNED_METADATA_KEY: True}, touch=False)

        page = await store.search(limit=2, offset=0)

        assert [r["thread_id"] for r in page] == ["t-1", "t-3"]

    @pytest.mark.anyio
    async def test_update_metadata_touch_false_preserves_updated_at(self, store):
        created = await store.create("t1")

        await store.update_metadata("t1", {THREAD_PINNED_METADATA_KEY: True}, touch=False)
        untouched = await store.get("t1")
        assert untouched["updated_at"] == created["updated_at"]

        await store.update_metadata("t1", {"other": 1})
        touched = await store.get("t1")
        assert touched["updated_at"] != created["updated_at"]

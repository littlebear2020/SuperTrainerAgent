"""In-memory ThreadMetaStore backed by LangGraph BaseStore.

Used when database.backend=memory. Delegates to the LangGraph Store's
``("threads",)`` namespace — the same namespace used by the Gateway
router for thread records.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.store.base import BaseStore

from deerflow.persistence.thread_meta.base import THREAD_PINNED_METADATA_KEY, ThreadMetaStore
from deerflow.runtime.keyed_lock import AsyncKeyedLockTable
from deerflow.runtime.user_context import AUTO, _AutoSentinel, resolve_user_id

THREADS_NS: tuple[str, ...] = ("threads",)
SEARCH_PAGE_SIZE = 500


class MemoryThreadMetaStore(ThreadMetaStore):
    def __init__(self, store: BaseStore) -> None:
        self._store = store
        # Per-thread async serialization for atomic check-then-write paths
        # (claim_unowned). Loop-aware so one store can serve event loops on
        # different threads without cross-loop asyncio.Lock deadlocks.
        self._thread_locks = AsyncKeyedLockTable[str]()

    async def _get_owned_record(
        self,
        thread_id: str,
        user_id: str | None | _AutoSentinel,
        method_name: str,
    ) -> dict | None:
        """Fetch a record and verify ownership. Returns a mutable copy, or None."""
        resolved = resolve_user_id(user_id, method_name=method_name)
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return None
        record = dict(item.value)
        if resolved is not None and record.get("user_id") != resolved:
            return None
        return record

    async def create(
        self,
        thread_id: str,
        *,
        assistant_id: str | None = None,
        user_id: str | None | _AutoSentinel = AUTO,
        display_name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.create")
        now = time.time()
        record: dict[str, Any] = {
            "thread_id": thread_id,
            "assistant_id": assistant_id,
            "user_id": resolved_user_id,
            "display_name": display_name,
            "status": "idle",
            "metadata": metadata or {},
            "values": {},
            "created_at": now,
            "updated_at": now,
        }
        await self._store.aput(THREADS_NS, thread_id, record)
        return record

    async def get(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> dict | None:
        return await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.get")

    async def search(
        self,
        *,
        metadata: dict | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
        user_id: str | None | _AutoSentinel = AUTO,
    ) -> list[dict]:
        """Search threads by materializing matches, then sorting in Python.

        The memory backend loads all matching rows in chunks before slicing so
        it can mirror SQL's pinned-first ordering. Use the SQL store for
        scalable paginated I/O.
        """
        resolved_user_id = resolve_user_id(user_id, method_name="MemoryThreadMetaStore.search")
        filter_dict: dict[str, Any] = {}
        if metadata:
            filter_dict.update(metadata)
        if status:
            filter_dict["status"] = status
        if resolved_user_id is not None:
            filter_dict["user_id"] = resolved_user_id

        items = []
        search_offset = 0
        while True:
            page = await self._store.asearch(
                THREADS_NS,
                filter=filter_dict or None,
                limit=SEARCH_PAGE_SIZE,
                offset=search_offset,
            )
            if not page:
                break
            items.extend(page)
            if len(page) < SEARCH_PAGE_SIZE:
                break
            search_offset += len(page)

        records = [self._item_to_dict(item) for item in items]
        records.sort(key=self._sort_key, reverse=True)
        return records[offset : offset + limit]

    async def check_access(self, thread_id: str, user_id: str, *, require_existing: bool = False) -> bool:
        item = await self._store.aget(THREADS_NS, thread_id)
        if item is None:
            return not require_existing
        record_user_id = item.value.get("user_id")
        if record_user_id is None:
            return True
        return record_user_id == user_id

    async def claim_unowned(self, thread_id: str, owner: str) -> bool:
        async with self._thread_locks.hold(thread_id):
            item = await self._store.aget(THREADS_NS, thread_id)
            if item is None or item.value.get("user_id") is not None:
                return False
            record = dict(item.value)
            record["user_id"] = owner
            await self._store.aput(THREADS_NS, thread_id, record)
            return True

    async def update_display_name(self, thread_id: str, display_name: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_display_name")
        if record is None:
            return
        record["display_name"] = display_name
        record["updated_at"] = time.time()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def update_status(self, thread_id: str, status: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_status")
        if record is None:
            return
        record["status"] = status
        record["updated_at"] = time.time()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def update_metadata(self, thread_id: str, metadata: dict, *, touch: bool = True, user_id: str | None | _AutoSentinel = AUTO) -> None:
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.update_metadata")
        if record is None:
            return
        merged = dict(record.get("metadata") or {})
        merged.update(metadata)
        record["metadata"] = merged
        if touch:
            record["updated_at"] = time.time()
        await self._store.aput(THREADS_NS, thread_id, record)

    async def delete(self, thread_id: str, *, user_id: str | None | _AutoSentinel = AUTO) -> None:
        record = await self._get_owned_record(thread_id, user_id, "MemoryThreadMetaStore.delete")
        if record is None:
            return
        await self._store.adelete(THREADS_NS, thread_id)

    @staticmethod
    def _item_to_dict(item) -> dict[str, Any]:
        """Convert a Store SearchItem to the dict format expected by callers."""
        val = item.value
        return {
            "thread_id": item.key,
            "assistant_id": val.get("assistant_id"),
            "user_id": val.get("user_id"),
            "display_name": val.get("display_name"),
            "status": val.get("status", "idle"),
            "metadata": val.get("metadata", {}),
            "created_at": str(val.get("created_at", "")),
            "updated_at": str(val.get("updated_at", "")),
        }

    @staticmethod
    def _sort_key(record: dict[str, Any]) -> tuple[bool, str, str]:
        """Pinned-first, then recency, then thread_id (all descending).

        ``updated_at`` is compared as a string: this store writes unix-epoch
        floats of identical width for all current timestamps, so lexicographic
        and chronological order coincide.
        """
        metadata = record.get("metadata")
        pinned = isinstance(metadata, dict) and metadata.get(THREAD_PINNED_METADATA_KEY) is True
        return (pinned, str(record.get("updated_at") or ""), str(record.get("thread_id") or ""))

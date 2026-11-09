"""SQLAlchemy-backed RunEventStore implementation.

Persists events to the ``run_events`` table. Trace content is truncated
at ``max_trace_content`` bytes to avoid bloating the database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.runtime.events.store.base import RunEventStore
from deerflow.runtime.user_context import AUTO, _AutoSentinel, get_current_user, resolve_user_id

logger = logging.getLogger(__name__)


class DbRunEventStore(RunEventStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *, max_trace_content: int = 10240):
        self._sf = session_factory
        self._max_trace_content = max_trace_content
        # A3（2026-10-07 反馈核实批）：per-thread asyncio 锁串行化同进程并发写者的
        # seq 分配（SELECT max(seq) → INSERT → COMMIT 整段进锁）——两个协程在
        # max(seq) 读后交错会撞 UNIQUE(thread_id, seq)，失败的 put_batch 整批
        # 静默丢失。DB 级 FOR UPDATE / 约束守跨进程竞态；这里守单进程常态。
        #
        # 取舍（D8，2026-10-07）：上游另有 PostgreSQL advisory fence
        # （pg_advisory_xact_lock）守跨进程写/删互斥——本地生产即 SQLite，fence
        # 对 SQLite 是 no-op，故不移植；跨进程防线维持 DB 级约束。
        #
        # 弱引用注册表在仍有人持有/等待某代锁时保住该代；pin 使活跃 thread 保持
        # 一锁一代，直到 delete_by_thread 显式退役；退役后仅存量持有者维持旧代
        # 存活至排空。
        self._write_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._write_lock_pins: dict[str, asyncio.Lock] = {}

    def _get_write_lock(self, thread_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-thread seq-assignment lock."""
        lock = self._write_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[thread_id] = lock
        # 删除后再来的新调用者使 thread 重新活跃——重新 pin 当代锁，保证活跃
        # thread 的注册表生命周期稳定。
        self._write_lock_pins[thread_id] = lock
        return lock

    @staticmethod
    def _row_to_dict(row: RunEventRow) -> dict:
        d = row.to_dict()
        d["metadata"] = d.pop("event_metadata", {})
        val = d.get("created_at")
        if isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=UTC)
            d["created_at"] = val.isoformat()
        d.pop("id", None)
        # Restore JSON-serialized content (dict OR list) written with the
        # content_is_json flag, plus legacy rows that used content_is_dict.
        raw = d.get("content", "")
        meta = d.get("metadata", {})
        if isinstance(raw, str) and (meta.get("content_is_json") or meta.get("content_is_dict")):
            try:
                d["content"] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # Content looked like JSON (flag set) but failed to parse;
                # keep the raw string as-is.
                logger.debug("Failed to deserialize content as JSON for event seq=%s", d.get("seq"))
        return d

    def _truncate_trace(self, category: str, content: str | dict | list, metadata: dict | None) -> tuple[str | dict | list, dict]:
        if category == "trace":
            text = json.dumps(content, default=str, ensure_ascii=False) if isinstance(content, (dict, list)) else content
            encoded = text.encode("utf-8")
            if len(encoded) > self._max_trace_content:
                # Truncate by bytes, then decode back (may cut a multi-byte char, so use errors="ignore")
                content = encoded[: self._max_trace_content].decode("utf-8", errors="ignore")
                metadata = {**(metadata or {}), "content_truncated": True, "original_byte_length": len(encoded)}
        return content, metadata or {}

    @staticmethod
    def _user_id_from_context() -> str | None:
        """Soft read of user_id from contextvar for write paths.

        Returns ``None`` (no filter / no stamp) if contextvar is unset,
        which is the expected case for background worker writes. HTTP
        request writes will have the contextvar set by auth middleware
        and get their user_id stamped automatically.

        Coerces ``user.id`` to ``str`` at the boundary: ``User.id`` is
        typed as ``UUID`` by the auth layer, but ``run_events.user_id``
        is ``VARCHAR(64)`` and aiosqlite cannot bind a raw UUID object
        to a VARCHAR column ("type 'UUID' is not supported") — the
        INSERT would silently roll back and the worker would hang.
        """
        user = get_current_user()
        return str(user.id) if user is not None else None

    async def put(self, *, thread_id, run_id, event_type, category, content="", metadata=None, created_at=None):  # noqa: D401
        """Write a single event — low-frequency path only.

        This opens a dedicated transaction with a FOR UPDATE lock to
        assign a monotonic *seq*.  For high-throughput writes use
        :meth:`put_batch`, which acquires the lock once for the whole
        batch.  Currently the only caller is ``worker.run_agent`` for
        the initial ``human_message`` event (once per run).
        """
        content, metadata = self._truncate_trace(category, content, metadata)
        if isinstance(content, (dict, list)):
            db_content = json.dumps(content, default=str, ensure_ascii=False)
            metadata = {**(metadata or {}), "content_is_json": True}
        else:
            db_content = content
        user_id = self._user_id_from_context()
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                async with session.begin():
                    # Use FOR UPDATE to serialize seq assignment within a thread.
                    # NOTE: with_for_update() on aggregates is a no-op on SQLite;
                    # the in-process per-thread asyncio lock serializes same-thread
                    # writers; the UNIQUE(thread_id, seq) constraint catches races.
                    max_seq = await session.scalar(select(func.max(RunEventRow.seq)).where(RunEventRow.thread_id == thread_id).with_for_update())
                    seq = (max_seq or 0) + 1
                    row = RunEventRow(
                        thread_id=thread_id,
                        run_id=run_id,
                        user_id=user_id,
                        event_type=event_type,
                        category=category,
                        content=db_content,
                        event_metadata=metadata,
                        seq=seq,
                        created_at=datetime.fromisoformat(created_at) if created_at else datetime.now(UTC),
                    )
                    session.add(row)
                return self._row_to_dict(row)

    async def put_batch(self, events):
        if not events:
            return []
        thread_ids = {e["thread_id"] for e in events}
        if len(thread_ids) > 1:
            raise ValueError(f"put_batch requires all events to belong to the same thread; got {thread_ids!r}")
        user_id = self._user_id_from_context()
        # All events belong to the same thread (validated above).
        thread_id = events[0]["thread_id"]
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                async with session.begin():
                    # Get max seq for the thread.
                    # NOTE: with_for_update() on aggregates is a no-op on SQLite;
                    # the in-process per-thread asyncio lock serializes same-thread
                    # writers; the UNIQUE(thread_id, seq) constraint catches races.
                    max_seq = await session.scalar(select(func.max(RunEventRow.seq)).where(RunEventRow.thread_id == thread_id).with_for_update())
                    seq = max_seq or 0
                    rows = []
                    for e in events:
                        seq += 1
                        content = e.get("content", "")
                        category = e.get("category", "trace")
                        metadata = e.get("metadata")
                        content, metadata = self._truncate_trace(category, content, metadata)
                        if isinstance(content, (dict, list)):
                            db_content = json.dumps(content, default=str, ensure_ascii=False)
                            metadata = {**(metadata or {}), "content_is_json": True}
                        else:
                            db_content = content
                        row = RunEventRow(
                            thread_id=e["thread_id"],
                            run_id=e["run_id"],
                            user_id=e.get("user_id", user_id),
                            event_type=e["event_type"],
                            category=category,
                            content=db_content,
                            event_metadata=metadata,
                            seq=seq,
                            created_at=datetime.fromisoformat(e["created_at"]) if e.get("created_at") else datetime.now(UTC),
                        )
                        session.add(row)
                        rows.append(row)
                return [self._row_to_dict(r) for r in rows]

    async def list_messages(
        self,
        thread_id,
        *,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.list_messages")
        stmt = select(RunEventRow).where(RunEventRow.thread_id == thread_id, RunEventRow.category == "message")
        if resolved_user_id is not None:
            stmt = stmt.where(RunEventRow.user_id == resolved_user_id)
        if before_seq is not None:
            stmt = stmt.where(RunEventRow.seq < before_seq)
        if after_seq is not None:
            stmt = stmt.where(RunEventRow.seq > after_seq)

        if after_seq is not None:
            # Forward pagination: first `limit` records after cursor
            stmt = stmt.order_by(RunEventRow.seq.asc()).limit(limit)
            async with self._sf() as session:
                result = await session.execute(stmt)
                return [self._row_to_dict(r) for r in result.scalars()]
        else:
            # before_seq or default (latest): take last `limit` records, return ascending
            stmt = stmt.order_by(RunEventRow.seq.desc()).limit(limit)
            async with self._sf() as session:
                result = await session.execute(stmt)
                rows = list(result.scalars())
                return [self._row_to_dict(r) for r in reversed(rows)]

    async def list_events(
        self,
        thread_id,
        run_id,
        *,
        event_types=None,
        limit=500,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.list_events")
        stmt = select(RunEventRow).where(RunEventRow.thread_id == thread_id, RunEventRow.run_id == run_id)
        if resolved_user_id is not None:
            stmt = stmt.where(RunEventRow.user_id == resolved_user_id)
        if event_types:
            stmt = stmt.where(RunEventRow.event_type.in_(event_types))
        stmt = stmt.order_by(RunEventRow.seq.asc()).limit(limit)
        async with self._sf() as session:
            result = await session.execute(stmt)
            return [self._row_to_dict(r) for r in result.scalars()]

    async def list_messages_by_run(
        self,
        thread_id,
        run_id,
        *,
        limit=50,
        before_seq=None,
        after_seq=None,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.list_messages_by_run")
        stmt = select(RunEventRow).where(
            RunEventRow.thread_id == thread_id,
            RunEventRow.run_id == run_id,
            RunEventRow.category == "message",
        )
        if resolved_user_id is not None:
            stmt = stmt.where(RunEventRow.user_id == resolved_user_id)
        if before_seq is not None:
            stmt = stmt.where(RunEventRow.seq < before_seq)
        if after_seq is not None:
            stmt = stmt.where(RunEventRow.seq > after_seq)

        if after_seq is not None:
            stmt = stmt.order_by(RunEventRow.seq.asc()).limit(limit)
            async with self._sf() as session:
                result = await session.execute(stmt)
                return [self._row_to_dict(r) for r in result.scalars()]
        else:
            stmt = stmt.order_by(RunEventRow.seq.desc()).limit(limit)
            async with self._sf() as session:
                result = await session.execute(stmt)
                rows = list(result.scalars())
                return [self._row_to_dict(r) for r in reversed(rows)]

    async def count_messages(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.count_messages")
        stmt = select(func.count()).select_from(RunEventRow).where(RunEventRow.thread_id == thread_id, RunEventRow.category == "message")
        if resolved_user_id is not None:
            stmt = stmt.where(RunEventRow.user_id == resolved_user_id)
        async with self._sf() as session:
            return await session.scalar(stmt) or 0

    async def delete_by_thread(
        self,
        thread_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.delete_by_thread")
        # 删除与写者共用同一临界段（per-thread 锁）：计数与删除之间不再被并发
        # put 落行"复活"已删 thread。上游此处另持 PG advisory fence，本地不移植
        # （D8 取舍，见 __init__ 注释）。
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                count_conditions = [RunEventRow.thread_id == thread_id]
                if resolved_user_id is not None:
                    count_conditions.append(RunEventRow.user_id == resolved_user_id)
                count_stmt = select(func.count()).select_from(RunEventRow).where(*count_conditions)
                count = await session.scalar(count_stmt) or 0
                if count > 0:
                    await session.execute(delete(RunEventRow).where(*count_conditions))
                    await session.commit()
            # 退役活跃 thread 的锁 pin，但不直接移除弱引用注册表项：
            # asyncio.Lock.release() 在排队等待者恢复前就清 locked()，无锁检查
            # 可能撞上交接窗口把一个 thread 劈成两代锁。存量持有/等待者维持旧代
            # 存活至排空；后来的调用者由此解析到同一把锁而非与之赛跑的新锁。
            self._write_lock_pins.pop(thread_id, None)
        return count

    async def delete_by_run(
        self,
        thread_id,
        run_id,
        *,
        user_id: str | None | _AutoSentinel = AUTO,
    ):
        resolved_user_id = resolve_user_id(user_id, method_name="DbRunEventStore.delete_by_run")
        # 与 delete_by_thread 同临界段；删单 run 不杀 thread，锁 pin 刻意保留。
        async with self._get_write_lock(thread_id):
            async with self._sf() as session:
                count_conditions = [RunEventRow.thread_id == thread_id, RunEventRow.run_id == run_id]
                if resolved_user_id is not None:
                    count_conditions.append(RunEventRow.user_id == resolved_user_id)
                count_stmt = select(func.count()).select_from(RunEventRow).where(*count_conditions)
                count = await session.scalar(count_stmt) or 0
                if count > 0:
                    await session.execute(delete(RunEventRow).where(*count_conditions))
                    await session.commit()
                return count

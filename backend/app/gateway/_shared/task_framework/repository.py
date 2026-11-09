"""TaskRepository — unified CRUD for the unified_tasks table."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import TaskRow

# P0-2: dirty_frames 是跨 session 的 read-modify-write，SQLite 无行锁
# （`with_for_update()` 在 sqlite 方言下被静默丢弃，实测编译输出无 FOR UPDATE），
# 故用进程内 keyed Lock 串行化。同 video_gen/service.py 的 _synth_lock_for 约定：
# 惰性创建、不清理（单进程 gateway 长驻，Lock 对象开销可忽略）。
_dirty_locks: dict[str, asyncio.Lock] = {}


def _dirty_lock_for(task_id: str) -> asyncio.Lock:
    lock = _dirty_locks.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _dirty_locks[task_id] = lock
    return lock


class TaskRepository:
    """All domain Services share this one repository."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(
        self,
        *,
        id: str,
        user_id: str,
        domain: str,
        task_type: str,
        payload: str,
        priority: int = 50,
        title: str | None = None,
        thumbnail_url: str | None = None,
    ) -> TaskRow:
        task = TaskRow(
            id=id,
            user_id=user_id,
            domain=domain,
            task_type=task_type,
            payload=payload,
            priority=priority,
            title=title,
            thumbnail_url=thumbnail_url,
        )
        async with self._sf() as session:
            session.add(task)
            await session.commit()
            await session.refresh(task)
        return task

    async def get(self, task_id: str, user_id: str | None = None) -> TaskRow | None:
        async with self._sf() as session:
            stmt = select(TaskRow).where(TaskRow.id == task_id)
            if user_id is not None:
                stmt = stmt.where(TaskRow.user_id == user_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: str,
        domain: str | None = None,
        status: str | None = None,
        limit: int = 100,
        task_type: str | None = None,
    ) -> list[TaskRow]:
        async with self._sf() as session:
            stmt = select(TaskRow).where(TaskRow.user_id == user_id)
            if domain is not None:
                stmt = stmt.where(TaskRow.domain == domain)
            if status is not None:
                stmt = stmt.where(TaskRow.status == status)
            if task_type is not None:
                stmt = stmt.where(TaskRow.task_type == task_type)
            stmt = stmt.order_by(TaskRow.created_at.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def update(self, task_id: str, **kwargs: Any) -> None:
        async with self._sf() as session:
            stmt = update(TaskRow).where(TaskRow.id == task_id).values(**kwargs, updated_at=datetime.now(UTC))
            await session.execute(stmt)
            await session.commit()

    async def touch(self, task_id: str) -> None:
        """W1（2026-08-24 看门狗活性重构）：活性 ping 落库--仅 UPDATE updated_at。

        与 :meth:`update` 的区别：不携带业务列，payload/result/status 一概不动--
        活性信号不得篡改任务状态。行不存在时静默无操作（reaper/任务删除竞态下
        ping 落在已删任务上是合法时序）。列表按 created_at 排序（:meth:`list_by_user`），
        ping 不扰序。行缺失/DB 抖动的重试语义由 service 层 ``liveness_ping`` 的
        节流戳回落负责。
        """
        async with self._sf() as session:
            stmt = update(TaskRow).where(TaskRow.id == task_id).values(updated_at=datetime.now(UTC))
            await session.execute(stmt)
            await session.commit()

    async def update_if_status(self, task_id: str, expect_status: str, **kwargs: Any) -> bool:
        """W6：CAS 守卫的条件 UPDATE--仅当 status 仍为 ``expect_status`` 时落 ``kwargs``。

        收尸重排用（N2③）：防「读 -> 写」窗口内任务状态被并发方（取消处理器/
        用户操作/worker）改写后仍被覆盖。rowcount=0 即 CAS 失败，调用方放弃本次
        重排。``updated_at`` 同步前推。
        """
        async with self._sf() as session:
            stmt = update(TaskRow).where(TaskRow.id == task_id).where(TaskRow.status == expect_status).values(**kwargs, updated_at=datetime.now(UTC))
            res = await session.execute(stmt)
            await session.commit()
            return bool(res.rowcount)

    async def update_if_owned(self, task_id: str, owner: str | None, **kwargs: Any) -> bool:
        """W3b：按 claimed_by 的 CAS 守卫条件 UPDATE（对齐 worker ``db_update_if_owned``）。

        ``owner=None`` 语义为「claimed_by IS NULL」（NULL 不能用 ``==`` 匹配）。
        收尸 composing 行的重排用：读到的 claimed_by 在写入时仍未变才生效--
        防「读租约过期 -> 写重排」窗口内 worker 复活双跑（TOCTOU，N2③）。
        """
        async with self._sf() as session:
            stmt = update(TaskRow).where(TaskRow.id == task_id)
            if owner is None:
                stmt = stmt.where(TaskRow.claimed_by.is_(None))
            else:
                stmt = stmt.where(TaskRow.claimed_by == owner)
            stmt = stmt.values(**kwargs, updated_at=datetime.now(UTC))
            res = await session.execute(stmt)
            await session.commit()
            return bool(res.rowcount)

    async def list_composing(self, domain: str) -> list[TaskRow]:
        """W3b：列出本域全部 composing 行（gateway 兜底谓词的扫描对象）。

        不过滤租约/updated_at--双条件判定在 service 层做（NULL 租约不收、
        租约存活跳过），保证 warning 等可观测路径也能看到全量行。
        """
        async with self._sf() as session:
            stmt = select(TaskRow).where(TaskRow.domain == domain).where(TaskRow.status == "composing")
            return list((await session.execute(stmt)).scalars().all())

    async def list_audio_regen_running(self, domain: str) -> list[TaskRow]:
        """B4（2026-09-28 edits 修复批）：payload 含 audio_regen_running 键的任务。

        regenerate-audio 后台批量随进程重启即死——启动清理（recover_audio_regen_flags）
        的扫描对象。payload 是 JSON 文本列，LIKE 粗筛 + service 层 json 复核。
        """
        async with self._sf() as session:
            # review 收尾（2026-09-28）：LIKE 收紧到 ': true'——DB 层排除值为 false/其他
            # 的脏数据。写侧唯一形态 = json.dumps(..., ensure_ascii=False) 默认分隔符
            # （'"audio_regen_running": true'）；service 层 json 复核仍保留（双保险）。
            stmt = select(TaskRow).where(TaskRow.domain == domain).where(TaskRow.payload.like('%"audio_regen_running": true%'))
            return list((await session.execute(stmt)).scalars().all())

    async def list_qc_repair(self, domain: str) -> list[TaskRow]:
        """D19（2026-09-08 remotion-course T12）：列出本域 qc_repair 行。

        qc_repair = worker QC 发现异常后的"修复中"状态（不标 ready）；gateway
        watcher 扫描本表驱动重生/降级状态机（见 VideoGenService._process_qc_repair_tasks）。
        """
        async with self._sf() as session:
            stmt = select(TaskRow).where(TaskRow.domain == domain).where(TaskRow.status == "qc_repair")
            return list((await session.execute(stmt)).scalars().all())

    async def add_dirty_frame(self, task_id: str, frame_index: int) -> None:
        """B2: append a frame index to dirty_frames (deduplicated, sorted)。

        P0-2: 全程持锁--读与写必须原子，否则并发 regen 互相覆盖丢标记。
        """
        import json

        async with _dirty_lock_for(task_id):
            async with self._sf() as session:
                stmt = select(TaskRow.dirty_frames).where(TaskRow.id == task_id)
                result = (await session.execute(stmt)).scalar_one_or_none()
                dirty: list[int] = json.loads(result or "[]")
                if frame_index not in dirty:
                    dirty.append(frame_index)
                    dirty.sort()
                await session.execute(
                    update(TaskRow)
                    .where(TaskRow.id == task_id)
                    .values(
                        dirty_frames=json.dumps(dirty),
                        updated_at=datetime.now(UTC),
                    ),
                )
                await session.commit()

    async def clear_dirty_frames(self, task_id: str) -> None:
        """锁内清空 dirty_frames（2026-08-08 P1-3）。

        修前路由层用裸 ``repo.update(task_id, dirty_frames="[]")`` 清空，不持
        ``_dirty_lock_for`` 锁——与并发的 add_dirty_frame 互相覆盖（add 读到旧值
        → 清空落库 → add 写回含新帧的旧列表，或反向吞掉刚加的帧），增量
        recompose 漏帧。清空必须与 add 走同一把锁。
        """
        async with _dirty_lock_for(task_id):
            async with self._sf() as session:
                await session.execute(
                    update(TaskRow).where(TaskRow.id == task_id).values(dirty_frames="[]", updated_at=datetime.now(UTC)),
                )
                await session.commit()

    async def delete(self, task_id: str) -> None:
        async with self._sf() as session:
            task = await session.get(TaskRow, task_id)
            if task is not None:
                await session.delete(task)
                await session.commit()

    async def list_stale_for_recovery(
        self,
        domain: str,
        claimable_by_gateway: tuple[str, ...],
        stale_after: datetime | None = None,
    ) -> list[TaskRow]:
        """List non-terminal tasks in this domain that the Gateway should recover.

        composing status is excluded (that's Worker territory).

        ``stale_after`` (optional): when set, only return tasks whose
        ``updated_at`` is older than this cutoff — i.e. tasks that haven't made
        progress in a while. Used by the periodic stall reaper
        (BaseTaskService.reap_stalled_tasks) to flip truly-stuck in-process tasks
        to failed, WITHOUT touching the startup-recovery path (which omits this
        arg and recovers every non-terminal task regardless of age).
        """
        async with self._sf() as session:
            stmt = select(TaskRow).where(TaskRow.domain == domain).where(TaskRow.status.in_(claimable_by_gateway))
            if stale_after is not None:
                stmt = stmt.where(TaskRow.updated_at < stale_after)
            return list((await session.execute(stmt)).scalars().all())

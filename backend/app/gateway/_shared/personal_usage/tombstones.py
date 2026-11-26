"""删除快照：作品/会话删除前写入一行，让「我的用量」继续计入已发生的费用（BUG-01）。

账本（``runs`` 的 ``llm_bookkeeping`` 行与 ``external_api_calls``）按 subject id 归组，
本身分不出类型——视频任务的 LLM 账本只有 ``domain=video_gen``，UI run 的
``assistant_id`` 恒为 ``lead_agent``。所以删除前把「类型、标题、状态」留成快照，
聚合时只补这三样；费用与 Token 仍从账本现算。

写入失败一律原样抛出：删除路径在调用点之前不得删任何东西，快照写不进去，这次删除就失败。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.gateway._shared.personal_usage.types import normalize_usage_status, resolve_agent_capability
from app.gateway._shared.task_framework.models import TaskRow
from deerflow.persistence.base import Base
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

USAGE_TASK_TYPES = frozenset({"ai_image", "ai_video"})
_TITLE_MAX_CHARS = 256


class UsageTombstoneRow(Base):
    __tablename__ = "usage_tombstones"

    # kind: "video"（unified_tasks 里的 ai_image/ai_video）| "agent"（Agent 会话线程）
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))


def _frozen_status(raw_status: str | None) -> str:
    """快照写入后不再更新（任务删除会取消执行），``running`` 若原样保留会永远显示「进行中」。"""
    status = normalize_usage_status(raw_status)
    return "incomplete" if status == "running" else status


async def record_task_tombstone(task: TaskRow) -> bool:
    if task.task_type not in USAGE_TASK_TYPES:
        return False
    session_factory = get_session_factory()
    if session_factory is None:
        return False
    async with session_factory() as session:
        await session.merge(
            UsageTombstoneRow(
                kind="video",
                subject_id=task.id,
                user_id=task.user_id,
                capability=task.task_type,
                title=task.title[:_TITLE_MAX_CHARS] if task.title else None,
                status=_frozen_status(task.status),
                subject_created_at=task.created_at,
                deleted_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return True


async def record_thread_tombstone(thread_id: str) -> bool:
    session_factory = get_session_factory()
    if session_factory is None:
        return False
    async with session_factory() as session:
        thread = await session.get(ThreadMetaRow, thread_id)
        # 行不存在（已删，重复 DELETE）或无主线程：不触碰已有快照。无主线程本来就不进任何人的用量，
        # 而快照表 user_id 非空，硬写只会让这次删除失败。
        if thread is None or not thread.user_id:
            return False
        runs = list(
            (
                await session.execute(
                    select(RunRow)
                    .where(
                        RunRow.thread_id == thread_id,
                        RunRow.operation_kind == "run",
                        (RunRow.user_id == thread.user_id) | RunRow.user_id.is_(None),
                    )
                    .order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
                )
            ).scalars()
        )
        capability = resolve_agent_capability(thread, runs)
        if capability is None:
            return False
        latest_run = runs[0] if runs else None
        display_name = thread.display_name if thread.display_name and thread.display_name != thread.thread_id else None
        title = display_name or (latest_run.first_human_message if latest_run else None)
        await session.merge(
            UsageTombstoneRow(
                kind="agent",
                subject_id=thread_id,
                user_id=thread.user_id,
                capability=capability,
                title=title[:_TITLE_MAX_CHARS] if title else None,
                status=_frozen_status(latest_run.status if latest_run else thread.status),
                subject_created_at=thread.created_at,
                deleted_at=datetime.now(UTC),
            )
        )
        await session.commit()
    return True


async def load_tombstones(
    session,
    *,
    user_id: str,
    kind: Literal["video", "agent"],
    capabilities: Collection[str] | None = None,
) -> list[UsageTombstoneRow]:
    stmt = select(UsageTombstoneRow).where(UsageTombstoneRow.user_id == user_id, UsageTombstoneRow.kind == kind)
    if capabilities is not None:
        stmt = stmt.where(UsageTombstoneRow.capability.in_(list(capabilities)))
    return list((await session.execute(stmt)).scalars())

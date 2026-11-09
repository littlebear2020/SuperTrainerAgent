"""Async repository for VoiceTwinRow CRUD operations."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.voice_twin.models import VoiceTwinRow


class VoiceTwinRepository:
    """Database operations for voice twin records."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, row: VoiceTwinRow) -> VoiceTwinRow:
        async with self._sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_by_id(self, voice_id: str) -> VoiceTwinRow | None:
        async with self._sf() as session:
            stmt = select(VoiceTwinRow).where(VoiceTwinRow.id == voice_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_all(self, user_id: str | None = None) -> list[VoiceTwinRow]:
        async with self._sf() as session:
            if user_id:
                stmt = select(VoiceTwinRow).where(VoiceTwinRow.user_id == user_id).order_by(VoiceTwinRow.created_at.desc())
            else:
                stmt = select(VoiceTwinRow).order_by(VoiceTwinRow.created_at.desc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_by_status(self, statuses: list[str]) -> list[VoiceTwinRow]:
        """按状态集合查询（训练巡检 reaper 扫 training/uploading 行用）。"""
        async with self._sf() as session:
            stmt = select(VoiceTwinRow).where(VoiceTwinRow.status.in_(statuses)).order_by(VoiceTwinRow.created_at.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update_status(self, voice_id: str, *, status: str, error_code: int | None = None, error_message: str | None = None, demo_audio_path: str | None = None) -> VoiceTwinRow | None:
        async with self._sf() as session:
            stmt = select(VoiceTwinRow).where(VoiceTwinRow.id == voice_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.status = status
            if error_code is not None:
                row.error_code = error_code
            if error_message is not None:
                row.error_message = error_message
            if demo_audio_path is not None:
                row.demo_audio_path = demo_audio_path
            await session.commit()
            await session.refresh(row)
            return row

    async def update_source_metadata(self, voice_id: str, *, source_audio_path: str | None = None, source_audio_format: str | None = None, source_audio_duration: float | None = None) -> None:
        async with self._sf() as session:
            stmt = select(VoiceTwinRow).where(VoiceTwinRow.id == voice_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return
            if source_audio_path is not None:
                row.source_audio_path = source_audio_path
            if source_audio_format is not None:
                row.source_audio_format = source_audio_format
            if source_audio_duration is not None:
                row.source_audio_duration = source_audio_duration
            await session.commit()

    async def delete(self, voice_id: str) -> bool:
        async with self._sf() as session:
            stmt = select(VoiceTwinRow).where(VoiceTwinRow.id == voice_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

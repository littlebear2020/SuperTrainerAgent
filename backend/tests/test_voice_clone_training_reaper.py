"""D1 训练巡检 reaper + 超时可配 + poll_status 异常分类 回归测试。

背景：训练状态收敛此前完全依赖前端轮询 poll_status；用户关页后无人轮询，
超时检查永不触发，行永卡 training。reaper 每 N 分钟扫一轮超时行：
先真实远端 poll 一次，ready → 更新状态；非 ready / poll 异常 → 标 failed。

fixture 风格照 test_voice_twin.py：async fixture 用 asyncio.new_event_loop()
做 setup/teardown（避免跨文件 get_event_loop 污染），不复用 pytest-asyncio
session loop。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.voice_twin.config import VoiceCloneConfig
from app.gateway.voice_twin.models import VoiceTwinRow
from app.gateway.voice_twin.repository import VoiceTwinRepository
from app.gateway.voice_twin.service import VoiceCloneService


@pytest.fixture
def async_voice_repo():
    """内存 sqlite 异步 repo（同 test_voice_twin.py 的既有模式）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(VoiceTwinRow.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        return VoiceTwinRepository(sf)

    repo = asyncio.new_event_loop().run_until_complete(_setup())
    yield repo
    asyncio.new_event_loop().run_until_complete(engine.dispose())


def _build_service(repo: VoiceTwinRepository, tmp_path, **config_kwargs) -> VoiceCloneService:
    config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"), **config_kwargs)
    svc = VoiceCloneService(config)
    svc.repo = repo
    svc.client = MagicMock()
    return svc


def _stale_created(minutes: int = 31) -> datetime:
    """超过默认 30 分钟阈值的 created_at（naive UTC，对齐 DateTime 列读回形态）。"""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes)


async def _create_row(repo: VoiceTwinRepository, voice_id: str, *, status: str = "training", created_at: datetime | None = None) -> VoiceTwinRow:
    row = VoiceTwinRow(
        id=voice_id,
        user_id="default",
        voice_name=f"音色{voice_id}",
        speaker_id=f"S_{voice_id}",
        model_type=5,
        status=status,
    )
    if created_at is not None:
        row.created_at = created_at
    return await repo.create(row)


class TestTrainingReaper:
    @pytest.mark.asyncio
    async def test_stale_training_row_remote_still_training_marks_failed(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """超时行 + 远端仍 training（status=1）→ failed，error_message 含「训练超时」。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "reap-1", created_at=_stale_created())
        svc.client.get_status = AsyncMock(return_value={"speaker_id": "S_reap-1", "status": 1})

        reaped = await svc._reap_stale_training()

        assert reaped == 1
        row = await async_voice_repo.get_by_id("reap-1")
        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "训练超时" in row.error_message
        assert "30" in row.error_message

    @pytest.mark.asyncio
    async def test_stale_training_row_remote_ready_marks_ready(self, async_voice_repo: VoiceTwinRepository, tmp_path, monkeypatch):
        """超时行 + 远端已 ready（status=2）→ ready（不冤枉慢训练），demo 走 best-effort 缓存。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "reap-2", created_at=_stale_created())
        svc.client.get_status = AsyncMock(return_value={"speaker_id": "S_reap-2", "status": 2, "demo_audio": "https://example.com/demo.wav"})
        monkeypatch.setattr(svc, "_cache_demo_audio", AsyncMock(return_value="/tmp/reap-2-demo.wav"))

        reaped = await svc._reap_stale_training()

        assert reaped == 1
        row = await async_voice_repo.get_by_id("reap-2")
        assert row is not None
        assert row.status == "ready"
        assert row.demo_audio_path == "/tmp/reap-2-demo.wav"
        svc._cache_demo_audio.assert_awaited_once_with("reap-2", "https://example.com/demo.wav")

    @pytest.mark.asyncio
    async def test_fresh_training_row_untouched(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """未超阈值的 training 行 → 状态不变，且不请求远端。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "reap-3")  # created_at=now
        svc.client.get_status = AsyncMock()

        reaped = await svc._reap_stale_training()

        assert reaped == 0
        row = await async_voice_repo.get_by_id("reap-3")
        assert row is not None
        assert row.status == "training"
        svc.client.get_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_row_poll_timeout_marks_failed(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """超时行 poll 抛 httpx.ReadTimeout → 标 failed 收敛（超时行 poll 失败即收敛）。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "reap-4", created_at=_stale_created())
        svc.client.get_status = AsyncMock(side_effect=httpx.ReadTimeout("boom"))

        reaped = await svc._reap_stale_training()

        assert reaped == 1
        row = await async_voice_repo.get_by_id("reap-4")
        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "训练超时" in row.error_message

    @pytest.mark.asyncio
    async def test_timeout_minutes_configurable(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """training_timeout_minutes 入 config：阈值调小后，较新的行也被收敛。"""
        svc = _build_service(async_voice_repo, tmp_path, training_timeout_minutes=5)
        await _create_row(async_voice_repo, "reap-5", created_at=_stale_created(minutes=6))
        svc.client.get_status = AsyncMock(return_value={"speaker_id": "S_reap-5", "status": 1})

        reaped = await svc._reap_stale_training()

        assert reaped == 1
        row = await async_voice_repo.get_by_id("reap-5")
        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "5分钟" in row.error_message

    @pytest.mark.asyncio
    async def test_one_row_failure_does_not_block_others(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """每行独立 try/except：首行处理抛异常（repo 更新失败）不影哋后续行收敛。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "reap-6a", created_at=_stale_created())
        await _create_row(async_voice_repo, "reap-6b", created_at=_stale_created())
        svc.client.get_status = AsyncMock(return_value={"status": 1})

        original_update = async_voice_repo.update_status
        calls = {"n": 0}

        async def _flaky_update(voice_id, **kwargs):
            calls["n"] += 1
            if voice_id == "reap-6a":
                raise RuntimeError("db boom")
            return await original_update(voice_id, **kwargs)

        svc.repo = MagicMock(wraps=async_voice_repo)
        svc.repo.list_by_status = async_voice_repo.list_by_status
        svc.repo.update_status = _flaky_update

        await svc._reap_stale_training()

        row_b = await async_voice_repo.get_by_id("reap-6b")
        assert row_b is not None
        assert row_b.status == "failed"

    @pytest.mark.asyncio
    async def test_start_stop_training_reaper_idempotent(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """start 幂等（重复调用同一 task）；stop cancel 后 task 结束；interval=0 no-op。"""
        svc = _build_service(async_voice_repo, tmp_path, training_reaper_interval_minutes=5)
        svc.start_training_reaper()
        task = svc._training_reaper_task
        assert task is not None and not task.done()
        svc.start_training_reaper()
        assert svc._training_reaper_task is task  # 重复启动不替换
        await svc.stop_training_reaper()
        assert task.done()
        assert svc._training_reaper_task is None

        svc_off = _build_service(async_voice_repo, tmp_path, training_reaper_interval_minutes=0)
        svc_off.start_training_reaper()
        assert svc_off._training_reaper_task is None


class TestPollStatusExceptionClassification:
    @pytest.mark.asyncio
    async def test_key_error_marks_failed_immediately(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """KeyError（代码/数据形态错误）→ 立即 failed，不再保持 training 隐形 30 分钟。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "poll-ke")
        svc.client.get_status = AsyncMock(side_effect=KeyError("status"))

        row = await svc.poll_status("poll-ke")

        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "状态轮询内部错误" in row.error_message

    @pytest.mark.asyncio
    async def test_read_timeout_keeps_training(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """httpx.ReadTimeout（传输瞬断）→ 保持 training 继续轮询，不崩不误判。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "poll-to")
        svc.client.get_status = AsyncMock(side_effect=httpx.ReadTimeout("boom"))

        row = await svc.poll_status("poll-to")

        assert row is not None
        assert row.status == "training"
        fetched = await async_voice_repo.get_by_id("poll-to")
        assert fetched is not None
        assert fetched.status == "training"

    @pytest.mark.asyncio
    async def test_ready_path_regression(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """正常 ready 路径回归：status=2 → poll_status 返回 ready（既有效能不破）。"""
        svc = _build_service(async_voice_repo, tmp_path)
        await _create_row(async_voice_repo, "poll-ok")
        svc.client.get_status = AsyncMock(return_value={"speaker_id": "S_poll-ok", "status": 2})

        row = await svc.poll_status("poll-ok")

        assert row is not None
        assert row.status == "ready"

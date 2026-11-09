"""Tests for voice twin backend modules."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.gateway.voice_twin.config import VoiceCloneConfig
from app.gateway.voice_twin.errors import get_friendly_error
from app.gateway.voice_twin.models import VoiceTwinRow
from app.gateway.voice_twin.repository import VoiceTwinRepository
from app.gateway.voice_twin.schemas import (
    MODELS_INFO,
    SynthesizeRequest,
    VoiceTwinResponse,
    VoiceTwinUploadRequest,
    get_resource_id,
)
from app.gateway.voice_twin.service import _VOLC_STATUS_MAP, NEEDS_TRANSCODE_FORMATS, SUPPORTED_FORMATS, VoiceCloneService
from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient


class TestErrorMapping:
    def test_known_error_code(self):
        assert get_friendly_error(1101) == "音频上传失败，请重新尝试"

    def test_wer_error(self):
        assert get_friendly_error(1109) == "音频与文本匹配度过低，请对着提词文本清晰朗读"

    def test_success_code(self):
        assert get_friendly_error(0) == "成功"

    def test_unknown_error_code(self):
        assert get_friendly_error(9999) == "未知错误，请稍后重试"

    def test_none_error_code(self):
        assert get_friendly_error(None) == "未知错误，请稍后重试"

    def test_all_codes_have_messages(self):
        from app.gateway.voice_twin.errors import ERROR_MESSAGES

        for code, msg in ERROR_MESSAGES.items():
            assert isinstance(code, int)
            assert isinstance(msg, str)
            assert len(msg) > 0


class TestVoiceCloneConfig:
    def test_defaults(self):
        cfg = VoiceCloneConfig()
        assert cfg.enabled is False
        # B2（2026-08-09）：default_model_type 死配置已删除（全后端无消费点，
        # 默认值五方统一为 5：schema/DB/前端/上传参数）。
        assert not hasattr(cfg, "default_model_type")
        assert cfg.default_language == 0
        assert cfg.max_upload_bytes == 10 * 1024 * 1024
        assert cfg.min_duration_seconds == 30
        assert cfg.storage_dir == ".deer-flow/voice-twins"

    def test_custom_values(self):
        cfg = VoiceCloneConfig(
            enabled=True,
            appid="test_app",
            token="test_token",
        )
        assert cfg.enabled is True
        assert cfg.appid == "test_app"

    def test_edge_tts_config_defaults(self):
        """A2：edge-tts 默认开启（兜底链安全网），超时 60s。"""
        cfg = VoiceCloneConfig()
        assert cfg.edge_tts.enabled is True
        assert cfg.edge_tts.timeout == 60.0

    def test_training_reaper_config_defaults(self):
        """D1：训练超时 30min / 巡检 5min 默认值。"""
        cfg = VoiceCloneConfig()
        assert cfg.training_timeout_minutes == 30
        assert cfg.training_reaper_interval_minutes == 5


# ---------------------------------------------------------------------------
# VoiceTwinRow ORM tests
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_session():
    """Create an in-memory SQLite session for voice twin tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    VoiceTwinRow.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class TestVoiceTwinRow:
    def test_create_voice_twin(self, voice_session: Session):
        row = VoiceTwinRow(
            id="test-uuid-1",
            user_id="default",
            voice_name="张老师_标准",
            speaker_id="S_test123",
            model_type=5,
            language=0,
        )
        voice_session.add(row)
        voice_session.commit()
        voice_session.refresh(row)

        assert row.id == "test-uuid-1"
        assert row.voice_name == "张老师_标准"
        assert row.speaker_id == "S_test123"
        assert row.model_type == 5
        assert row.status == "uploading"
        assert row.error_code is None
        assert isinstance(row.created_at, datetime)

    def test_status_machine_values(self, voice_session: Session):
        row = VoiceTwinRow(
            id="test-uuid-2",
            user_id="default",
            voice_name="测试",
            speaker_id="S_test456",
            status="training",
        )
        voice_session.add(row)
        voice_session.commit()

        fetched = voice_session.query(VoiceTwinRow).filter_by(id="test-uuid-2").one()
        assert fetched.status == "training"

    def test_repr(self, voice_session: Session):
        row = VoiceTwinRow(
            id="test-uuid-4",
            user_id="default",
            voice_name="测试",
            speaker_id="S_repr",
        )
        voice_session.add(row)
        voice_session.commit()
        r = repr(row)
        assert "VoiceTwinRow" in r
        assert "test-uuid-4" in r
        assert "uploading" in r

    def test_unique_speaker_id(self, voice_session: Session):
        row1 = VoiceTwinRow(id="u1", user_id="default", voice_name="A", speaker_id="S_dup")
        voice_session.add(row1)
        voice_session.commit()

        row2 = VoiceTwinRow(id="u2", user_id="default", voice_name="B", speaker_id="S_dup")
        voice_session.add(row2)
        with pytest.raises(Exception):
            voice_session.commit()


class TestSchemas:
    def test_get_resource_id_icl_v3(self):
        assert get_resource_id(5) == "seed-icl-2.0"

    def test_get_resource_id_icl_v2(self):
        assert get_resource_id(4) == "seed-icl-2.0"

    def test_get_resource_id_icl_v1(self):
        assert get_resource_id(1) == "seed-icl-1.0"

    def test_get_resource_id_dit(self):
        assert get_resource_id(3) == "seed-icl-1.0"

    def test_models_info_completeness(self):
        model_types = {m.model_type for m in MODELS_INFO}
        assert model_types == {1, 2, 3, 4, 5}

    def test_upload_request_defaults(self):
        """VoiceTwinUploadRequest applies documented defaults + ranges."""
        req = VoiceTwinUploadRequest(temp_file_id="tmp_1", voice_name="n")
        assert req.model_type == 5  # default
        assert req.language == 0  # default
        assert req.extra_params is None

    def test_upload_request_rejects_out_of_range_model_type(self):
        """The /upload route now validates via this schema — model_type=99
        (previously passed through unvalidated to Volcengine) must be rejected.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VoiceTwinUploadRequest(temp_file_id="tmp_1", voice_name="n", model_type=99)

    def test_upload_request_rejects_blank_voice_name(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            VoiceTwinUploadRequest(temp_file_id="tmp_1", voice_name="")

    def test_synthesize_request_defaults(self):
        req = SynthesizeRequest(text="测试")
        assert req.output_format == "mp3"
        assert req.speed_ratio == 1.0

    def test_voice_twin_response_from_attributes(self):
        resp = VoiceTwinResponse(
            id="test",
            user_id="u",
            voice_name="n",
            speaker_id="s",
            model_type=5,
            language=0,
            status="ready",
        )
        assert resp.status == "ready"


# ---------------------------------------------------------------------------
# Async VoiceTwinRepository tests
# ---------------------------------------------------------------------------


@pytest.fixture
def async_voice_repo():
    """Create an async in-memory SQLite repo for testing.

    Uses a freshly-created event loop for setup/teardown instead of the
    deprecated ``asyncio.get_event_loop()``. The previous form reused the
    pytest-asyncio session loop, which another test file may have already
    closed → RuntimeError "no current event loop" when this file ran after
    the production tests (the cross-file event-loop contamination).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(VoiceTwinRow.metadata.create_all)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        return VoiceTwinRepository(sf)

    repo = asyncio.new_event_loop().run_until_complete(_setup())
    yield repo
    asyncio.new_event_loop().run_until_complete(engine.dispose())


class TestVoiceTwinRepository:
    @pytest.mark.asyncio
    async def test_create_and_get(self, async_voice_repo: VoiceTwinRepository):
        row = VoiceTwinRow(
            id="repo-1",
            user_id="default",
            voice_name="测试",
            speaker_id="S_repo1",
        )
        created = await async_voice_repo.create(row)
        assert created.id == "repo-1"

        fetched = await async_voice_repo.get_by_id("repo-1")
        assert fetched is not None
        assert fetched.voice_name == "测试"

    @pytest.mark.asyncio
    async def test_list_all(self, async_voice_repo: VoiceTwinRepository):
        for i in range(3):
            await async_voice_repo.create(
                VoiceTwinRow(
                    id=f"list-{i}",
                    user_id="default",
                    voice_name=f"V{i}",
                    speaker_id=f"S_list{i}",
                )
            )
        rows = await async_voice_repo.list_all()
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_list_by_user(self, async_voice_repo: VoiceTwinRepository):
        await async_voice_repo.create(
            VoiceTwinRow(
                id="lu-1",
                user_id="user_a",
                voice_name="A",
                speaker_id="S_ua",
            )
        )
        await async_voice_repo.create(
            VoiceTwinRow(
                id="lu-2",
                user_id="user_b",
                voice_name="B",
                speaker_id="S_ub",
            )
        )
        rows = await async_voice_repo.list_all(user_id="user_a")
        assert len(rows) == 1
        assert rows[0].voice_name == "A"

    @pytest.mark.asyncio
    async def test_update_status(self, async_voice_repo: VoiceTwinRepository):
        await async_voice_repo.create(
            VoiceTwinRow(
                id="upd-1",
                user_id="default",
                voice_name="U",
                speaker_id="S_upd",
            )
        )
        updated = await async_voice_repo.update_status("upd-1", status="ready", demo_audio_path="/tmp/demo.wav")
        assert updated is not None
        assert updated.status == "ready"
        assert updated.demo_audio_path == "/tmp/demo.wav"

    @pytest.mark.asyncio
    async def test_update_status_not_found(self, async_voice_repo: VoiceTwinRepository):
        result = await async_voice_repo.update_status("nonexistent", status="ready")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, async_voice_repo: VoiceTwinRepository):
        await async_voice_repo.create(
            VoiceTwinRow(
                id="del-1",
                user_id="default",
                voice_name="D",
                speaker_id="S_del",
            )
        )
        assert await async_voice_repo.delete("del-1") is True
        assert await async_voice_repo.get_by_id("del-1") is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_voice_repo: VoiceTwinRepository):
        assert await async_voice_repo.delete("nonexistent") is False


# ---------------------------------------------------------------------------
# VolcengineTTSClient tests
# ---------------------------------------------------------------------------


class TestVolcengineClient:
    def test_v1_headers(self):
        client = VolcengineTTSClient(appid="app1", token="tok1")
        headers = client._v1_headers("seed-icl-2.0")
        assert headers["Authorization"] == "Bearer;tok1"
        assert headers["Resource-Id"] == "seed-icl-2.0"

    def test_v3_tts_headers(self):
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")
        headers = client._v3_tts_headers("seed-icl-2.0")
        assert "Authorization" not in headers
        assert headers["X-Api-Key"] == "ak-123"
        assert headers["X-Api-Resource-Id"] == "seed-icl-2.0"
        assert headers["X-Api-Request-Id"]  # UUID present
        assert "X-Api-App-Id" not in headers

    def test_v3_training_headers(self):
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")
        headers = client._v3_training_headers()
        assert headers["X-Api-Key"] == "ak-123"
        assert "X-Api-Request-Id" in headers
        # New console auth does NOT include X-Api-App-Id
        assert "X-Api-App-Id" not in headers

    @pytest.mark.asyncio
    async def test_upload_audio_v1_path(self):
        client = VolcengineTTSClient(appid="app1", token="tok1")

        mock_response = MagicMock()
        mock_response.json.return_value = {"BaseResp": {"StatusCode": 0, "StatusMessage": ""}, "speaker_id": "S_test"}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.upload_audio(
                speaker_id="S_test",
                audio_bytes=b"fake_audio",
                audio_format="wav",
                model_type=5,
            )

            assert result["BaseResp"]["StatusCode"] == 0
            call_args = mock_client.post.call_args
            assert "/api/v1/mega_tts/audio/upload" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_upload_audio_v3_path(self):
        """Pre-paid: speaker_id starts with S_ → use directly, no custom_speaker_id."""
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "speaker_id": "S_test",
            "status": 2,
            "speaker_status": [{"demo_audio": "https://example.com/demo.wav", "model_type": 4}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.upload_audio(
                speaker_id="S_test",
                audio_bytes=b"fake_audio",
                audio_format="wav",
                model_type=5,
            )

            assert result["speaker_id"] == "S_test"
            assert result["status"] == 2
            assert result["demo_audio"] == "https://example.com/demo.wav"
            call_args = mock_client.post.call_args
            assert "/api/v3/tts/voice_clone" in call_args[0][0]
            # Pre-paid: speaker_id used directly
            body = call_args.kwargs["json"]
            assert body["speaker_id"] == "S_test"
            assert "custom_speaker_id" not in body

    @pytest.mark.asyncio
    async def test_upload_audio_v3_postpaid(self):
        """Post-paid: usr_ prefix → custom_speaker_id format."""
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "speaker_id": "custom_speaker_id",
            "status": 2,
            "speaker_status": [{"demo_audio": "https://example.com/demo.wav", "model_type": 5}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.upload_audio(
                speaker_id="usr_test123",
                audio_bytes=b"fake_audio",
                audio_format="wav",
                model_type=5,
            )

            assert result["speaker_id"] == "custom_speaker_id"
            call_args = mock_client.post.call_args
            # Post-paid: custom_speaker_id format
            body = call_args.kwargs["json"]
            assert body["speaker_id"] == "custom_speaker_id"
            assert body["custom_speaker_id"] == "usr_test123"

    @pytest.mark.asyncio
    async def test_get_status_v1(self):
        client = VolcengineTTSClient(appid="app1", token="tok1")

        mock_response = MagicMock()
        mock_response.json.return_value = {"BaseResp": {"StatusCode": 0}, "speaker_id": "S_test", "status": 2}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_status("S_test")
            assert result["speaker_id"] == "S_test"
            assert "/api/v1/mega_tts/status" in mock_client.post.call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_status_v3(self):
        """Pre-paid: speaker_id starts with S_ → use directly."""
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "speaker_id": "S_test",
            "status": 2,
            "speaker_status": [{"demo_audio": "https://example.com/demo.wav", "model_type": 4}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_status("S_test")
            assert result["speaker_id"] == "S_test"
            assert result["status"] == 2
            assert "/api/v3/tts/get_voice" in mock_client.post.call_args[0][0]
            # Pre-paid: speaker_id used directly
            body = mock_client.post.call_args.kwargs["json"]
            assert body["speaker_id"] == "S_test"
            assert "custom_speaker_id" not in body

    @pytest.mark.asyncio
    async def test_get_status_v3_postpaid(self):
        """Post-paid: usr_ prefix → custom_speaker_id format."""
        client = VolcengineTTSClient(appid="app1", token="tok1", access_key="ak-123")

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "speaker_id": "custom_speaker_id",
            "status": 2,
            "speaker_status": [{"demo_audio": "https://example.com/demo.wav", "model_type": 5}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.get_status("usr_test123")
            body = mock_client.post.call_args.kwargs["json"]
            assert body["speaker_id"] == "custom_speaker_id"
            assert body["custom_speaker_id"] == "usr_test123"


# ---------------------------------------------------------------------------
# VoiceCloneService tests
# ---------------------------------------------------------------------------


class TestVoiceCloneService:
    def test_supported_formats(self):
        assert "wav" in SUPPORTED_FORMATS
        assert "mp3" in SUPPORTED_FORMATS
        assert "webm" not in SUPPORTED_FORMATS

    def test_needs_transcode_formats(self):
        assert "webm" in NEEDS_TRANSCODE_FORMATS

    @pytest.mark.asyncio
    async def test_prescreen_rejects_oversized(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        result = await svc.prescreen(b"x" * 11 * 1024 * 1024, "test.wav")
        assert result["status"] == "fail"
        assert "超过限制" in result["error"]

    @pytest.mark.asyncio
    async def test_prescreen_rejects_unsupported_format(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        result = await svc.prescreen(b"fake", "test.exe")
        assert result["status"] == "fail"
        assert "不支持" in result["error"]

    @pytest.mark.asyncio
    async def test_prescreen_passes_wav(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav")
        assert result["status"] == "pass"
        assert result["format"] == "wav"
        assert result["needs_transcode"] is False
        assert result["temp_file_id"].startswith("tmp_")

    @pytest.mark.asyncio
    async def test_prescreen_detects_webm(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc._ffmpeg_available = True
        result = await svc.prescreen(b"fake_webm" * 100, "recording.webm")
        assert result["status"] == "pass"
        assert result["needs_transcode"] is True
        assert any("webm" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_prescreen_webm_no_ffmpeg(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc._ffmpeg_available = False
        result = await svc.prescreen(b"fake_webm" * 100, "recording.webm")
        assert result["status"] == "fail"
        assert "ffmpeg" in result["error"]

    @pytest.mark.asyncio
    async def test_prescreen_rejects_too_short(self, tmp_path):
        """min_duration_seconds is now enforced (was previously dead config)."""
        from app.gateway.voice_twin import service as svc_mod

        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"), min_duration_seconds=30.0)
        svc = VoiceCloneService(config)
        # Force _probe_duration to report a clip shorter than the 30s minimum.
        with patch.object(svc_mod, "_probe_duration", return_value=5.0):
            result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav")
        assert result["status"] == "fail"
        assert "时长过短" in result["error"]
        assert result["duration"] == 5.0
        # The temp file must be cleaned up on rejection.
        assert not any((tmp_path / "voice" / "temp").iterdir())

    @pytest.mark.asyncio
    async def test_prescreen_reports_real_duration(self, tmp_path):
        from app.gateway.voice_twin import service as svc_mod

        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"), min_duration_seconds=30.0)
        svc = VoiceCloneService(config)
        with patch.object(svc_mod, "_probe_duration", return_value=45.0):
            result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav")
        assert result["status"] == "pass"
        assert result["duration"] == 45.0

    def test_check_ffmpeg_returns_bool(self):
        from app.gateway.voice_twin.service import _check_ffmpeg

        result = _check_ffmpeg()
        assert isinstance(result, bool)

    def test_purge_voice_files_removes_originals_and_demo(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        base = svc._get_storage_base()
        original = base / "originals" / "vid-1.wav"
        demo = base / "demos" / "vid-1.wav"
        original.write_bytes(b"orig")
        demo.write_bytes(b"demo")

        svc.purge_voice_files("vid-1")

        assert not original.exists()
        assert not demo.exists()

    def test_purge_voice_files_missing_is_ok(self, tmp_path):
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        # No files exist — must not raise.
        svc.purge_voice_files("never-existed")

    def test_purge_voice_files_removes_preview_cache(self, tmp_path):
        """删除音色须连带清 voice_previews/{voice_id}_*（防删后凭 voice_id+文本 md5
        继续取缓存试听 + 防磁盘泄漏）。其他音色的缓存不受影响，重复 purge 幂等。"""
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        base = svc._get_storage_base()
        previews_dir = base / "voice_previews"
        previews_dir.mkdir()
        preview_a = previews_dir / "vid-1_abcd1234.mp3"
        preview_b = previews_dir / "vid-1_efgh5678.mp3"
        other_preview = previews_dir / "vid-2_abcd1234.mp3"
        for p in (preview_a, preview_b, other_preview):
            p.write_bytes(b"preview")

        svc.purge_voice_files("vid-1")

        assert not preview_a.exists()
        assert not preview_b.exists()
        assert other_preview.exists()
        svc.purge_voice_files("vid-1")  # idempotent: second purge must not raise

    def test_cleanup_storage_evicts_stale_files(self, tmp_path):
        import os
        import time

        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        base = svc._get_storage_base()

        # Stale temp file (>24h)
        stale = base / "temp" / "stale.tmp"
        stale.write_bytes(b"old")
        old_ts = time.time() - (25 * 3600)
        os.utime(stale, (old_ts, old_ts))
        # Fresh temp file (<24h) — must survive
        fresh = base / "temp" / "fresh.tmp"
        fresh.write_bytes(b"new")

        # Stale preview (>7d)
        preview_dir = tmp_path / "previews"
        preview_dir.mkdir()
        stale_preview = preview_dir / "preset_1_abc.wav"
        stale_preview.write_bytes(b"old-preview")
        very_old = time.time() - (8 * 24 * 3600)
        os.utime(stale_preview, (very_old, very_old))

        counts = svc.cleanup_storage(preview_dir=preview_dir)

        assert counts["temp"] == 1
        assert counts["previews"] == 1
        assert not stale.exists()
        assert fresh.exists()
        assert not stale_preview.exists()

    def test_cleanup_storage_evicts_stale_outputs(self, tmp_path):
        """VT-2（R3，2026-09-09）：outputs 目标纳入清理——过期（>24h，与 temp 同档）
        删除并计数；窗口内（试听/下载产物）不动。"""
        import os
        import time

        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        base = svc._get_storage_base()

        stale_output = base / "outputs" / "out_stale.mp3"
        stale_output.write_bytes(b"old-output")
        old_ts = time.time() - (25 * 3600)
        os.utime(stale_output, (old_ts, old_ts))
        stale_raw = base / "outputs" / "_out_stale.wav"
        stale_raw.write_bytes(b"old-raw")
        os.utime(stale_raw, (old_ts, old_ts))
        # Fresh outputs (<24h) — download/preview window must survive
        fresh_output = base / "outputs" / "out_fresh.mp3"
        fresh_output.write_bytes(b"new-output")

        counts = svc.cleanup_storage()

        assert counts["outputs"] == 2
        assert not stale_output.exists()
        assert not stale_raw.exists()
        assert fresh_output.exists()

    def test_storage_reaper_config_default(self):
        cfg = VoiceCloneConfig()
        assert cfg.storage_reaper_interval_minutes == 60

    @pytest.mark.asyncio
    async def test_storage_reaper_start_stop_idempotent(self, tmp_path):
        """启停对称且幂等：重复 start 不重复建任务；stop 后任务取消；再 stop 不抛。"""
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)

        assert svc._storage_reaper_task is None
        svc.start_storage_reaper()
        task = svc._storage_reaper_task
        assert task is not None and not task.done()
        svc.start_storage_reaper()  # idempotent: same task, no duplicate
        assert svc._storage_reaper_task is task

        await svc.stop_storage_reaper()
        assert svc._storage_reaper_task is None
        assert task.done()
        await svc.stop_storage_reaper()  # idempotent stop

    def test_storage_reaper_disabled_by_zero_interval(self, tmp_path):
        """storage_reaper_interval_minutes=0 → start no-op（关闭约定与 training reaper 同）。"""
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"), storage_reaper_interval_minutes=0)
        svc = VoiceCloneService(config)
        svc.start_storage_reaper()
        assert svc._storage_reaper_task is None


# ---------------------------------------------------------------------------
# Multi-user isolation tests
# ---------------------------------------------------------------------------


class TestMultiUserIsolation:
    """Verify that voice twin endpoints enforce per-user isolation."""

    @pytest.mark.asyncio
    async def test_list_voices_filters_by_user(self, async_voice_repo: VoiceTwinRepository):
        """list_all(user_id=...) should only return voices belonging to that user."""
        # Create voices for user_a
        await async_voice_repo.create(VoiceTwinRow(id="iso-a1", user_id="user_a", voice_name="A Voice 1", speaker_id="S_ia1"))
        await async_voice_repo.create(VoiceTwinRow(id="iso-a2", user_id="user_a", voice_name="A Voice 2", speaker_id="S_ia2"))
        # Create voices for user_b
        await async_voice_repo.create(VoiceTwinRow(id="iso-b1", user_id="user_b", voice_name="B Voice 1", speaker_id="S_ib1"))

        # user_a should see 2 voices
        rows_a = await async_voice_repo.list_all(user_id="user_a")
        assert len(rows_a) == 2
        assert {r.id for r in rows_a} == {"iso-a1", "iso-a2"}

        # user_b should see 1 voice
        rows_b = await async_voice_repo.list_all(user_id="user_b")
        assert len(rows_b) == 1
        assert rows_b[0].id == "iso-b1"

        # No user filter returns all 3
        all_rows = await async_voice_repo.list_all()
        assert len(all_rows) == 3

    @pytest.mark.asyncio
    async def test_cross_user_access_denied(self, async_voice_repo: VoiceTwinRepository):
        """Router-level ownership checks should reject access to another user's voice twin."""
        from unittest.mock import patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.gateway.routers.voice_twin import router

        # Create a voice owned by user_a
        await async_voice_repo.create(
            VoiceTwinRow(
                id="cross-owner",
                user_id="user_a",
                voice_name="Owned by A",
                speaker_id="S_cross",
                status="ready",
            )
        )

        app = FastAPI()
        app.include_router(router)

        # Build a mock service that uses our test repo
        mock_service = MagicMock()
        mock_service.repo = async_voice_repo
        app.state.voice_clone_service = mock_service

        with TestClient(app) as client:
            # Simulate user_b trying to access user_a's voice
            with patch("app.gateway.routers.voice_twin.get_effective_user_id", return_value="user_b"):
                # GET status - should get 403
                resp = client.get("/api/voice-twins/cross-owner/status")
                assert resp.status_code == 403
                assert "Not your voice twin" in resp.json()["detail"]

                # GET demo - should get 403
                resp = client.get("/api/voice-twins/cross-owner/demo")
                assert resp.status_code == 403

                # POST synthesize - should get 403
                resp = client.post(
                    "/api/voice-twins/cross-owner/synthesize",
                    json={"text": "hello"},
                )
                assert resp.status_code == 403

                # DELETE - should get 403
                resp = client.delete("/api/voice-twins/cross-owner")
                assert resp.status_code == 403

            # Verify that user_a CAN access their own voice
            with patch("app.gateway.routers.voice_twin.get_effective_user_id", return_value="user_a"):
                mock_service.poll_status = AsyncMock(
                    return_value=MagicMock(
                        id="cross-owner",
                        speaker_id="S_cross",
                        status="ready",
                        error_code=None,
                        error_message=None,
                    )
                )
                mock_service.get_demo_path = MagicMock(return_value=None)
                resp = client.get("/api/voice-twins/cross-owner/status")
                assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Demo audio caching must be best-effort (P1#4)
# ---------------------------------------------------------------------------


class TestDemoCacheAtomicWrite:
    """D4：demo 缓存原子写——并发/中断不得产生半截文件。"""

    def _build_service(self, tmp_path) -> VoiceCloneService:
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.client = MagicMock()
        return svc

    def _mock_httpx(self, monkeypatch):
        """httpx.AsyncClient stub：GET 返回以 url 为 payload 的响应。"""

        class _Resp:
            def __init__(self, content: bytes):
                self.content = content

            def raise_for_status(self):
                return None

        class _Client:
            def __init__(self, timeout: float):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url: str):
                await asyncio.sleep(0.01)  # 制造并发交错窗口
                return _Resp(url.encode())

        monkeypatch.setattr("app.gateway.voice_twin.service.httpx.AsyncClient", _Client)

    @pytest.mark.asyncio
    async def test_concurrent_cache_no_partial_file(self, tmp_path, monkeypatch):
        self._mock_httpx(monkeypatch)
        svc = self._build_service(tmp_path)
        base = svc._get_storage_base()

        payload_a = "A" * 5000
        payload_b = "B" * 5000
        await asyncio.gather(
            svc._cache_demo_audio("v1", payload_a),
            svc._cache_demo_audio("v1", payload_b),
        )

        final = (base / "demos" / "v1.wav").read_bytes()
        # 终态必须是某一份的完整内容，不是混杂/半截
        assert final in (payload_a.encode(), payload_b.encode())
        assert len(final) == 5000
        # tmp 文件全部清理
        assert not list((base / "demos").glob("*.tmp"))

    @pytest.mark.asyncio
    async def test_tmp_cleaned_on_write_failure(self, tmp_path, monkeypatch):
        """下载抛错时不留 tmp 残渣。"""

        class _BoomClient:
            def __init__(self, timeout: float):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url: str):
                raise RuntimeError("network down")

        monkeypatch.setattr("app.gateway.voice_twin.service.httpx.AsyncClient", _BoomClient)
        svc = self._build_service(tmp_path)
        base = svc._get_storage_base()

        with pytest.raises(RuntimeError, match="network down"):
            await svc._cache_demo_audio("v2", "http://x")
        assert not (base / "demos" / "v2.wav").exists()
        assert not list((base / "demos").glob("*.tmp"))


class TestClonePathRetry:
    """D2：克隆 upload/synthesize 接入共享重试（曾裸调，一次抖动即失败）。"""

    def _build_service(self, tmp_path) -> VoiceCloneService:
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.client = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_synthesize_retries_structured_429(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        from app.gateway.voice_twin.volcengine_client import VolcengineTTSHTTPError

        svc = self._build_service(tmp_path)
        svc.repo = async_voice_repo
        await async_voice_repo.create(VoiceTwinRow(id="vt-r1", user_id="u", voice_name="v", speaker_id="S_r1", status="ready", model_type=5, language=0))
        svc.client.synthesize_sse = AsyncMock(side_effect=[VolcengineTTSHTTPError(429, "rate limit"), b"audio_ok"])

        out = await svc.synthesize("vt-r1", "hello")
        assert svc.client.synthesize_sse.call_count == 2
        assert (tmp_path / "voice" / "outputs" / out.split("/")[-1]).exists()

    @pytest.mark.asyncio
    async def test_synthesize_4xx_not_retried(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        from app.gateway.voice_twin.volcengine_client import VolcengineTTSHTTPError

        svc = self._build_service(tmp_path)
        svc.repo = async_voice_repo
        await async_voice_repo.create(VoiceTwinRow(id="vt-r2", user_id="u", voice_name="v", speaker_id="S_r2", status="ready", model_type=5, language=0))
        svc.client.synthesize_sse = AsyncMock(side_effect=VolcengineTTSHTTPError(401, "bad key"))

        with pytest.raises(VolcengineTTSHTTPError):
            await svc.synthesize("vt-r2", "hello")
        assert svc.client.synthesize_sse.call_count == 1

    @pytest.mark.asyncio
    async def test_synthesize_edge_voice(self, tmp_path):
        """A2：service.synthesize 识别 edge: 音色（无 DB 行）。"""
        svc = self._build_service(tmp_path)
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.edge_client = edge

        out = await svc.synthesize("edge:zh-CN-XiaoxiaoNeural", "hello")
        edge.synthesize.assert_awaited_once()
        assert edge.synthesize.call_args.kwargs["voice"] == "zh-CN-XiaoxiaoNeural"
        assert out.endswith(".mp3")

    @pytest.mark.asyncio
    async def test_synthesize_edge_unconfigured(self, tmp_path):
        svc = self._build_service(tmp_path)
        svc.edge_client = None
        with pytest.raises(ValueError, match="edge TTS"):
            await svc.synthesize("edge:zh-CN-XiaoxiaoNeural", "hello")

    @pytest.mark.asyncio
    async def test_synthesize_edge_no_outer_retry(self, tmp_path):
        """edge voice 不走 call_with_tts_retry（service.py #4 同构修复）。

        edge_client.synthesize 内部已有 tenacity 3× 重试，外层再包会 3×3=9 次。
        """
        from app.gateway.voice_twin import service as svc_mod

        svc = self._build_service(tmp_path)
        edge = MagicMock()
        edge.synthesize = AsyncMock(return_value=b"edge_mp3")
        svc.edge_client = edge

        with patch.object(svc_mod, "call_with_tts_retry", wraps=svc_mod.call_with_tts_retry) as spy:
            out = await svc.synthesize("edge:zh-CN-XiaoxiaoNeural", "hello")

        assert out.endswith(".mp3")
        spy.assert_not_called()  # edge path bypasses call_with_tts_retry
        edge.synthesize.assert_awaited_once()


class TestDemoCacheBestEffort:
    """demo 音频下载失败不得阻断训练状态推进（submit / poll 均 best-effort）。"""

    def _build_service(self, repo: VoiceTwinRepository, tmp_path) -> VoiceCloneService:
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.repo = repo
        svc.client = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_submit_training_demo_cache_failure_does_not_fail_voice(self, async_voice_repo: VoiceTwinRepository, tmp_path, monkeypatch):
        """demo 下载失败不得把训练成功的音色误判 failed（best-effort 缓存）。"""
        import hashlib

        from app.gateway.voice_twin import service as svc_mod

        svc = self._build_service(async_voice_repo, tmp_path)
        base = svc._get_storage_base()
        # temp_file_id 必须带提交用户（此处默认 "default"）的 sha1 前缀（Task 8 绑定校验）。
        user_tag = hashlib.sha1(b"default").hexdigest()[:8]
        temp_file_id = f"tmp_{user_tag}_demo1"
        (base / "temp" / f"{temp_file_id}.wav").write_bytes(b"fake_wav_data" * 100)

        ready_code = next(k for k, v in _VOLC_STATUS_MAP.items() if v == "ready")
        svc.client.upload_audio = AsyncMock(return_value={"speaker_id": "usr_demo1", "status": ready_code, "demo_audio": "https://example.com/demo.wav"})
        monkeypatch.setattr(svc, "_cache_demo_audio", AsyncMock(side_effect=RuntimeError("demo download boom")))

        with patch.object(svc_mod, "_probe_duration", return_value=45.0):
            row = await svc.submit_training(temp_file_id=temp_file_id, voice_name="测试音色", model_type=5, language=0, extra_params=None)

        assert row.status == "ready"
        assert row.demo_audio_path is None
        assert row.error_message is None or "demo download boom" not in row.error_message

    @pytest.mark.asyncio
    async def test_poll_status_demo_cache_failure_still_marks_ready(self, async_voice_repo: VoiceTwinRepository, tmp_path, monkeypatch):
        """poll_status：demo 下载失败不得让已就绪音色永卡 training。"""
        svc = self._build_service(async_voice_repo, tmp_path)
        await async_voice_repo.create(
            VoiceTwinRow(
                id="poll-1",
                user_id="default",
                voice_name="轮询音色",
                speaker_id="S_poll1",
                model_type=5,
                status="training",
            )
        )

        ready_code = next(k for k, v in _VOLC_STATUS_MAP.items() if v == "ready")
        svc.client.get_status = AsyncMock(return_value={"speaker_id": "S_poll1", "status": ready_code, "demo_audio": "https://example.com/demo.wav"})
        monkeypatch.setattr(svc, "_cache_demo_audio", AsyncMock(side_effect=RuntimeError("demo download boom")))

        row = await svc.poll_status("poll-1")

        assert row is not None
        assert row.status == "ready"
        assert row.demo_audio_path is None


# ---------------------------------------------------------------------------
# Task 8 安全包：上传 Content-Length 预检 + temp_file_id 绑定用户
# ---------------------------------------------------------------------------


class TestPrescreenAndUploadRouteSecurity:
    """Route-level：413 预检不读 body（内存 DoS 防线）；他人 temp_file_id → 403 不得 500。"""

    def _make_client(self, mock_service):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.gateway.routers.voice_twin import router

        app = FastAPI()
        app.include_router(router)
        app.state.voice_clone_service = mock_service
        return TestClient(app)

    def test_prescreen_rejects_oversized_content_length(self):
        """Content-Length 超限 → 413 不读 body（曾全量读内存后才查大小 = 内存 DoS）。"""
        mock_service = MagicMock()
        mock_service.config.max_upload_bytes = 10 * 1024 * 1024
        with self._make_client(mock_service) as client:
            resp = client.post(
                "/api/voice-twins/prescreen",
                files={"file": ("a.wav", b"x", "audio/wav")},
                headers={"content-length": str(999 * 1024 * 1024)},
            )
        assert resp.status_code == 413
        mock_service.prescreen.assert_not_called()

    def test_prescreen_under_limit_passes_precheck(self):
        """Content-Length 未超限 → 正常进入 service.prescreen（预检不得误伤正常上传）。"""
        mock_service = MagicMock()
        mock_service.config.max_upload_bytes = 10 * 1024 * 1024
        mock_service.prescreen = AsyncMock(return_value={"status": "pass", "temp_file_id": "tmp_x", "duration": 45.0, "format": "wav", "needs_transcode": False})
        with self._make_client(mock_service) as client:
            resp = client.post("/api/voice-twins/prescreen", files={"file": ("a.wav", b"x", "audio/wav")})
        assert resp.status_code == 200
        mock_service.prescreen.assert_awaited_once()

    def test_upload_with_other_users_temp_file_maps_to_403(self):
        """submit_training 的 PermissionError 必须以 403 外发，不得冒 500。"""
        mock_service = MagicMock()
        mock_service.submit_training = AsyncMock(side_effect=PermissionError("temp file does not belong to this user"))
        with self._make_client(mock_service) as client:
            resp = client.post("/api/voice-twins/upload", json={"temp_file_id": "tmp_deadbeefcafe_1234567890ab", "voice_name": "x"})
        assert resp.status_code == 403


class TestTempFileUserBinding:
    """prescreen 的 temp_file_id 绑定用户：tmp_{sha1(user_id)[:8]}_{uuid12}；

    submit_training 校验前缀，拿他人 temp_file_id 提交克隆 → PermissionError。"""

    def _build_service(self, repo, tmp_path) -> VoiceCloneService:
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.repo = repo
        svc.client = MagicMock()
        return svc

    @pytest.mark.asyncio
    async def test_prescreen_temp_file_id_embeds_user_tag(self, tmp_path):
        import hashlib

        svc = self._build_service(None, tmp_path)
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav", user_id="user_a")
        assert result["status"] == "pass"
        expected_prefix = f"tmp_{hashlib.sha1(b'user_a').hexdigest()[:8]}_"
        assert result["temp_file_id"].startswith(expected_prefix)

    @pytest.mark.asyncio
    async def test_prescreen_anonymous_user_gets_anon_tag(self, tmp_path):
        """未传 user_id（兼容旧调用方）→ anon 前缀，不得为空 tag。"""
        svc = self._build_service(None, tmp_path)
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav")
        assert result["status"] == "pass"
        assert result["temp_file_id"].startswith("tmp_anon_")

    @pytest.mark.asyncio
    async def test_submit_training_rejects_other_users_temp_file(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """temp_file_id 前缀绑定用户——拿他人 temp_file_id 提交克隆 → PermissionError。"""
        svc = self._build_service(async_voice_repo, tmp_path)
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav", user_id="user_a")
        assert result["status"] == "pass"

        with pytest.raises(PermissionError):
            await svc.submit_training(
                temp_file_id=result["temp_file_id"],
                voice_name="盗用的音色",
                model_type=5,
                language=0,
                extra_params=None,
                user_id="user_b",
            )

    @pytest.mark.asyncio
    async def test_submit_training_accepts_own_temp_file(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """同一用户的 temp_file_id 通过前缀校验，训练流程照常推进。"""
        svc = self._build_service(async_voice_repo, tmp_path)
        svc.client.upload_audio = AsyncMock(return_value={"speaker_id": "usr_own1", "status": 1})
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav", user_id="user_a")
        assert result["status"] == "pass"

        row = await svc.submit_training(
            temp_file_id=result["temp_file_id"],
            voice_name="自己的音色",
            model_type=5,
            language=0,
            extra_params=None,
            user_id="user_a",
        )
        assert row is not None
        assert row.user_id == "user_a"
        svc.client.upload_audio.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_submit_training_empty_user_id_consistent_tag(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """空 user_id 直调服务时两端标签一致（prescreen "anon" vs submit sha1("") 曾必 PermissionError）。

        路由层 get_effective_user_id() 恒非空故线上不可达；服务直调兜底应自洽。
        """
        svc = self._build_service(async_voice_repo, tmp_path)
        svc.client.upload_audio = AsyncMock(return_value={"speaker_id": "usr_anon1", "status": 1})
        result = await svc.prescreen(b"fake_wav_data" * 100, "recording.wav", user_id="")
        assert result["status"] == "pass"
        row = await svc.submit_training(
            temp_file_id=result["temp_file_id"],
            voice_name="匿名音色",
            model_type=5,
            language=0,
            extra_params=None,
            user_id="",
        )
        assert row is not None


# ---------------------------------------------------------------------------
# Task 10: 训练状态收敛（V1 API 错误 / 训练超时 / "成功"误显）
# ---------------------------------------------------------------------------


class TestTrainingStatusConvergence:
    """poll_status 必须收敛到 failed：V1 BaseResp 非 0、training 超 30 分钟；

    且失败路径不得把 ERROR_MESSAGES[0]="成功" 显示为失败原因。"""

    def _build_service(self, repo: VoiceTwinRepository, tmp_path) -> VoiceCloneService:
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.repo = repo
        return svc

    @pytest.mark.asyncio
    async def test_poll_status_v1_api_error_marks_failed(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """V1 BaseResp.StatusCode 非 0（鉴权/参数错）→ failed + 真实原因（曾免疫映射永卡 training）。"""
        svc = self._build_service(async_voice_repo, tmp_path)
        await async_voice_repo.create(
            VoiceTwinRow(
                id="conv-1",
                user_id="default",
                voice_name="收敛音色",
                speaker_id="S_conv1",
                model_type=5,
                status="training",
            )
        )
        # 走真实 _get_status_v1 路径（无 access_key → V1），HTTP 层 mock 出 API 级错误：
        # 错误响应无 "status" 字段，旧实现缺省 0 → "uploading" 永卡。
        svc.client = VolcengineTTSClient(appid="app1", token="tok1")

        mock_response = MagicMock()
        mock_response.json.return_value = {"BaseResp": {"StatusCode": 1107, "StatusMessage": "SpeakerID 未找到"}}
        mock_response.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        with patch("app.gateway.voice_twin.volcengine_client.httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_http_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
            row = await svc.poll_status("conv-1")

        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "SpeakerID 未找到" in row.error_message

    @pytest.mark.asyncio
    async def test_poll_status_training_timeout_marks_failed(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """training 超 30 分钟 → failed（远端任务丢失曾永卡 + 前端 5s 无限轮询）。"""
        svc = self._build_service(async_voice_repo, tmp_path)
        # created_at 为 naive UTC（DateTime 列不带 tz，SQLAlchemy/SQLite 读回即 naive）。
        stale_created = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=31)
        await async_voice_repo.create(
            VoiceTwinRow(
                id="conv-2",
                user_id="default",
                voice_name="超时音色",
                speaker_id="S_conv2",
                model_type=5,
                status="training",
                created_at=stale_created,
            )
        )
        svc.client = MagicMock()
        svc.client.get_status = AsyncMock()

        row = await svc.poll_status("conv-2")

        assert row is not None
        assert row.status == "failed"
        assert row.error_message is not None
        assert "超时" in row.error_message
        # 超时短路必须先于远端调用——不得再请求 Volcengine。
        svc.client.get_status.assert_not_called()

    def test_friendly_error_never_returns_success_for_failure(self):
        """失败路径不得显示"成功"（ERROR_MESSAGES[0] 陷阱）。"""
        from app.gateway.voice_twin.errors import get_friendly_error

        assert get_friendly_error(0, for_failure=True) != "成功"


# ---------------------------------------------------------------------------
# Task 17: list 响应带 demo_available——前端不再盲渲染 404 播放器。
# ---------------------------------------------------------------------------


class TestListVoicesDemoAvailable:
    def test_list_voices_includes_demo_available(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """列表响应带 demo_available：磁盘 demos/{id}.wav 存在 → True。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.gateway.routers.voice_twin import router

        asyncio.new_event_loop().run_until_complete(
            # conftest autouse fixture 注入 contextvar 用户 "test-user-autouse"，行须归属该用户。
            async_voice_repo.create(VoiceTwinRow(id="demo-1", user_id="test-user-autouse", voice_name="有试听", speaker_id="S_demo1", status="ready"))
        )
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        demos = svc._get_storage_base() / "demos"  # _ensure_dirs 已 mkdir demos/
        (demos / "demo-1.wav").write_bytes(b"RIFFdemo")
        svc.repo = async_voice_repo

        app = FastAPI()
        app.include_router(router)
        app.state.voice_clone_service = svc

        with TestClient(app) as client:
            resp = client.get("/api/voice-twins")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "demo-1"
        assert data[0]["demo_available"] is True

    def test_list_voices_demo_available_false_when_no_file(self, async_voice_repo: VoiceTwinRepository, tmp_path):
        """无磁盘 demo 文件 → demo_available=False（默认不盲渲染）。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.gateway.routers.voice_twin import router

        asyncio.new_event_loop().run_until_complete(async_voice_repo.create(VoiceTwinRow(id="nodemo-1", user_id="test-user-autouse", voice_name="无试听", speaker_id="S_nodemo1", status="ready")))
        config = VoiceCloneConfig(storage_dir=str(tmp_path / "voice"))
        svc = VoiceCloneService(config)
        svc.repo = async_voice_repo

        app = FastAPI()
        app.include_router(router)
        app.state.voice_clone_service = svc

        with TestClient(app) as client:
            resp = client.get("/api/voice-twins")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["demo_available"] is False


class TestPreviewStorageDirAnchored:
    """评审二轮（2026-10-06）：voice 预览缓存目录必须锚定 get_paths().base_dir——
    修前是 CWD 相对字面量（容器内 CWD=/app 时落未挂载区，预览静默丢失）。"""

    def test_preview_storage_dir_follows_deer_flow_home(self, tmp_path, monkeypatch):
        from app.gateway.routers import voice_twin_production as vtp
        from deerflow.config import paths as paths_mod

        monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
        monkeypatch.setattr(paths_mod, "_paths", None)
        d = vtp._preview_storage_dir()
        assert d == tmp_path / "voice-twins" / "voice_previews"
        assert d.is_dir()

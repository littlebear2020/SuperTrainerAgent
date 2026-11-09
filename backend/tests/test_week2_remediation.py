"""Tests for Week 2 remediation: payload_patch P0 fix, QihuTTS api_key, effective_frame_concurrency, manual audio API."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


@pytest.mark.asyncio
async def test_generate_draft_audio_uses_read_merge_write():
    """_generate_draft_audio should read-merge-write payload, not payload_patch."""
    from app.gateway.video_gen.service import VideoGenService

    mock_task = MagicMock()
    mock_task.payload = json.dumps({"topic": "test course"})
    mock_task.user_id = "user1"

    repo = AsyncMock()
    repo.get = AsyncMock(return_value=mock_task)
    repo.update = AsyncMock()

    svc = VideoGenService(repo=repo, config=VideoGenConfig(), providers=MagicMock())

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="zh-CN-YunjianNeural")
    sb = Storyboard(title="test", config=cfg, frames=[StoryboardFrame(index=0, narration="测试")])

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        task_dir = Path(tmpdir)
        (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
        with patch.object(svc, "_synthesize_audio", new_callable=AsyncMock):
            await svc._generate_draft_audio("task_001", task_dir)

    repo.update.assert_called_once()
    kwargs = repo.update.call_args.kwargs
    assert "payload_patch" not in kwargs, "Must not use payload_patch"
    payload_arg = kwargs.get("payload") or repo.update.call_args.args[1]
    merged = json.loads(payload_arg)
    assert merged["audio_ready"] is True
    assert merged["topic"] == "test course"


@pytest.mark.asyncio
async def test_synthesize_qihu_passes_api_key():
    """_synthesize_qihu should construct QihuTTSClient with api_key from env."""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    with patch.dict("os.environ", {"QIHU_API_KEY": "test-key-123"}, clear=False), patch("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient") as MockClient:
        mock_inst = MockClient.return_value
        mock_inst.synthesize_sse = AsyncMock(return_value=b"fake")
        with patch.object(tts_mod, "_wav_to_mp3"), patch.object(tts_mod, "_get_audio_duration", return_value=2.0):
            await tts_mod._synthesize_qihu("test", "preset:146", 1.0, "/tmp/test.mp3")
        MockClient.assert_called_once_with(api_key="test-key-123")


@pytest.mark.asyncio
async def test_synthesize_qihu_warns_on_empty_api_key(caplog):
    """_synthesize_qihu should warn when QIHU_API_KEY is empty."""
    import logging

    from app.gateway.video_gen.orchestration import tts as tts_mod

    with patch.dict("os.environ", {"QIHU_API_KEY": ""}, clear=False), patch("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient") as MockClient:
        mock_inst = MockClient.return_value
        mock_inst.synthesize_sse = AsyncMock(side_effect=Exception("401 Unauthorized"))
        with patch.object(tts_mod, "_synthesize_edge", new_callable=AsyncMock) as mock_edge:
            from app.gateway.video_gen.orchestration.tts import TTSResult

            mock_edge.return_value = TTSResult(audio_path="/tmp/out.mp3", srt="", duration=1.0)
            with caplog.at_level(logging.WARNING):
                await tts_mod._synthesize_qihu("test", "preset:146", 1.0, "/tmp/out.mp3")

    assert any("QIHU_API_KEY is empty" in r.message for r in caplog.records)
    mock_edge.assert_called_once()


def test_make_visual_source_ai_image_accepts_concurrency():
    """_make_visual_source should pass concurrency to AIImageVisualSource."""
    from app.gateway.video_gen.service import VideoGenService

    config = VideoGenConfig(frame_concurrency=3)
    with patch("app.gateway.video_gen.visual_sources.ai_image.AIImageVisualSource") as MockSrc:
        svc = VideoGenService(repo=MagicMock(), config=config, providers=MagicMock())
        svc._make_visual_source("ai_image", concurrency=1)
        assert MockSrc.call_args.kwargs.get("concurrency") == 1


@pytest.mark.asyncio
async def test_manual_audio_trigger():
    """POST /tasks/{task_id}/generate-audio should trigger background TTS."""
    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    mock_svc._generate_draft_audio = AsyncMock()
    mock_svc._storage_dir = "/tmp/video-gen"
    mock_svc.repo = AsyncMock()
    mock_task = MagicMock()
    mock_task.status = "draft"
    mock_task.user_id = "user1"
    mock_task.payload = '{"topic": "test"}'
    mock_svc.repo.get = AsyncMock(return_value=mock_task)
    # E1: 端点改用 svc.get_task(task_id, user_id)（按属主过滤，修 IDOR），不再是 svc.repo.get
    mock_svc.get_task = AsyncMock(return_value=mock_task)

    app.state.video_gen_service = mock_svc

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/video/tasks/vg_test001/generate-audio")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"

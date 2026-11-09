"""Tests for Week 5 remediation: MixedVisualSource, Voice API, pipeline_version integration, QihuTTS."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_has_mixed_sources_detects_mixed():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = VideoGenService(repo=MagicMock(), config=VideoGenConfig(), providers=MagicMock())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
        ],
    )
    assert svc._has_mixed_sources(sb) is True


def test_has_mixed_sources_false_when_uniform():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = VideoGenService(repo=MagicMock(), config=VideoGenConfig(), providers=MagicMock())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a"),
            StoryboardFrame(index=1, narration="b"),
        ],
    )
    assert svc._has_mixed_sources(sb) is False


def test_make_visual_source_mixed_creates_mixed():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    svc = VideoGenService(repo=MagicMock(), config=VideoGenConfig(), providers=MagicMock())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
        ],
    )
    visual = svc._make_visual_source_mixed(sb, concurrency=2)
    assert isinstance(visual, MixedVisualSource)
    assert "ai_video" in visual._sources
    assert "ai_image" in visual._sources


def test_make_visual_source_mixed_skips_remotion():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    svc = VideoGenService(repo=MagicMock(), config=VideoGenConfig(), providers=MagicMock())
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_image"),
            StoryboardFrame(index=1, narration="b", visual_source="remotion"),
        ],
    )
    visual = svc._make_visual_source_mixed(sb)
    assert isinstance(visual, MixedVisualSource)
    assert "ai_image" in visual._sources
    assert "remotion" not in visual._sources


# ---------------------------------------------------------------------------
# Task 2: Voice API lang branch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_api_no_lang_returns_curated():
    """Without lang param, should return curated voices directly."""
    from app.gateway.routers.video_gen import list_voices_endpoint

    result = await list_voices_endpoint(lang=None)
    assert isinstance(result, list)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_voice_api_lang_includes_preset_and_provider(monkeypatch):
    """With lang param, result should include provider field for each voice."""
    from app.gateway.routers.video_gen import list_voices_endpoint

    # Worktree/CI 没有 .env 时也必须验证 qihu 分支，不能依赖宿主真实 key。
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    with patch("app.gateway.video_gen.orchestration.voices.list_voices_cached", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [
            {"ShortName": "zh-CN-XiaoxiaoNeural", "FriendlyName": "Xiaoxiao", "Locale": "zh-CN", "Gender": "Female"},
        ]
        result = await list_voices_endpoint(lang="zh")

    # Each result item should have provider field
    assert all("provider" in v for v in result)
    providers = {v["provider"] for v in result}
    assert "edge-tts" in providers
    assert "qihu" in providers  # CURATED_VOICES has zh-CN qihu presets


# ---------------------------------------------------------------------------
# Task 3: pipeline_version integration verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_version_integration_calls_probe_and_decide(monkeypatch):
    """Verify probe + decide functions work together for pipeline routing."""
    from app.gateway.video_gen import worker_probe
    from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

    # _probe_cache 是模块级 60s TTL 缓存：probe 成功后会写回。monkeypatch 负责恢复
    # 测试前状态（同 tests/test_worker_probe_url.py:81 先例），finally 显式清空测试
    # 期间写回的缓存，避免污染后续调用方。
    monkeypatch.setattr(worker_probe, "_probe_cache", None, raising=False)

    try:
        sb = Storyboard(
            title="test",
            config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
            frames=[StoryboardFrame(index=i, narration=f"f{i}") for i in range(6)],
            scene_groups=[
                SceneGroup(index=0, title="a", frame_indices=[0, 1, 2]),
                SceneGroup(index=1, title="b", frame_indices=[3, 4, 5]),
            ],
        )

        # Simulate worker with v3_segmented capability
        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"capabilities": ["v1", "v2_mixed", "v3_segmented"]}
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            MockClient.return_value = mock_client

            caps = await worker_probe.probe_worker_capabilities("http://test:8040")
            pv = worker_probe.decide_pipeline_version(sb, caps)

        assert "v3_segmented" in caps
        assert pv == "v3_segmented"

        # Now test with mixed sources but no scene_groups
        monkeypatch.setattr(worker_probe, "_probe_cache", None, raising=False)
        sb2 = Storyboard(
            title="mixed",
            config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
            frames=[
                StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
                StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
            ],
        )
        pv2 = worker_probe.decide_pipeline_version(sb2, ["v1", "v2_mixed", "v3_segmented"])
        assert pv2 == "v2_mixed"
    finally:
        worker_probe._probe_cache = None


# ---------------------------------------------------------------------------
# Task 4: QihuTTS interface verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qihu_tts_preset_voice_id_passed_as_int(tmp_path, monkeypatch, tts_wav_bytes):
    """QihuTTSClient should receive voice_id as int when prefixed with 'preset:'.

    2026-10-12 CI 修复：无 QIHU_API_KEY 环境（GitHub runner）此前走 no-key 分支
    直落真 edge-tts（对 runner 持续 403）；本地因 import 期 load_dotenv() 恒有
    真 key 从未暴露。现显式 setenv 固定分支 + mock 返回合法 WAV（b"wav_bytes"
    假数据会在 wav→mp3 转码抛错、被 except 吞掉后静默外呼 edge）+ edge tripwire。
    """
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.orchestration.tts import _synthesize_qihu

    monkeypatch.setenv("QIHU_API_KEY", "test-key")

    async def _edge_tripwire(*args, **kwargs):
        raise AssertionError("不应触发 edge 回退（QIHU_API_KEY 已设 + 客户端已 mock）")

    monkeypatch.setattr(tts_mod, "_synthesize_edge", _edge_tripwire)

    output = str(tmp_path / "out.mp3")
    with patch("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.synthesize_sse = AsyncMock(return_value=tts_wav_bytes())
        MockClient.return_value = mock_instance

        await _synthesize_qihu("hello world", "preset:123456", 1.0, output)

        # speed 现随调用下发（QIHU_TTS_SPEED_ATEMPO=1 时传 None 走 atempo 后处理）
        mock_instance.synthesize_sse.assert_called_once_with(text="hello world", voice=123456, speed=1.0)


@pytest.mark.asyncio
async def test_qihu_tts_speed_silently_ignored(tmp_path, monkeypatch, tts_wav_bytes):
    """QihuTTS should accept speed param without error.

    同上 2026-10-12 离线化：setenv 固定 qihu 分支 + 合法 WAV + edge tripwire。
    """
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.orchestration.tts import _synthesize_qihu

    monkeypatch.setenv("QIHU_API_KEY", "test-key")

    async def _edge_tripwire(*args, **kwargs):
        raise AssertionError("不应触发 edge 回退（QIHU_API_KEY 已设 + 客户端已 mock）")

    monkeypatch.setattr(tts_mod, "_synthesize_edge", _edge_tripwire)

    output = str(tmp_path / "out.mp3")
    with patch("app.gateway.voice_twin.qihu_tts_client.QihuTTSClient") as MockClient:
        mock_instance = MagicMock()
        mock_instance.synthesize_sse = AsyncMock(return_value=tts_wav_bytes())
        MockClient.return_value = mock_instance

        # Various speed values should not raise
        await _synthesize_qihu("text", "preset:100", 0.5, output)
        await _synthesize_qihu("text", "preset:100", 2.0, output)

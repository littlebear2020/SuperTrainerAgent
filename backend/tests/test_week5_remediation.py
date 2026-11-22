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

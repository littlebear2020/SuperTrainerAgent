"""Tests for Week 4 remediation: worker probe, pipeline_version."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_probe_worker_capabilities_caches():
    """probe_worker_capabilities should cache results."""
    from app.gateway.video_gen import worker_probe

    worker_probe._probe_cache = None
    with patch("httpx.AsyncClient") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"capabilities": ["v1", "v2_mixed"]}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()
        MockClient.return_value = mock_client

        caps = await worker_probe.probe_worker_capabilities("http://test:8040")
        assert "v2_mixed" in caps

        # Second call should use cache (no new HTTP request)
        caps2 = await worker_probe.probe_worker_capabilities("http://test:8040")
        assert caps2 == caps
        assert mock_client.get.call_count == 1


@pytest.mark.asyncio
async def test_probe_worker_failure_returns_v1():
    """probe_worker_capabilities should return ['v1'] on failure."""
    from app.gateway.video_gen import worker_probe

    worker_probe._probe_cache = None
    with patch("httpx.AsyncClient", side_effect=Exception("connection refused")):
        caps = await worker_probe.probe_worker_capabilities("http://nonexistent:9999")
        assert caps == ["v1"]


def test_should_use_mixed_pipeline_true():
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.worker_probe import should_use_mixed_pipeline

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
        ],
    )
    assert should_use_mixed_pipeline(sb, ["v1", "v2_mixed"]) is True


def test_should_use_mixed_pipeline_false_uniform():
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.worker_probe import should_use_mixed_pipeline

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a"), StoryboardFrame(index=1, narration="b")],
    )
    assert should_use_mixed_pipeline(sb, ["v1", "v2_mixed"]) is False


def test_decide_pipeline_version_segmented():
    from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.worker_probe import decide_pipeline_version

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=i, narration=f"f{i}") for i in range(10)],
        scene_groups=[
            SceneGroup(index=0, title="a", frame_indices=[0, 1, 2, 3]),
            SceneGroup(index=1, title="b", frame_indices=[4, 5, 6, 7, 8, 9]),
        ],
    )
    assert decide_pipeline_version(sb, ["v1", "v2_mixed", "v3_segmented"]) == "v3_segmented"


def test_decide_pipeline_version_mixed():
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.worker_probe import decide_pipeline_version

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
        ],
    )
    assert decide_pipeline_version(sb, ["v1", "v2_mixed"]) == "v2_mixed"


def test_decide_pipeline_version_v1():
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.worker_probe import decide_pipeline_version

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a")],
    )
    assert decide_pipeline_version(sb, ["v1"]) == "v1"


def test_build_worker_payload_includes_pipeline_version():
    """build_worker_payload should include pipeline_version field."""
    from app.gateway.video_gen.service import build_worker_payload
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a")],
    )
    payload = build_worker_payload(sb, subtitle_path=None, audio_concat_path=None, pipeline_version="v3_segmented")
    assert payload["pipeline_version"] == "v3_segmented"


def test_build_worker_payload_defaults_to_v1():
    """build_worker_payload should default pipeline_version to v1."""
    from app.gateway.video_gen.service import build_worker_payload
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a")],
    )
    payload = build_worker_payload(sb, subtitle_path=None, audio_concat_path=None)
    assert payload["pipeline_version"] == "v1"

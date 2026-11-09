"""Tests for Week 5: TTS routing, 360 voices, and mixed visual sources."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.storyboard import (
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
)

# ---------------------------------------------------------------------------
# TTS routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_routes_to_edge_by_default():
    """Default voice_id should route to edge-tts."""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    with patch.object(tts_mod, "_synthesize_edge", new_callable=AsyncMock) as mock_edge:
        from app.gateway.video_gen.orchestration.tts import TTSResult

        mock_edge.return_value = TTSResult(audio_path="/tmp/test.mp3", srt="srt", duration=2.0)
        await tts_mod.synthesize("test", voice_id="zh-CN-YunjianNeural", speed=1.0, output_path="/tmp/test.mp3")
        assert mock_edge.called


@pytest.mark.asyncio
async def test_tts_routes_to_qihu_for_preset_prefix():
    """voice_id starting with 'preset:' should route to 360 TTS."""
    from app.gateway.video_gen.orchestration import tts as tts_mod

    with patch.object(tts_mod, "_synthesize_qihu", new_callable=AsyncMock) as mock_qihu:
        from app.gateway.video_gen.orchestration.tts import TTSResult

        mock_qihu.return_value = TTSResult(audio_path="/tmp/test.mp3", srt="", duration=2.0)
        await tts_mod.synthesize("test", voice_id="preset:146", speed=1.0, output_path="/tmp/test.mp3")
        assert mock_qihu.called


# ---------------------------------------------------------------------------
# 360 preset voices in curated list
# ---------------------------------------------------------------------------


def test_curated_voices_include_qihu_presets():
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    qihu_voices = [v for v in CURATED_VOICES if v.get("provider") == "qihu"]
    assert len(qihu_voices) >= 6
    assert all(v["id"].startswith("preset:") for v in qihu_voices)


def test_curated_voices_include_edge_tts():
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    edge_voices = [v for v in CURATED_VOICES if v.get("provider") == "edge-tts"]
    assert len(edge_voices) >= 10


# ---------------------------------------------------------------------------
# MixedVisualSource
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_visual_source_routes_by_frame_type():
    """MixedVisualSource should route each frame to the correct source."""
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    ai_video_source = MagicMock()
    ai_video_source.render = AsyncMock()
    ai_image_source = MagicMock()
    ai_image_source.render = AsyncMock()

    sources = {"ai_video": ai_video_source, "ai_image": ai_image_source}

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="test",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_video"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_image"),
            StoryboardFrame(index=2, narration="c", visual_source="ai_video"),
        ],
    )

    mixed = MixedVisualSource(sources)
    await mixed.render(sb)

    # ai_video_source should have been called with frames [0, 2]
    video_call = ai_video_source.render.call_args
    assert video_call.kwargs.get("only_frames") == [0, 2]

    # ai_image_source should have been called with frames [1]
    ai_call = ai_image_source.render.call_args
    assert ai_call.kwargs.get("only_frames") == [1]


@pytest.mark.asyncio
async def test_mixed_visual_source_uses_default_when_no_override():
    """Frames without visual_source should use config default."""
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    image_source = MagicMock()
    image_source.render = AsyncMock()

    sources = {"ai_image": image_source}

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="test",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a"),  # no visual_source → defaults to ai_image
            StoryboardFrame(index=1, narration="b"),
        ],
    )

    mixed = MixedVisualSource(sources)
    await mixed.render(sb)

    image_source.render.assert_called_once()
    assert image_source.render.call_args.kwargs.get("only_frames") == [0, 1]


@pytest.mark.asyncio
async def test_mixed_visual_source_skips_unregistered_types():
    """If a visual source type isn't registered, skip those frames gracefully."""
    from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

    image_source = MagicMock()
    image_source.render = AsyncMock()

    sources = {"ai_image": image_source}  # no ai_video registered

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="test",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", visual_source="ai_image"),
            StoryboardFrame(index=1, narration="b", visual_source="ai_video"),  # no source
        ],
    )

    mixed = MixedVisualSource(sources)
    await mixed.render(sb)  # should not crash

    image_source.render.assert_called_once()
    assert image_source.render.call_args.kwargs.get("only_frames") == [0]


# ---------------------------------------------------------------------------
# StoryboardFrame visual_source field
# ---------------------------------------------------------------------------


def test_frame_visual_source_default_none():
    f = StoryboardFrame(index=0, narration="test")
    assert f.visual_source is None


def test_frame_visual_source_set():
    f = StoryboardFrame(index=0, narration="test", visual_source="ai_image")
    assert f.visual_source == "ai_image"


def test_frame_visual_source_roundtrip():
    """visual_source should survive to_dict/from_dict roundtrip."""
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="a", visual_source="remotion")],
    )
    data = sb.to_dict()
    assert data["frames"][0]["visual_source"] == "remotion"

    sb2 = Storyboard.from_dict(data)
    assert sb2.frames[0].visual_source == "remotion"

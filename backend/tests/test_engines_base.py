"""Tests for video_gen engines base contracts (plan §4.1 / 阶段 1.1).

Engine Protocol + data models: SegmentArtifact, EngineOutcome, EngineContext,
VideoEngine. These are the layer-3 contracts that all engines (remotion/vimax) implement and the composition layer consumes.
"""

from pathlib import Path

import pytest

from app.gateway.video_gen.engines.base import (
    EngineContext,
    EngineOutcome,
    SegmentArtifact,
    VideoEngine,
)


def test_segment_artifact_required_fields():
    a = SegmentArtifact(frame_index=0, visual_path="/tmp/x.mp4", visual_type="video")
    assert a.frame_index == 0
    assert a.visual_path == "/tmp/x.mp4"
    assert a.visual_type == "video"
    assert a.duration == 0.0
    assert a.review_score is None


def test_engine_outcome_segments_mode():
    seg = SegmentArtifact(frame_index=1, visual_path="/tmp/a.png", visual_type="image", duration=2.0)
    out = EngineOutcome(mode="segments", segments=[seg])
    assert out.mode == "segments"
    assert out.segments is not None and len(out.segments) == 1
    assert out.finished_video_path is None


def test_engine_outcome_finished_mode():
    out = EngineOutcome(mode="finished", finished_video_path="/tmp/final.mp4")
    assert out.mode == "finished"
    assert out.finished_video_path == "/tmp/final.mp4"
    assert out.segments is None


def test_engine_context_carries_task_and_vimax_knobs():
    ctx = EngineContext(
        user_id="u1",
        task_id="t1",
        task_dir=Path("/tmp/task"),
        pipeline="script2video",
        style="realistic movie style",
    )
    assert ctx.task_id == "t1"
    assert ctx.pipeline == "script2video"
    assert ctx.style == "realistic movie style"
    # voice_id has a default
    assert ctx.voice_id


def test_video_engine_protocol_is_satisfied_by_duck_typed_class():
    class _DummyEngine:
        name = "dummy"

        async def render(self, ctx: EngineContext) -> EngineOutcome:
            return EngineOutcome(mode="finished", finished_video_path="/tmp/x.mp4")

        async def review_frame(self, ctx: EngineContext, frame_index: int, visual_path: str):
            return None

    # runtime_checkable Protocol: instance satisfies it structurally
    assert isinstance(_DummyEngine(), VideoEngine)


@pytest.mark.asyncio
async def test_dummy_engine_render_returns_outcome():
    class _E:
        name = "e"

        async def render(self, ctx: EngineContext) -> EngineOutcome:
            return EngineOutcome(mode="finished", finished_video_path="/tmp/o.mp4")

        async def review_frame(self, ctx, frame_index, visual_path):
            return None

    out = await _E().render(EngineContext(user_id="u", task_id="t", task_dir=Path("/tmp")))
    assert out.mode == "finished"

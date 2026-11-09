"""E5a: visual 渲染期间每帧须调 progress（75→90 渐进），不让 progress 冻在 75 致 stall reaper 误杀。

75% 是 gateway 侧 visual.render() 前置；整个 visual 生成期间 on_frame_done 旧只写
storyboard.json 不调 progress/不刷 updated_at → stall reaper 双谓词命中 → cancel →
"Cancelled (transient); resuming" → retry 从头跑 → 永远完不成。E5a 每帧推进 progress。
"""

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_make_on_frame_done_emits_per_frame_progress(tmp_path):
    """E5a: _on_frame_done 每帧调 progress（75→90 渐进）；修前只写文件不调 progress。"""
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    progresses: list[int | None] = []

    async def prog(*, progress=None, **k):
        progresses.append(progress)

    svc = VideoGenService.__new__(VideoGenService)
    svc._update_storyboard = AsyncMock()
    frames = [StoryboardFrame(index=i, narration="n", image_prompt="p", duration=5.0, visual_url=f"http://x/{i}", visual_type="image") for i in range(4)]
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1080, height=1920), frames=frames)
    on_fd = svc._make_on_frame_done(sb, tmp_path, progress=prog, total=4)
    for i in range(4):
        await on_fd(i)

    # 须有 75<p<90 的中间 progress（每帧推进），不让 progress 冻在 75
    assert any(p is not None and 75 < p < 90 for p in progresses), f"须每帧推进 progress 75→90，got {progresses}"
    assert progresses == sorted(progresses), f"progress 须单调递增，got {progresses}"
    assert progresses[-1] == 90, f"末帧后须 progress=90，got {progresses[-1]}"


@pytest.mark.asyncio
async def test_make_on_frame_done_no_progress_no_crash(tmp_path):
    """progress=None 时不调（不崩），仍写 storyboard（既有行为不破坏）。"""
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = VideoGenService.__new__(VideoGenService)
    svc._update_storyboard = AsyncMock()
    frames = [StoryboardFrame(index=0, narration="n", image_prompt="p", duration=5.0, visual_url="http://x/0", visual_type="image")]
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1080, height=1920), frames=frames)
    on_fd = svc._make_on_frame_done(sb, tmp_path, progress=None, total=1)
    await on_fd(0)  # 不崩
    svc._update_storyboard.assert_awaited_once()


def test_effective_stall_timeout_scales_with_frames():
    """E5b: effective_stall_timeout(total) = 1800 + 60*total——54 帧→5040s=84min 覆盖串行 ai_video。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.effective_stall_timeout(0) == 1800
    assert cfg.effective_stall_timeout(10) == 1800 + 600
    assert cfg.effective_stall_timeout(54) == 1800 + 60 * 54  # 5040
    # 单调：帧数越多 timeout 越长
    assert cfg.effective_stall_timeout(54) > cfg.effective_stall_timeout(10)


def test_stage_to_percent_maps_storyboard_and_decompose_stages():
    """E1: fill the 78%→82% gap. design_storyboard/load_storyboard and
    decompose_shots/load_shot_descriptions previously returned None → the bar
    froze at 78% during these LLM-heavy stages (confounding live tests once B1
    raised the LLM timeout to 300s)."""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    assert _stage_to_percent("design_storyboard") == 20
    assert _stage_to_percent("load_storyboard") == 20
    assert _stage_to_percent("storyboard_ready") == 20
    assert _stage_to_percent("decompose_shots") == 25
    assert _stage_to_percent("load_shot_descriptions") == 25
    assert _stage_to_percent("shot_descriptions_ready") == 25
    # neighbors unchanged
    assert _stage_to_percent("character_portraits_start") == 15
    assert _stage_to_percent("camera_tree_ready") == 30


def test_stage_to_percent_storyboard_rescales_to_80_decompose_to_81():
    """E1 rescale check: raw 20→80%, raw 25→81% via pct = 75 + int(raw*24/95).
    Gets the bar OFF 78% (the old failure number) onto clearly-progressing
    percents during the storyboard/decompose LLM stages."""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    assert 75 + int(_stage_to_percent("design_storyboard") * 24 / 95) == 80
    assert 75 + int(_stage_to_percent("decompose_shots") * 24 / 95) == 81

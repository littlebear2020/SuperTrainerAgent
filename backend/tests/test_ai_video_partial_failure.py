"""HR-T1 gateway 侧：ai_video 部分失败→静态占位（修黑帧/失同步）。"""

from unittest.mock import MagicMock, patch

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _make_storyboard(n=3):
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"第{i}句", duration=5.0, image_prompt=f"prompt{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_partial_failure_produces_placeholder_not_none(tmp_path):
    """帧1 provider 失败→生成占位 mp4，visual_url 仍 None 但 local_visual_path 非空。"""
    sb = _make_storyboard(3)

    async def fake_gen(prompt, *, duration, image_urls=None, ratio=None, **kw):  # noqa: ANN001,ANN003  # **kw：T3 起调用方还传 role=
        if "prompt1" in prompt:
            raise RuntimeError("hailuo 500")
        return MagicMock(url=f"http://video/{prompt}")

    provider = MagicMock()
    provider.generate_video = fake_gen
    provider.max_video_duration = 8.0  # P0-2: MagicMock auto-attr 会让 min() TypeError，显式声明
    src = AIVideoVisualSource(provider, concurrency=2)

    async def fake_placeholder(frame, task_dir, **kwargs):
        visuals_dir = task_dir / "visuals"
        visuals_dir.mkdir(parents=True, exist_ok=True)
        rel = f"visuals/placeholder_{frame.index:04d}.mp4"
        (task_dir / rel).write_bytes(b"fake-mp4")
        return rel

    with patch(
        "app.gateway.video_gen.visual_sources.ai_video._make_placeholder_clip",
        side_effect=fake_placeholder,
    ):
        await src.render(sb, task_dir=tmp_path)

    f0, f1, f2 = sb.frames
    assert f0.visual_url == "http://video/prompt0"
    assert f2.visual_url == "http://video/prompt2"
    # 帧1 失败→占位：local_visual_path 非空，visual_type=video，visual_url=None
    assert f1.visual_url is None
    assert f1.local_visual_path == "visuals/placeholder_0001.mp4"
    assert f1.visual_type == "video"


@pytest.mark.asyncio
async def test_all_failure_still_raises(tmp_path):
    """全部失败仍抛 RuntimeError（不静默成全占位）。"""
    sb = _make_storyboard(2)
    provider = MagicMock()
    provider.generate_video = MagicMock(side_effect=RuntimeError("down"))
    provider.max_video_duration = 8.0  # P0-2: MagicMock auto-attr 会让 min() TypeError，显式声明
    src = AIVideoVisualSource(provider, concurrency=2)

    async def fake_placeholder(frame, task_dir, **kwargs):
        # 占位也应失败（模拟 _make_solid_color_video 也挂）→ 全部失败仍抛
        raise RuntimeError("placeholder also failed")

    with patch(
        "app.gateway.video_gen.visual_sources.ai_video._make_placeholder_clip",
        side_effect=fake_placeholder,
    ):
        # Task 9/B2：判据由「全部失败」改为覆盖率，报错文案随之变化（意图不变：全失败必抛）
        with pytest.raises(RuntimeError, match="coverage too low"):
            await src.render(sb, task_dir=tmp_path)

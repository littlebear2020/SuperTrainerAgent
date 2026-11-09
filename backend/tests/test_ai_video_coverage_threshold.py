"""Task 9 (B2): ai_video 覆盖率门槛——审计修法无效，须换判据。

审计说「只有全失败才中止」，但**真相更糟**：那个守卫在**生产路径上几乎永不触发**。

    :182   frame.local_visual_path = rel      # 占位路径也写了 local_visual_path
    :202   failed = [f for f in target if not f.visual_url and not f.local_visual_path]
    :203   if failed and len(failed) == len(target): raise

凡走过占位的帧都被 `failed` 判据排除 → 全失败场景下 `failed` 为空 → raise 不触发。

**措辞订正**：该守卫**并非严格不可达**，仍有两条触发路径——``task_dir is None``
（占位块整段跳过）或占位本身也抛异常（只 logger.exception，不写
local_visual_path）。但正常生产路径（task_dir 已设 + 占位成功）下恒不触发。

结论：**照审计去调阈值毫无效果，必须换判据**——改为按「占位帧数 / 总帧数」计覆盖
率，对齐 ai_image.py 已有的 MIN_COVERAGE = 0.6 模式。
"""

from unittest.mock import MagicMock, patch

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _make_storyboard(n):
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"第{i}句", duration=5.0, image_prompt=f"prompt{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


def _provider(fail_predicate):
    async def fake_gen(prompt, *, duration, image_urls=None, ratio=None, **kw):
        if fail_predicate(prompt):
            raise RuntimeError("provider 500")
        return MagicMock(url=f"http://video/{prompt}")

    p = MagicMock()
    p.generate_video = fake_gen
    p.max_video_duration = 8.0
    return p


async def _fake_placeholder(frame, task_dir, **kwargs):
    visuals = task_dir / "visuals"
    visuals.mkdir(parents=True, exist_ok=True)
    rel = f"visuals/placeholder_{frame.index:04d}.mp4"
    (task_dir / rel).write_bytes(b"fake-mp4")
    return rel


async def _render(sb, provider, task_dir):
    src = AIVideoVisualSource(provider, concurrency=2)
    with patch(
        "app.gateway.video_gen.visual_sources.ai_video._make_placeholder_clip",
        side_effect=_fake_placeholder,
    ):
        await src.render(sb, task_dir=task_dir)


@pytest.mark.asyncio
async def test_nine_of_ten_placeholder_raises(tmp_path):
    """10 帧 9 帧占位 → 必须抛错（当前代码此用例必绿 = 证明守卫失效）。"""
    sb = _make_storyboard(10)
    # 只有 frame 0 成功，其余 9 帧走占位
    provider = _provider(lambda prompt: "prompt0" not in prompt)

    with pytest.raises(RuntimeError, match="coverage|覆盖"):
        await _render(sb, provider, tmp_path)


@pytest.mark.asyncio
async def test_all_frames_placeholder_raises(tmp_path):
    """全帧占位 → 抛错（修前 failed 为空 → 静默交付一片占位）。"""
    sb = _make_storyboard(5)
    provider = _provider(lambda prompt: True)

    with pytest.raises(RuntimeError):
        await _render(sb, provider, tmp_path)


@pytest.mark.asyncio
async def test_coverage_above_threshold_does_not_raise(tmp_path):
    """10 帧仅 2 帧占位（覆盖率 0.8 ≥ 0.6）→ 不抛，占位兜底交付。"""
    sb = _make_storyboard(10)
    provider = _provider(lambda prompt: prompt.endswith(("prompt8", "prompt9")))

    await _render(sb, provider, tmp_path)

    placeholders = [f for f in sb.frames if not f.visual_url and f.local_visual_path]
    assert len(placeholders) == 2
    assert sum(1 for f in sb.frames if f.visual_url) == 8


@pytest.mark.asyncio
async def test_all_success_does_not_raise(tmp_path):
    """全成功 → 不抛（不得回归）。"""
    sb = _make_storyboard(4)
    provider = _provider(lambda prompt: False)

    await _render(sb, provider, tmp_path)
    assert all(f.visual_url for f in sb.frames)


@pytest.mark.asyncio
async def test_threshold_reuses_ai_image_constant():
    """阈值须与 ai_image 的 MIN_COVERAGE 复用同一常量，避免两源不一致。"""
    from app.gateway.video_gen.visual_sources import ai_image, ai_video

    assert hasattr(ai_video, "MIN_COVERAGE"), "ai_video 未定义 MIN_COVERAGE"
    assert hasattr(ai_image, "MIN_COVERAGE"), "ai_image 的 MIN_COVERAGE 须提到模块级"

    # ai_video 故意低于 ai_image：2026-07-13 cb38966ff 决策「半数占位不硬失败，
    # 靠 sidecar 告警保 A/V 同步」；ai_image 无占位机制故可更严。但 ai_video 仍须
    # 有下限，否则 9/10 失败会静默交付一片灰占位。
    assert 0 < ai_video.MIN_COVERAGE <= 0.5, f"ai_video 阈值 {ai_video.MIN_COVERAGE} 会与 07-13「50% 占位仍交付」决策冲突"
    assert ai_video.MIN_COVERAGE <= ai_image.MIN_COVERAGE

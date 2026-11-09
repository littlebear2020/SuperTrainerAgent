"""P0-4: 失败帧三层降级——gateway 重试1次 → 复用上一帧冻结 → worker 黑场安全网。

本文件测 gateway 层（ai_image.py）：重试 + 复用上一帧。
worker 层（_collect_visual_assets 不丢帧）单独测。
"""

from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.providers.throttle import RateLimitError
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource


def _sb(n=3):
    cfg = StoryboardConfig(video_type="ai_image", width=720, height=1280)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", duration=5.0, image_prompt=f"p{i}") for i in range(n)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.mark.asyncio
async def test_failed_frame_retries_once_then_stays_none(tmp_path):
    """帧1 首次失败 -> 重试1次仍失败 -> visual_url 留 None（BUG 1-B：不再复用上帧图），
    由 worker layer-3 黑场占位处理（黑场 + 本帧旁白）。"""
    calls = {"frame1": 0}

    async def fake_gen(prompt, *, size=None, **kw):
        if "p1" in prompt:
            calls["frame1"] += 1
            raise RateLimitError("rate limit")
        return MagicMock(url=f"http://img/{prompt}")

    sb = _sb(3)
    src = AIImageVisualSource(MagicMock(generate_image=fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path)

    assert calls["frame1"] >= 2, f"应重试1次（≥2次调用），实际 {calls['frame1']}"
    assert sb.frames[1].visual_url is None, "BUG 1-B：失败帧不再复用上帧图，留 None 交 worker 黑场"
    assert sb.frames[1].visual_type != "image_reuse", "不再产生 image_reuse 标记"


@pytest.mark.asyncio
async def test_no_reuse_when_no_preceding_success(tmp_path):
    """帧0 失败（无前序成功帧）→ 不复用（留 None，交给 worker 安全网）。"""

    async def fake_gen(prompt, *, size=None, **kw):
        if "p0" in prompt:
            raise RuntimeError("fail")
        return MagicMock(url=f"http://img/{prompt}")

    sb = _sb(3)
    src = AIImageVisualSource(MagicMock(generate_image=fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path)
    assert sb.frames[0].visual_url is None, "帧0 无前序成功帧，不复用"


@pytest.mark.asyncio
async def test_coverage_guard_still_fires_on_mass_failure(tmp_path):
    """>40% 帧失败（原始成功率<60%）→ 仍抛 coverage guard，不静默产出。"""

    async def fake_gen(prompt, *, size=None, **kw):
        if "p0" in prompt or "p1" in prompt:
            raise RuntimeError("fail")
        return MagicMock(url=f"http://img/{prompt}")

    sb = _sb(3)  # 2/3 失败 = 33% 成功率 < 60%
    src = AIImageVisualSource(MagicMock(generate_image=fake_gen), concurrency=1)
    with pytest.raises(RuntimeError, match="coverage too low"):
        await src.render(sb, task_dir=tmp_path)


@pytest.mark.asyncio
async def test_failed_frame_does_not_trigger_on_frame_done(tmp_path):
    """BUG 1-B：失败帧（visual_url=None）不触发 on_frame_done--与纯 None 帧行为一致，
    不改变进度/完成语义。"""
    done_indices: list[int] = []

    async def on_done(idx):
        done_indices.append(idx)

    async def fake_gen(prompt, *, size=None, **kw):
        if "p1" in prompt:
            raise RuntimeError("fail")
        return MagicMock(url=f"http://img/{prompt}")

    sb = _sb(3)
    src = AIImageVisualSource(MagicMock(generate_image=fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path, on_frame_done=on_done)
    assert 1 not in done_indices, "BUG 1-B：失败帧不再复用->不触发 on_frame_done"
    assert 0 in done_indices, "成功帧仍触发 on_frame_done"


@pytest.mark.asyncio
async def test_reference_image_paths_passed_to_provider(tmp_path):
    """Task 12: 项目绑定角色 → ai_image 生成时传 reference_image_paths 给 provider。

    provider(base/gpt_image2)已支持 reference_image_paths(角色一致性 i2i)。
    ai_image render 新增参数透传，使项目三视图 front 注入每帧生图。
    """
    refs_seen: list = []

    async def fake_gen(prompt, *, size=None, reference_image_paths=None, **kw):
        refs_seen.append(reference_image_paths)
        return MagicMock(url=f"http://img/{prompt}")

    sb = _sb(2)
    src = AIImageVisualSource(MagicMock(generate_image=fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path, reference_image_paths=["/proj/front.png"])
    # 每帧都应含角色 front（Task 12 角色 i2i 语义不变）
    assert all(r and "/proj/front.png" in r for r in refs_seen), f"每帧应含角色 front，实际 {refs_seen}"
    # T-I0-④（2026-08-17）：跨帧风格锚默认关--第二帧不再额外携带上一帧成功图
    # （env=1 开回行为由 test_ti0_image_prompt_bleeding_stop.py 覆盖）。
    assert all("http://img/" not in (r or []) for r in refs_seen), f"默认不应注入风格锚: {refs_seen}"

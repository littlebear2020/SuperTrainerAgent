"""Task 3: ai_video 生成后实测时长并回写。"""

from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_video as mod
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _sb(duration: float = 8.9) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="n", duration=duration, image_prompt="p")])


def _provider(fake_gen) -> MagicMock:  # noqa: ANN001
    """seedance-ark 栅格属性齐备的 provider mock（整数秒 [4,15]）。

    ⚠️ 这是**裸** provider，直接声明能力属性 → 绕过生产的 Throttle/Fallback 包裹层。
    2026-08-03 BUG-B 正是因此漏检：生产链上这两个属性没被透传，getattr 恒取兜底值，
    栅格对齐全程失效，而本文件 5 个测试照样全绿。
    本文件保留裸 mock 是**刻意**的——它测的是 fill() 读到能力后的行为；
    「能力能否穿透包裹链」由 tests/test_provider_capability_passthrough.py 覆盖。
    改动能力属性时两个文件都要看。
    """
    return MagicMock(
        generate_video=fake_gen,
        max_video_duration=15.0,
        min_video_duration=4.0,
        integer_seconds_only=True,
    )


@pytest.mark.asyncio
async def test_requests_ceiling_integer_duration(tmp_path, monkeypatch) -> None:
    """8.9s 旁白 → 请求 12s：E9 超发（2026-08-25）×1.25=11.125 后再向上取整。"""
    seen: list[float] = []

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        seen.append(duration)
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    src = AIVideoVisualSource(_provider(fake_gen), concurrency=1)
    await src.render(_sb(8.9), task_dir=tmp_path)
    assert seen == [12.0], f"应 ×1.25 后向上取整到 12s，实际 {seen}"


@pytest.mark.asyncio
async def test_probed_duration_written_back(tmp_path, monkeypatch) -> None:
    """模型只给 5s → actual_visual_duration=5.0，duration 保持 8.9（音频权威不动）。"""

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        (task_dir / "visuals").mkdir(parents=True, exist_ok=True)
        (task_dir / "visuals" / "v0.mp4").write_bytes(b"x" * 2048)
        return "visuals/v0.mp4"

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    monkeypatch.setattr(mod, "_probe_video_seconds", lambda p: 5.0)
    sb = _sb(8.9)
    src = AIVideoVisualSource(_provider(fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path)
    assert sb.frames[0].actual_visual_duration == 5.0
    assert sb.frames[0].duration == 8.9, "音频权威时间轴不得被视觉实测改写"


@pytest.mark.asyncio
async def test_probe_failure_leaves_field_none(tmp_path, monkeypatch) -> None:
    """探测失败不猜——留 None，由 worker 的保守定格兜底。"""

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        (task_dir / "visuals").mkdir(parents=True, exist_ok=True)
        (task_dir / "visuals" / "v0.mp4").write_bytes(b"x" * 2048)
        return "visuals/v0.mp4"

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    monkeypatch.setattr(mod, "_probe_video_seconds", lambda p: 0.0)
    sb = _sb(8.9)
    src = AIVideoVisualSource(_provider(fake_gen), concurrency=1)
    await src.render(sb, task_dir=tmp_path)
    assert sb.frames[0].actual_visual_duration is None


@pytest.mark.asyncio
async def test_short_narration_lifted_to_provider_floor(tmp_path, monkeypatch) -> None:
    """2.5s 旁白 → 请求 4s（Ark 下限），而非 2.5s（会被 Ark 端静默钳位）。"""
    seen: list[float] = []

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        seen.append(duration)
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    src = AIVideoVisualSource(_provider(fake_gen), concurrency=1)
    await src.render(_sb(2.5), task_dir=tmp_path)
    assert seen == [4.0], f"应抬到 Ark 下限 4s，实际 {seen}"


@pytest.mark.asyncio
async def test_provider_without_grid_attrs_keeps_legacy_behavior(tmp_path, monkeypatch) -> None:
    """无栅格属性的 provider（qihu 等）→ 保持旧的 min(duration, 8.0) 浮点行为。

    duration_safety_factor=1.0 显式锁旧契约（E9 超发与栅格行为正交）；
    默认系数下的行为见下方新用例。"""
    seen: list[float] = []

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        seen.append(duration)
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    provider = MagicMock(generate_video=fake_gen)
    del provider.max_video_duration
    del provider.min_video_duration
    del provider.integer_seconds_only
    src = AIVideoVisualSource(provider, concurrency=1, duration_safety_factor=1.0)
    await src.render(_sb(6.4), task_dir=tmp_path)
    assert seen == [6.4], f"非整数秒 provider 应保留小数，实际 {seen}"


@pytest.mark.asyncio
async def test_provider_without_grid_attrs_applies_safety_factor(tmp_path, monkeypatch) -> None:
    """E9 超发（2026-08-25）：无栅格 provider 默认 ×1.25——6.4×1.25=8.0 封顶默认
    cap 8.0（float 保留，无 ceil）。"""
    seen: list[float] = []

    async def fake_gen(prompt, *, duration=5.0, **kw):  # noqa: ANN001,ANN003
        seen.append(duration)
        return MagicMock(url="http://v/1")

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)
    provider = MagicMock(generate_video=fake_gen)
    del provider.max_video_duration
    del provider.min_video_duration
    del provider.integer_seconds_only
    src = AIVideoVisualSource(provider, concurrency=1)
    await src.render(_sb(6.4), task_dir=tmp_path)
    assert seen == [8.0], f"应 ×1.25 后封顶 8.0，实际 {seen}"


def test_seedance_ark_declares_grid_attrs() -> None:
    """Task 13 的能力表会锁死这两个类属性，Task 3 必须先把它们公开出来。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider as P

    assert P.min_video_duration == 4.0
    assert P.integer_seconds_only is True

"""Task 6: (a) fallback 不支持多图时至少传首图（image_url）并打 degraded 标记；
(b) refs 被忽略/降级时 sidecar 写可见标记；(c) coverage 全帧口径 + 全成功时清理。"""

from __future__ import annotations

import json

import pytest

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.throttle import ProviderUnavailableError


class _BoomPrimary:
    supports_image_urls = True
    max_video_duration = 15.0

    async def generate_video(self, prompt, **kwargs):
        raise ProviderUnavailableError("ark down")


class _CapturingFallback:
    # T3（2026-08-18）注：修前注释「qihu 只吃单数 image_url」已过时——四个在册
    # video provider 均已声明 supports_image_urls=True 并消费复数；本桩模拟的是
    # 「未来未声明该能力的降级端」，走首图降级分支（QP1-3）。
    supports_image_urls = False
    max_video_duration = 8.0

    def __init__(self):
        self.kwargs = None

    async def generate_video(self, prompt, **kwargs):
        self.kwargs = kwargs
        return VideoRef(url="http://x/v.mp4", duration=5.0, source="qihu")


@pytest.mark.asyncio
async def test_fallback_passes_first_ref_and_marks_degraded():
    fb = _CapturingFallback()
    p = FallbackAIVideoProvider(_BoomPrimary(), fb)
    ref = await p.generate_video("p", duration=5.0, image_urls=["front.png", "side.png", "back.png"])
    assert ref.url == "http://x/v.mp4"
    assert fb.kwargs.get("image_url") == "front.png"  # 至少保首图（不存在路径原样透传）
    assert "image_urls" not in fb.kwargs  # 多图键被剥（防 **kwargs 静默吞）
    assert p.degraded_without_refs is True


@pytest.mark.asyncio
async def test_fallback_normalizes_real_local_ref_to_data_uri(tmp_path):
    """终审 Important-1: image_urls 是角色三视图本地绝对路径，远端 360 API 抓不到——
    降级映射 image_url 前必须归一成 data-URI（真实存在的文件）。"""
    img = tmp_path / "front.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    fb = _CapturingFallback()
    p = FallbackAIVideoProvider(_BoomPrimary(), fb)
    await p.generate_video("p", duration=5.0, image_urls=[str(img)])
    image_url = fb.kwargs.get("image_url", "")
    assert image_url.startswith("data:image/"), f"本地路径须转 data-URI，实际: {image_url!r}"
    assert str(img) not in image_url


@pytest.mark.asyncio
async def test_sidecar_full_storyboard_scope_and_cleanup(tmp_path, monkeypatch):
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources import ai_video as av
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    # 桩掉本地化下载（防真实网络/DNS 60s 超时）与占位 ffmpeg（防外部依赖）
    async def _fake_localize(url, frame, td):
        return None

    async def _fake_placeholder(frame, td, **kwargs):
        return "visuals/p.mp4"

    monkeypatch.setattr(av, "_localize_video", _fake_localize)
    monkeypatch.setattr(av, "_make_placeholder_clip", _fake_placeholder)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="a", visual_url="http://x/0.mp4"),
            StoryboardFrame(index=1, narration="b"),
        ],
    )

    class FlakyProvider:
        supports_image_urls = True

        def __init__(self):
            self.calls = 0

        async def generate_video(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")  # 首批帧1失败 → 占位 → 部分覆盖（写 sidecar）
            return VideoRef(url="http://x/1.mp4", duration=5.0, source="stub")

    src = AIVideoVisualSource(FlakyProvider())
    await src.render(sb, only_frames=[1], task_dir=tmp_path)
    cov = json.loads((tmp_path / "visual_coverage.json").read_text(encoding="utf-8"))
    assert cov["total"] == 2, "coverage 必须按全 storyboard 帧计（修前按当批 only_frames=1）"
    assert cov["succeeded"] == 1
    assert cov["placeholder"] == 1

    # 全成功且无 refs 标记 → sidecar 删除（修前永不清理，旧警告残留）
    sb.frames[1].visual_url = "http://x/1.mp4"
    sb.frames[1].local_visual_path = None
    await src.render(sb, only_frames=[1], task_dir=tmp_path)
    assert not (tmp_path / "visual_coverage.json").exists()


@pytest.mark.asyncio
async def test_refs_ignored_writes_sidecar_flag(tmp_path, monkeypatch):
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources import ai_video as av
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    async def _fake_localize(url, frame, td):
        return None

    monkeypatch.setattr(av, "_localize_video", _fake_localize)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="a")],
    )

    class NoRefsProvider:
        supports_image_urls = False

        async def generate_video(self, prompt, **kwargs):
            return VideoRef(url="http://x/0.mp4", duration=5.0, source="qihu")

    src = AIVideoVisualSource(NoRefsProvider())
    await src.render(sb, task_dir=tmp_path, reference_image_paths=["front.png"])
    cov = json.loads((tmp_path / "visual_coverage.json").read_text(encoding="utf-8"))
    assert cov["character_ref_ignored"] is True

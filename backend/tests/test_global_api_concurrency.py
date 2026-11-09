"""Task 1 (闸1): global concurrency cap for ai_image/ai_video upstream AI API calls.

Per-task frame_concurrency alone let 20-30 tasks x 3 = 60-90 concurrent calls
hammer the upstream (字节 Ark seedream/seedance-ark by default, 360 as fallback)
→ 429 rate-limiting + cascade timeouts. A process-global semaphore (mirrors
ViMax's _API_SEM) caps the TOTAL across all running ai_image/ai_video tasks.
"""

import asyncio
import base64

import pytest


@pytest.mark.asyncio
async def test_global_semaphore_is_process_singleton():
    """Two calls with same limit return the same Semaphore (process-global)."""
    from app.gateway.video_gen.visual_sources._global_concurrency import (
        get_ai_api_semaphore,
        reset_for_test,
    )

    reset_for_test()
    s1 = get_ai_api_semaphore(12)
    s2 = get_ai_api_semaphore(12)
    assert s1 is s2


@pytest.mark.asyncio
async def test_global_semaphore_caps_concurrent_calls():
    """Across two simulated tasks, total concurrent acquisitions <= limit."""
    from app.gateway.video_gen.visual_sources._global_concurrency import (
        get_ai_api_semaphore,
        reset_for_test,
    )

    reset_for_test()
    sem = get_ai_api_semaphore(4)
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def hold():
        nonlocal active, peak
        async with sem:
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1

    # 2 "tasks" each gathering 4 frames = 8 concurrent wannabes, cap 4
    await asyncio.gather(*(hold() for _ in range(8)))
    assert peak <= 4


# ---------------------------------------------------------------------------
# render() 行为层：两个 visual source 必须在真实渲染路径上经过全局信号量。
# 若回退为每实例自建 asyncio.Semaphore(self._concurrency)，spy 永不触发 → 红。
# ---------------------------------------------------------------------------


class _TrackingSemaphore:
    """包装真全局信号量，记录 __aenter__ 命中（证明 render 真的 acquire 了它）。"""

    def __init__(self, inner: asyncio.Semaphore) -> None:
        self._inner = inner
        self.entries = 0

    async def __aenter__(self):
        self.entries += 1
        return await self._inner.__aenter__()

    async def __aexit__(self, *exc):
        return await self._inner.__aexit__(*exc)


def _spy_global_semaphore(monkeypatch, module):
    """Patch the module's get_ai_api_semaphore with a tracking wrapper; return tracker."""
    from app.gateway.video_gen.visual_sources._global_concurrency import (
        get_ai_api_semaphore,
        reset_for_test,
    )

    reset_for_test()
    tracker = _TrackingSemaphore(get_ai_api_semaphore(4))
    monkeypatch.setattr(module, "get_ai_api_semaphore", lambda limit: tracker)
    return tracker


class _FakeImageProvider:
    """BUG-B 纪律：手写 fake（只声明 generate_image），data-URI 产物免网络。"""

    async def generate_image(self, prompt: str, **kw):
        from app.gateway.video_gen.providers.base import ImageRef

        return ImageRef(url="data:image/png;base64," + base64.b64encode(b"y" * 700).decode(), width=10, height=10, source="t")


class _FakeVideoProvider:
    """BUG-B 纪律：手写 fake，声明 AIVideoVisualSource 实读的能力属性。"""

    max_video_duration = 15.0
    min_video_duration = 4.0
    integer_seconds_only = True
    supports_image_urls = False

    async def generate_video(self, prompt: str, **kw):
        from app.gateway.video_gen.providers.base import VideoRef

        return VideoRef(url="http://v/1.mp4", duration=5.0, source="t")


@pytest.mark.asyncio
async def test_ai_image_render_acquires_global_semaphore(monkeypatch, tmp_path):
    """AIImageVisualSource.render acquires the global semaphore, not a per-instance one."""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources import ai_image

    tracker = _spy_global_semaphore(monkeypatch, ai_image)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="n", image_prompt="p")],
    )
    await ai_image.AIImageVisualSource(_FakeImageProvider()).render(sb, task_dir=tmp_path)
    assert tracker.entries >= 1, "render 未 acquire 全局信号量（可能回退为每实例 semaphore）"


@pytest.mark.asyncio
async def test_ai_video_render_acquires_global_semaphore(monkeypatch, tmp_path):
    """AIVideoVisualSource.render acquires the global semaphore, not a per-instance one."""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources import ai_video

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(ai_video, "_localize_video", fake_localize)
    tracker = _spy_global_semaphore(monkeypatch, ai_video)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=720, height=1280),
        frames=[StoryboardFrame(index=0, narration="n", duration=5.0, image_prompt="p")],
    )
    await ai_video.AIVideoVisualSource(_FakeVideoProvider(), concurrency=1).render(sb, task_dir=tmp_path)
    assert tracker.entries >= 1, "render 未 acquire 全局信号量（可能回退为每实例 semaphore）"

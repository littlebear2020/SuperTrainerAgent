"""T2（2026-08-14 计划）：视觉 gather「20 分钟无进展」看门狗 + TTS gather elapsed 兜底。

固定时长预算会误杀 >50 帧串行 + Tier E 续写的健康长任务（单帧合法最坏 ≈13.5min），
正确信号是**活性而非耗时**：timeout 内没有任何一帧 fill 结束 → 上游 hang/限流 →
取消全部未完成 fill 并抛可读 RuntimeError。VisualBudgetExceeded 必须穿透
（看门狗只包取消与超时路径，不改变子协程异常类型）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

# ---------------------------------------------------------------------------
# helper 单元测试（_progress_watchdog）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_raises_when_no_frame_completes():
    from app.gateway.video_gen.visual_sources._progress_watchdog import gather_with_no_progress_watchdog

    started: list = []

    async def fill(item):
        started.append(item)
        # 有界 hang：30s 远长于窗口 0.1s。用 sleep 而非 Event 永等——
        # 变异（去异常重抛）时测试会 30s 后干净红，而不是整跑挂起。
        await asyncio.sleep(30)

    with pytest.raises(RuntimeError, match="无帧完成"):
        await gather_with_no_progress_watchdog(
            fill,
            [1, 2, 3],
            timeout=0.1,
            message="生图阶段 20 分钟无帧完成，可能上游 hang 或限流",
        )
    assert len(started) == 3  # 全部帧都已启动（后被取消）


@pytest.mark.asyncio
async def test_watchdog_not_triggered_when_frames_complete_slowly():
    from app.gateway.video_gen.visual_sources._progress_watchdog import gather_with_no_progress_watchdog

    results: list = []

    async def fill(item):
        await asyncio.sleep(0.1)
        results.append(item)

    # timeout 0.5s，但每 0.1s 就有一帧完成 → 窗口不断重置，看门狗不触发（防误杀核心断言）
    await gather_with_no_progress_watchdog(
        fill,
        [1, 2, 3],
        timeout=0.5,
        message="生图阶段 20 分钟无帧完成，可能上游 hang 或限流",
    )
    assert results == [1, 2, 3]


@pytest.mark.asyncio
async def test_watchdog_propagates_budget_exception_and_cancels_siblings():
    """VisualBudgetExceeded 必须穿透（不被看门狗/取消潮吞掉），兄弟帧被取消。"""
    from app.gateway.video_gen.visual_sources._progress_watchdog import gather_with_no_progress_watchdog
    from app.gateway.video_gen.visual_sources.budget import VisualBudgetExceeded

    cancelled: list = []

    async def fill(item):
        if item == 1:
            raise VisualBudgetExceeded("cap hit")
        try:
            # 有界 hang：看门狗窗口 60s > 30s——变异（去异常重抛）时兄弟帧 30s 后
            # 自然结束 → 函数正常返回 → pytest.raises 干净红，而不是整跑挂起。
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(item)
            raise

    with pytest.raises(VisualBudgetExceeded, match="cap hit"):
        await gather_with_no_progress_watchdog(
            fill,
            [1, 2, 3],
            timeout=60,
            message="生图阶段 20 分钟无帧完成，可能上游 hang 或限流",
        )
    assert cancelled == [2, 3]  # 兄弟帧被取消，不留孤儿


@pytest.mark.asyncio
async def test_watchdog_empty_target_returns_immediately():
    from app.gateway.video_gen.visual_sources._progress_watchdog import gather_with_no_progress_watchdog

    await gather_with_no_progress_watchdog(lambda x: None, [], timeout=0.1, message="m")


# ---------------------------------------------------------------------------
# ai_image / ai_video render 层：全 hang → 看门狗触发；慢速推进 → 不误杀
# ---------------------------------------------------------------------------


def _make_storyboard(video_type: str, n: int = 3) -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=1080, height=1920)
    return Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=i, narration=f"第{i}句", image_prompt=f"画面{i}", duration=5.0) for i in range(n)],
    )


@pytest.mark.asyncio
async def test_ai_image_render_raises_when_all_frames_hang(monkeypatch):
    from app.gateway.video_gen.visual_sources import ai_image as mod

    monkeypatch.setattr(mod, "_NO_PROGRESS_TIMEOUT", 0.1)

    class _HangingProvider:
        async def generate_image(self, prompt, **kwargs):
            await asyncio.sleep(30)  # hang 远长于看门狗窗口

    src = mod.AIImageVisualSource(_HangingProvider())
    with pytest.raises(RuntimeError, match="无帧完成"):
        await src.render(_make_storyboard("ai_image"))


@pytest.mark.asyncio
async def test_ai_video_render_raises_when_all_frames_hang(monkeypatch):
    from app.gateway.video_gen.visual_sources import ai_video as mod

    monkeypatch.setattr(mod, "_NO_PROGRESS_TIMEOUT", 0.1)

    class _HangingProvider:
        async def generate_video(self, prompt, **kwargs):
            await asyncio.sleep(30)

    src = mod.AIVideoVisualSource(_HangingProvider())
    with pytest.raises(RuntimeError, match="无帧完成"):
        await src.render(_make_storyboard("ai_video"), task_dir=None)


@pytest.mark.asyncio
async def test_ai_image_render_not_killed_when_frames_trickle_in(monkeypatch):
    """慢速但持续推进（每帧 0.1s < 窗口 0.5s）→ 看门狗不误杀（防误杀回归）。"""
    from app.gateway.video_gen.visual_sources import ai_image as mod

    monkeypatch.setattr(mod, "_NO_PROGRESS_TIMEOUT", 0.5)

    class _SlowProvider:
        async def generate_image(self, prompt, **kwargs):
            await asyncio.sleep(0.1)
            return ImageRef(url="https://cdn/x.png", width=1080, height=1920, source="qihu")

    src = mod.AIImageVisualSource(_SlowProvider())
    sb = _make_storyboard("ai_image")
    await src.render(sb)
    assert all(f.visual_url for f in sb.frames)


# ---------------------------------------------------------------------------
# TTS gather elapsed 兜底（service._synthesize_audio_locked）
# ---------------------------------------------------------------------------


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_tts_gather_elapsed_budget_raises_and_cancels(tmp_path, monkeypatch):
    """TTS 全帧 hang → 预算 max(0, n×0.2)=0.4s 内取消并抛「TTS 阶段超时」（修前挂到 hang 结束）。"""
    from app.gateway.video_gen import service as svc_mod

    monkeypatch.setattr(svc_mod, "TTS_GATHER_TIMEOUT_FLOOR", 0)
    monkeypatch.setattr(svc_mod, "TTS_GATHER_TIMEOUT_PER_FRAME", 0.2)

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句", duration=5.0),
            StoryboardFrame(index=1, narration="第二句", duration=5.0),
        ],
    )

    async def _hang_then_boom(*a, **kw):
        # 修前：gather 无预算 → 5s 后此异常冒出（"boom"≠"TTS 阶段超时" → 红）
        await asyncio.sleep(5)
        raise RuntimeError("boom")

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_hang_then_boom):
        with pytest.raises(RuntimeError, match="TTS 阶段超时"):
            await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural")


@pytest.mark.asyncio
async def test_tts_gather_budget_scales_with_frames():
    """预算 = max(1800, n×60)：长任务有足额合法时间（T4 已关闭，长脚本仍允许 100+ 帧）。"""
    from app.gateway.video_gen.service import TTS_GATHER_TIMEOUT_FLOOR, TTS_GATHER_TIMEOUT_PER_FRAME, _tts_gather_timeout

    assert TTS_GATHER_TIMEOUT_FLOOR == 1800
    assert TTS_GATHER_TIMEOUT_PER_FRAME == 60
    assert _tts_gather_timeout(1) == 1800
    assert _tts_gather_timeout(20) == 1800
    assert _tts_gather_timeout(140) == 140 * 60  # 140 帧 = 8400s > 1800
    assert _tts_gather_timeout(0) == 1800


@pytest.mark.asyncio
async def test_tts_gather_child_timeout_not_mislabeled_as_budget(tmp_path, monkeypatch):
    """审查 Minor 1：子任务自身的 TimeoutError（降级链尽头）须原样传播并取消兄弟帧，
    不得误标成「TTS 阶段超时（预算 1800s）」（wait_for 无法区分两种超时——已换 asyncio.wait）。"""
    from app.gateway.video_gen import service as svc_mod

    monkeypatch.setattr(svc_mod, "TTS_GATHER_TIMEOUT_FLOOR", 1800)
    monkeypatch.setattr(svc_mod, "TTS_GATHER_TIMEOUT_PER_FRAME", 60)

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句", duration=5.0),
            StoryboardFrame(index=1, narration="第二句", duration=5.0),
        ],
    )

    async def _timeout_synth(*a, **kw):
        raise TimeoutError()  # 单帧降级链尽头超时（非整批预算）

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_timeout_synth):
        with pytest.raises(TimeoutError):
            await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural")


@pytest.mark.asyncio
async def test_tts_gather_outer_cancel_cleans_up_children(tmp_path):
    """审查 Minor 1 回归（二审）：外层取消 _synthesize_audio（= T6 reaper 谓词 C 杀任务
    的路径）时必须取消全部 in-flight synth_one——修前孤儿继续烧 doubao/360 TTS 配额
    （每帧最坏 120s×3 重试+降级链），并在锁释放后仍写 CAS 文件（与重试代际交错）。"""
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句", duration=5.0),
            StoryboardFrame(index=1, narration="第二句", duration=5.0),
        ],
    )

    entered: list = []
    cancelled_children: list = []
    started = asyncio.Event()
    release = asyncio.Event()
    counter = {"n": 0}

    async def _slow_synth(text, **kwargs):
        counter["n"] += 1
        out = tmp_path / "audio" / f"slow_{counter['n']}.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        entered.append(text)
        if len(entered) == 2:
            started.set()  # 两个子任务都已进入上游调用
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled_children.append(text)
            raise
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_slow_synth):
        main = asyncio.ensure_future(svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            main.cancel()
            with pytest.raises(asyncio.CancelledError):
                await main
            assert len(cancelled_children) == 2, f"外层取消必须取消全部子任务（修前孤儿存活：{cancelled_children}）"
        finally:
            release.set()  # 收敛可能的孤儿，避免测试泄漏挂起任务
            await asyncio.sleep(0.05)

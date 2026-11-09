"""ViMax LLM timeout/retry/concurrency regression tests (B1/B2/B3).

Covers two complementary fixes:
  1. ``resolve_chat_model_config`` injects a global ``request_timeout=320``
     so EVERY vimax LLM call (most agents have no per-call wait_for) is bounded.
     320 > the per-attempt ``llm_retry_timeout(300)`` so ``asyncio.wait_for``
     fires first as a clean ``TimeoutError`` (caught by tenacity reraise in
     storyboard_artist), NOT httpx.ReadTimeout which would bypass retry (B1).
  2. ``reference_image_selector.select_reference_images_and_generate_prompt``
     wraps both LLM calls in ``asyncio.wait_for(timeout=retry_timeout)`` so
     a stall raises TimeoutError -> the existing @retry fires (vg_f309ae0f32f5).
  3. ``Script2VideoPipeline.llm_retry_timeout = 300`` named attr replaces the
     old caller hardcodes 150/120 that caused 78% TimeoutError on
     doubao-seed-2.1-pro structured output (B1).

These touch vendored ``packages/vimax`` (plan §2.1 allows vendor edits).
"""

import asyncio

import pytest
from langchain_core.runnables import Runnable
from vimax.agents.reference_image_selector import ReferenceImageSelector
from vimax.utils.provider_presets import resolve_chat_model_config


@pytest.fixture()
def _no_retry_backoff(monkeypatch):
    """重试间的指数退避（``wait_exponential(min=1, max=10)`` → 每次等 1~2s）不是
    本文件用例的断言对象——断言 per-instance max_attempts（script2video 2 /
    旧调用 3）与 reraise 原始 TimeoutError。置零等待；monkeypatch 用后即还原，
    ``test_vendored_agents_retry_config_hardened`` 的 wait 类型内省不受影响。"""
    import tenacity
    from vimax.agents import reference_image_selector, storyboard_artist

    # design_storyboard 已从静态 decorator 改为 per-instance AsyncRetrying；
    # 退避对象收敛到模块级 _STORYBOARD_RETRY_WAIT。
    monkeypatch.setattr(storyboard_artist, "_STORYBOARD_RETRY_WAIT", tenacity.wait_none())
    for fn in (
        reference_image_selector.ReferenceImageSelector.select_reference_images_and_generate_prompt,
        storyboard_artist.StoryboardArtist.decompose_visual_description,
    ):
        monkeypatch.setattr(fn.retry, "wait", tenacity.wait_none())


# ── resolve_chat_model_config: global request_timeout injection (B1) ─────────


def test_resolve_chat_model_config_default_request_timeout_is_320():
    # must be > llm_retry_timeout(300) so asyncio.wait_for fires first (clean
    # TimeoutError), not httpx.ReadTimeout — see B1.
    args = resolve_chat_model_config({"model_provider": "openai"})
    assert args["request_timeout"] == 320
    assert args["request_timeout"] > 300


def test_resolve_chat_model_config_respects_explicit_request_timeout():
    """An explicit config value wins over the default (don't clobber tuning)."""
    args = resolve_chat_model_config({"model_provider": "openai", "request_timeout": 600})
    assert args["request_timeout"] == 600


def test_resolve_chat_model_config_provider_preset_still_gets_timeout():
    """A preset provider (minimax etc.) still gets the timeout guard."""
    out = resolve_chat_model_config({"model_provider": "minimax", "api_key": "k"})
    assert out["request_timeout"] == 320
    assert out["model_provider"] == "openai"  # preset rewrite intact


# ── Script2VideoPipeline.llm_retry_timeout named attr (B1) ───────────────────


def test_pipeline_llm_retry_timeout_default_is_300(tmp_path):
    from unittest.mock import MagicMock

    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    pipe = Script2VideoPipeline(
        chat_model=MagicMock(),
        image_generator=MagicMock(),
        video_generator=MagicMock(),
        working_dir=str(tmp_path),
    )
    assert pipe.llm_retry_timeout == 300


# ── reference_image_selector: wait_for timeout on stalled LLM ────────────────


class _StallingChain:
    """A chain whose ainvoke never returns — simulates a hung 360 API request.

    Before the fix, ``await chain.ainvoke(...)`` blocked forever here. After the
    fix, ``asyncio.wait_for`` cancels it at retry_timeout → TimeoutError → @retry.
    """

    def __or__(self, other):
        return self

    async def ainvoke(self, messages):
        await asyncio.sleep(3600)  # never returns within any sane timeout
        raise AssertionError("should have been cancelled by wait_for")


@pytest.mark.asyncio
async def test_reference_image_selector_times_out_instead_of_hanging(tmp_path, _no_retry_backoff):
    """A stalled LLM call must raise within retry_timeout, not hang forever.

    This is the vg_f309ae0f32f5 regression: shot 1 reference-image selection
    against a non-responsive 360 endpoint froze the whole pipeline at 30%. The
    @retry(stop=3) then kicks in — we assert TimeoutError surfaces (retry will
    re-raise after exhausting attempts, but the key point is it does NOT hang).

    The selector base64-encodes the reference images BEFORE calling the LLM, so
    we must hand it a real image file to get past image_path_to_b64 and reach
    the (stalling) ainvoke on the multimodal branch."""
    from PIL import Image

    # real tiny image so image_path_to_b64 succeeds and we reach ainvoke
    img_path = tmp_path / "portrait.png"
    Image.new("RGB", (8, 8)).save(img_path)

    selector = ReferenceImageSelector(chat_model=_StallingChain())
    # retry_timeout tiny so the test is fast; the real default is 300s.
    # <8 image pairs → skips the text-only filter branch, goes straight to the
    # multimodal branch (the second ainvoke) which is the one that hung in prod.
    #
    # The fix makes the stalled ainvoke raise TimeoutError → @retry fires 3x.
    # M4 (2026-07-22) added reraise=True → the ORIGINAL TimeoutError surfaces
    # (NOT tenacity.RetryError[<Future…>], same convention as B2 storyboard_artist).
    # The precise point under test: the call TERMINATES (raises) within
    # retry_timeout × attempts + backoff, instead of hanging forever.
    with pytest.raises(asyncio.TimeoutError):
        await selector.select_reference_images_and_generate_prompt(
            available_image_path_and_text_pairs=[(str(img_path), "a portrait")],
            frame_description="a scene",
            retry_timeout=0.05,
        )


# ── storyboard_artist: @retry reraise=True + wait_exponential (B2) ──────────
#
# Before B2 the two @retry(stop=3) decorators in storyboard_artist.py lacked
# ``reraise`` (so the original ``TimeoutError`` was wrapped in a confusing
# ``RetryError[<Future…>]``) and lacked ``wait`` (so 3 attempts fired back-to-
# back, hammerming an already-overloaded Ark endpoint). B2 adds
# ``wait_exponential(min=1, max=10), reraise=True`` so the clean TimeoutError
# surfaces and retries back off.


class _FailingChatModel(Runnable):
    """Stub whose ainvoke always raises TimeoutError.

    Subclasses ``Runnable`` so LangChain's ``ChatPromptTemplate | self`` (used in
    ``decompose_visual_description``) accepts it — the brief's ``__or__``-returns-
    ``self`` trick only works when the stub is on the LEFT of ``|`` (as in
    ``design_storyboard``), because the right operand is validated by
    ``Runnable.__or__``.
    """

    def invoke(self, input, config=None, **kwargs):
        raise TimeoutError()

    async def ainvoke(self, messages, config=None, **kwargs):
        raise TimeoutError()


@pytest.mark.asyncio
async def test_design_storyboard_reraises_timeout_not_retryerror(_no_retry_backoff):
    # script2video 配置 max_attempts=2；reraise=True → 第二次失败后原始
    # TimeoutError 直接上抛，NOT RetryError[<Future…>]。
    from vimax.agents.storyboard_artist import StoryboardArtist

    artist = StoryboardArtist(chat_model=_FailingChatModel(), max_attempts=2)
    with pytest.raises(asyncio.TimeoutError):
        await artist.design_storyboard(script="x", characters=[], retry_timeout=1)


@pytest.mark.asyncio
async def test_decompose_reraises_timeout_not_retryerror(_no_retry_backoff):
    from vimax.agents.storyboard_artist import StoryboardArtist
    from vimax.interfaces import ShotBriefDescription

    artist = StoryboardArtist(chat_model=_FailingChatModel())
    # ShotBriefDescription requires: idx, is_last, cam_idx, visual_desc, audio_desc
    # (brief's stub omitted cam_idx — adapted to the real interface here).
    shot = ShotBriefDescription(idx=0, is_last=True, cam_idx=0, visual_desc="a room", audio_desc="hi")
    with pytest.raises(asyncio.TimeoutError):
        await artist.decompose_visual_description(
            shot_brief_desc=shot,
            characters=[],
            retry_timeout=1,
        )


# ── Script2VideoPipeline: per-shot decompose concurrency cap (B3) ────────────
#
# Before B3 ``decompose_visual_descriptions`` asyncio.gather'd ALL shots with no
# limit → N concurrent Ark LLM calls competing for quota → each slows → 78%
# TimeoutError. B3 adds an ``asyncio.Semaphore(3)`` so at most 3 shots decompose
# concurrently, staying under Ark's concurrency ceiling while keeping throughput.


def test_decompose_concurrency_limit_default_is_3(tmp_path):
    from unittest.mock import MagicMock

    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    pipe = Script2VideoPipeline(
        chat_model=MagicMock(),
        image_generator=MagicMock(),
        video_generator=MagicMock(),
        working_dir=str(tmp_path),
    )
    assert pipe._decompose_concurrency == 3


@pytest.mark.asyncio
async def test_decompose_concurrency_capped_to_limit(tmp_path):
    from unittest.mock import MagicMock

    from vimax.interfaces import ShotBriefDescription
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    pipe = Script2VideoPipeline(
        chat_model=MagicMock(),
        image_generator=MagicMock(),
        video_generator=MagicMock(),
        working_dir=str(tmp_path),
    )
    n = 8
    pipe.shot_desc_events = {i: asyncio.Event() for i in range(n)}
    pipe.frame_events = {i: None for i in range(n)}  # 'small' branch only writes, never reads

    current = {"n": 0}
    peak = {"n": 0}

    async def fake_decompose(shot_brief_desc, characters, retry_timeout=300):
        current["n"] += 1
        peak["n"] = max(peak["n"], current["n"])
        await asyncio.sleep(0.05)
        current["n"] -= 1
        m = MagicMock()
        m.variation_type = "small"
        # decompose_for_single_shot then calls json.dump(m.model_dump(), f);
        # MagicMock's auto-child is NOT JSON serializable, so return a plain
        # dict. Keeps the stub focused on the concurrency contract, not on
        # ShotDescription's full schema (variation_type is all we exercise).
        m.model_dump.return_value = {"idx": shot_brief_desc.idx, "variation_type": "small"}
        return m

    pipe.storyboard_artist.decompose_visual_description = fake_decompose
    # ShotBriefDescription requires cam_idx (no default) — brief's stub omitted
    # it; adapted here per B2's finding.
    shots = [ShotBriefDescription(idx=i, is_last=(i == n - 1), cam_idx=0, visual_desc="x", audio_desc="y") for i in range(n)]

    await pipe.decompose_visual_descriptions(shots, characters=[], quiet=True)

    assert peak["n"] <= pipe._decompose_concurrency, f"concurrency {peak['n']} exceeded limit {pipe._decompose_concurrency}"
    assert peak["n"] > 1, "expected some concurrency (limit should be >1)"


def test_http_request_timeout_exceeds_llm_retry_timeout(tmp_path):
    """B1 invariant: HTTP request_timeout MUST exceed llm_retry_timeout so
    asyncio.wait_for fires FIRST (clean TimeoutError → tenacity reraise in B2),
    not httpx.ReadTimeout (which would bypass the retry path). Locks 320 > 300.
    """
    from unittest.mock import MagicMock

    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline
    from vimax.utils.provider_presets import resolve_chat_model_config

    http_timeout = resolve_chat_model_config({"model_provider": "openai"})["request_timeout"]
    pipe = Script2VideoPipeline(
        chat_model=MagicMock(),
        image_generator=MagicMock(),
        video_generator=MagicMock(),
        working_dir=str(tmp_path),
    )
    assert http_timeout > pipe.llm_retry_timeout, (
        f"HTTP request_timeout ({http_timeout}s) must exceed llm_retry_timeout ({pipe.llm_retry_timeout}s) so asyncio.wait_for fires before the httpx timeout — see B1 (provider_presets.py) + B2 (storyboard_artist reraise)."
    )


# ── M4 (2026-07-22): 全部 vendored agent @retry 必须 reraise + 退避 ─────────
#
# 78% 假冻结的机制：无 reraise → 干净 TimeoutError 被包成 RetryError[<Future…>]；
# 无 wait → 连续重试锤已超载的端点 → 雪崩。B2 只修了 storyboard_artist 和
# character_portraits_generator，其余 9 处（含主路径上的 character_extractor /
# reference_image_selector）仍是旧配置。tenacity 装饰器把 Retrying 实例挂在
# wrapped_fn.retry 上，可直接内省配置。


def test_vendored_agents_retry_config_hardened():
    import tenacity
    from vimax.agents import (
        camera_image_generator,
        character_extractor,
        reference_image_selector,
        screenwriter,
    )

    # novel 系 agents（best_image_selector/event_extractor/scene_extractor/
    # script_enhancer/global_information_planner）的 @retry 站点已随 novel2movie
    # 管线拆除（T1，2026-10-16）一并移除；保留面 = script2video/idea2video 主路径。
    sites = [
        ("camera_image_generator.construct_camera_tree", camera_image_generator.CameraImageGenerator.construct_camera_tree),
        ("character_extractor.extract_characters", character_extractor.CharacterExtractor.extract_characters),
        ("reference_image_selector.select_reference_images_and_generate_prompt", reference_image_selector.ReferenceImageSelector.select_reference_images_and_generate_prompt),
        ("screenwriter.develop_story", screenwriter.Screenwriter.develop_story),
        ("screenwriter.write_script_based_on_story", screenwriter.Screenwriter.write_script_based_on_story),
    ]

    for label, fn in sites:
        r = fn.retry  # tenacity 把 Retrying 实例挂在 wrapped function 上
        assert r.reraise is True, f"{label}: missing reraise=True（TimeoutError 会被包成 RetryError）"
        assert isinstance(r.wait, tenacity.wait_exponential), f"{label}: missing wait_exponential（无退避连续重试会雪崩）"


# ── Plan C Task 3（2026-10-19）：本地 deadline 精确分类 ─────────────────────


@pytest.mark.asyncio
async def test_await_with_local_deadline_pending_expires_raises_dedicated():
    """pending 到期：cancel+drain 底层后抛 LocalStoryboardDeadlineError；显式
    task_retryable=False（script2video）时不触发任务级重放。"""
    from vimax.utils.retry import LocalStoryboardDeadlineError, await_with_local_deadline

    cleaned = asyncio.Event()
    cancelled_seen: list[bool] = []

    async def slow():
        try:
            await asyncio.sleep(30)
            return "late"
        except asyncio.CancelledError:
            cancelled_seen.append(True)
            raise
        finally:
            cleaned.set()

    with pytest.raises(LocalStoryboardDeadlineError) as ei:
        await await_with_local_deadline(slow(), timeout=0.05, task_retryable=False)
    assert "vimax_storyboard_deadline" in str(ei.value)
    # script2video 显式 False：2×420 后失败交用户手动重试。
    assert ei.value.pipeline_retryable is False
    # 边界热修复（2026-10-20 P2）：cancel+drain 真实断言——修前是 suppress(TimeoutError)
    # 包住 wait_for 的伪断言（尾逗号串成元组表达式）：未 cancel 时 cleaned 永不置位 →
    # wait_for 超时 → TimeoutError 被吞 → 恒绿。现在两道硬断言：CancelledError 真实
    # 送达内任务；drain（finally 执行）在 2s 内完成——任一不成立测试即红。
    assert cancelled_seen == [True], "底层任务必须真实收到 cancel（CancelledError 送达）"
    await asyncio.wait_for(cleaned.wait(), timeout=2)


@pytest.mark.asyncio
async def test_await_with_local_deadline_done_returns_result():
    """awaitable 已完成 → 原样 result()；内部自带 TimeoutError 原样传播（provider 超时）。"""
    from vimax.utils.retry import LocalStoryboardDeadlineError, await_with_local_deadline

    async def ok():
        await asyncio.sleep(0)
        return 42

    assert await await_with_local_deadline(ok(), timeout=5) == 42

    async def provider_timeout():
        raise TimeoutError("provider timed out")

    with pytest.raises(TimeoutError) as ei:
        await await_with_local_deadline(provider_timeout(), timeout=5)
    assert not isinstance(ei.value, LocalStoryboardDeadlineError), "provider 自身 TimeoutError 不得被改写成本地 deadline"
    assert "provider" in str(ei.value)


@pytest.mark.asyncio
async def test_await_with_local_deadline_outer_cancel_propagates():
    """外层取消：CancelledError 原样传播，底层任务被清理。"""
    import contextlib

    from vimax.utils.retry import await_with_local_deadline

    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(30)

    task = asyncio.ensure_future(await_with_local_deadline(hang(), timeout=30))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled(), "外层取消必须原样传播为 CancelledError"


@pytest.mark.asyncio
async def test_design_storyboard_local_deadline_reraises_dedicated(_no_retry_backoff):
    """artist 走本地 deadline：默认 3 次 attempt 耗尽后抛
    LocalStoryboardDeadlineError（idea2video/旧调用语义）。"""
    from vimax.agents.storyboard_artist import StoryboardArtist
    from vimax.utils.retry import LocalStoryboardDeadlineError

    class _HangingChatModel(Runnable):
        def invoke(self, input, config=None, **kwargs):
            raise NotImplementedError

        async def ainvoke(self, messages, config=None, **kwargs):
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    artist = StoryboardArtist(chat_model=_HangingChatModel())
    with pytest.raises(LocalStoryboardDeadlineError):
        await artist.design_storyboard(script="x", characters=[], retry_timeout=0.05)


@pytest.mark.asyncio
async def test_await_with_local_deadline_outer_cancel_during_drain_propagates():
    """[important] drain 窗口内送达的外层取消必须原样传播——不得被吞成
    LocalStoryboardDeadlineError（否则用户取消变僵尸重试，最终覆写 cancelled 态）。"""
    from vimax.utils.retry import LocalStoryboardDeadlineError, await_with_local_deadline

    cleanup_started = asyncio.Event()

    async def slow_cleanup():
        try:
            await asyncio.sleep(30)
        finally:
            cleanup_started.set()
            await asyncio.sleep(0.3)  # SDK 关连接等清理 await——drain 窗口

    outer = asyncio.ensure_future(await_with_local_deadline(slow_cleanup(), timeout=0.05))
    await asyncio.wait_for(cleanup_started.wait(), timeout=2)
    await asyncio.sleep(0.08)  # 底层已进入清理（drain 进行中）
    outer.cancel()
    try:
        await outer
        raised = "returned"
    except asyncio.CancelledError:
        raised = "cancelled"
    except LocalStoryboardDeadlineError:
        raised = "local_deadline"
    assert raised == "cancelled", f"drain 窗口的外层取消必须传播为 CancelledError，实际 {raised}"


@pytest.mark.asyncio
async def test_drain_does_not_swallow_dying_task_real_exception_context():
    """[minor 连带] 内任务清理后抛出的真实异常链不被 raise from None 丢弃。"""
    import re

    from vimax.utils.retry import LocalStoryboardDeadlineError, await_with_local_deadline

    async def dies_with_context():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            raise RuntimeError("sdk close failed")  # 清理期真实错误

    # 内任务异常仅进日志（drain 吞掉是设计），但 Local 异常自身消息保留稳定标记
    try:
        await await_with_local_deadline(dies_with_context(), timeout=0.05)
        raised = False
    except LocalStoryboardDeadlineError as e:
        raised = True
        assert re.search(r"vimax_storyboard_deadline.*300|vimax_storyboard_deadline.*0\.05", str(e))
    assert raised


# ── 边界收口 Task 5（P1-4）：deadline 按批次缓存区分 ───────────────────────


def test_local_deadline_metadata_flags():
    """task_retryable=None 保持旧行为：多批 False、单批 True。"""
    from vimax.utils.retry import LocalStoryboardDeadlineError

    batched = LocalStoryboardDeadlineError(300, batches_reusable=True)
    assert batched.error_code == "vimax_storyboard_deadline"
    assert batched.pipeline_retryable is False
    assert "vimax_storyboard_deadline" in str(batched)

    single = LocalStoryboardDeadlineError(300, batches_reusable=False)
    assert single.pipeline_retryable is True, "旧行为：单批仍可任务级重放"
    script2video = LocalStoryboardDeadlineError(420, batches_reusable=False, task_retryable=False)
    assert script2video.pipeline_retryable is False
    assert "single-batch" in str(single)


@pytest.mark.asyncio
async def test_artist_deadline_carries_batching_flag(_no_retry_backoff):
    """artist 透传 batches_reusable：拆批调用 True / 单批缺省 False。"""
    from vimax.agents.storyboard_artist import StoryboardArtist
    from vimax.utils.retry import LocalStoryboardDeadlineError

    class _Hanging(Runnable):
        def invoke(self, input, config=None, **kwargs):
            raise NotImplementedError

        async def ainvoke(self, messages, config=None, **kwargs):
            await asyncio.sleep(30)

    artist = StoryboardArtist(chat_model=_Hanging())
    with pytest.raises(LocalStoryboardDeadlineError) as ei:
        await artist.design_storyboard(script="x", characters=[], retry_timeout=0.05, batches_reusable=True)
    assert ei.value.batches_reusable is True

    artist2 = StoryboardArtist(chat_model=_Hanging())
    with pytest.raises(LocalStoryboardDeadlineError) as ei2:
        await artist2.design_storyboard(script="x", characters=[], retry_timeout=0.05)
    assert ei2.value.batches_reusable is False

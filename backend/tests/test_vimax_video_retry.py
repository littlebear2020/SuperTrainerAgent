"""Regression tests for ViMax i2v (video) generation timeouts (vg_4eea9dfd7215).

Root cause: the Hailuo/360 video API occasionally stalls — ``QihuVideoProvider._poll``
waits the full ``max_wait`` (600s) then raises ``TimeoutError``. That propagated up
through ViMax → ``_wrap_pipeline`` and triggered a **whole-pipeline Retry 1/2**
(re-running every scene). Even with ViMax's per-shot file cache, a single 600s
timeout + full re-run is what made the task take 30+ min.

Fix B1: retry the i2v call LOCALLY (per-shot) on TimeoutError — so one slow Hailuo
task doesn't nuke the whole pipeline. The retry must NOT swallow ``VimaxBudgetExceeded``
(re-running would defeat the cost hard-cutoff).

Fix B2: ``max_wait`` was hardcoded 600s at every layer (config → bridge → provider);
make it configurable so it can be tuned when Hailuo is systematically slow.
"""

import asyncio

import pytest

from app.gateway.video_gen.engines.vimax.provider_bridge import (
    SeedanceVideoGeneratorForViMax,
    VimaxBudgetExceeded,
    build_vimax_config,
)
from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.providers.qihu_video import QihuServerError


def _make_video_ref(url="https://example.com/v.mp4") -> VideoRef:
    return VideoRef(url=url)


# ── B1: per-shot retry on TimeoutError ────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_single_video_retries_on_timeout_then_succeeds():
    """A single Hailuo TimeoutError must be retried locally (not nuke the pipeline).

    Mock provider: first call raises TimeoutError, second succeeds. After the retry,
    the generator must return the VideoOutput and the provider must have been called
    twice (1 initial + 1 retry)."""
    call_count = 0

    class _FlakyProvider:
        async def generate_video(self, prompt, image_url=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("simulated Hailuo poll timeout")
            return _make_video_ref()

    gen = SeedanceVideoGeneratorForViMax(api_key="k")
    gen._provider = _FlakyProvider()  # inject the flaky provider

    out = await gen.generate_single_video(prompt="a cat runs")

    assert call_count == 2  # 1 initial (timed out) + 1 retry (success)
    assert out is not None
    assert out.data == "https://example.com/v.mp4"


@pytest.mark.asyncio
async def test_generate_single_video_does_not_retry_budget_exceeded():
    """VimaxBudgetExceeded must NOT be retried — it would defeat the cost cap.

    max_calls=0 trips the budget check on the very first call (before the provider
    is reached). Assert it propagates immediately and the provider is never called."""
    call_count = 0

    class _BudgetProvider:
        async def generate_video(self, prompt, image_url=None, **kwargs):
            nonlocal call_count
            call_count += 1
            raise VimaxBudgetExceeded("cost cap")

    gen = SeedanceVideoGeneratorForViMax(api_key="k", max_calls=0)
    gen._provider = _BudgetProvider()

    # max_calls=0 → budget tripped before any provider call
    with pytest.raises(VimaxBudgetExceeded):
        await gen.generate_single_video(prompt="x")
    assert call_count == 0  # provider never reached (budget checked first)


@pytest.mark.asyncio
async def test_generate_single_video_does_not_retry_runtime_errors():
    """Generic errors (not TimeoutError) must propagate without retry — retrying a
    genuine API error (4xx, malformed request) would just repeat the failure."""
    call_count = 0

    class _ErrorProvider:
        async def generate_video(self, prompt, image_url=None, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("400 Bad Request: invalid prompt")

    gen = SeedanceVideoGeneratorForViMax(api_key="k")
    gen._provider = _ErrorProvider()

    with pytest.raises(RuntimeError):
        await gen.generate_single_video(prompt="x")
    assert call_count == 1  # no retry


# ── B2: max_wait flows config → bridge → provider ─────────────────────────────


def test_build_vimax_config_passes_video_max_wait_when_set():
    """build_vimax_config must carry video_max_wait into video_generator.init_args
    when provided, so QihuVideoProvider.poll stops at the configured budget."""
    cfg = build_vimax_config(
        working_dir="/tmp/wd",
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="k",
        video_api_key="k",
        video_max_wait=300,
    )
    video_init = cfg["video_generator"]["init_args"]
    assert video_init.get("max_wait") == 300


def test_build_vimax_config_omits_max_wait_when_unset():
    """No max_wait override → don't inject it (QihuVideoProvider keeps its 600 default)."""
    cfg = build_vimax_config(
        working_dir="/tmp/wd",
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="k",
        video_api_key="k",
    )
    assert "max_wait" not in cfg["video_generator"]["init_args"]


def test_video_max_wait_config_field_exists():
    """VideoGenConfig must expose a vimax_video_max_wait knob (default 400, per Phase 1.2)."""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert hasattr(cfg, "vimax_video_max_wait")
    assert cfg.vimax_video_max_wait == 400


# ── B3: 5xx server errors retry locally (vg_1b893b5f09e1: 360 returned 500) ───
# The 360 video API occasionally returns HTTP 500 ("服务异常", code 10183) — a
# transient server-side fault. It DOES recover on retry (vg_1b893b5f09e1 succeeded
# after the task-layer Retry 1/2). But that retry re-ran the whole pipeline; a LOCAL
# per-shot retry (like TimeoutError) recovers faster and doesn't burn the task's
# retry budget. Fix: _submit raises a dedicated QihuServerError for 5xx (retryable),
# keeping RuntimeError for 4xx (won't fix itself); the shot-level retry covers both.


def test_submit_raises_qihu_server_error_on_5xx():
    """A 5xx response is a transient server fault → QihuServerError (retryable),
    NOT a plain RuntimeError (which the shot retry skips as 'won't fix itself')."""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.providers.qihu_video import QihuServerError, QihuVideoProvider

    provider = QihuVideoProvider(api_key="k")
    # mock a 500 response — patch _get_client so the provider uses our mock client
    resp = MagicMock()
    resp.status_code = 500
    resp.text = '{"error":{"code":"10183","message":"服务异常:服务异常"}}'
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=resp)
    provider._get_client = lambda: mock_client

    with pytest.raises(QihuServerError):
        asyncio.run(provider._submit("prompt", duration=8, image_url=None))


def test_submit_keeps_runtime_error_on_4xx():
    """A 4xx (e.g. 400 Bad Request) is a client error → stays RuntimeError (NOT retried;
    retrying a malformed request just repeats the failure). Only 5xx is retryable."""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.providers.qihu_video import QihuServerError, QihuVideoProvider

    provider = QihuVideoProvider(api_key="k")
    resp = MagicMock()
    resp.status_code = 400
    resp.text = '{"error":"bad request"}'
    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=resp)
    provider._get_client = lambda: mock_client

    # 4xx raises plain RuntimeError, NOT QihuServerError
    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(provider._submit("prompt", duration=8, image_url=None))
    assert not isinstance(exc_info.value, QihuServerError)


@pytest.mark.asyncio
async def test_generate_single_video_retries_on_5xx_server_error():
    """A transient 500 from the 360 submit must retry locally (like TimeoutError),
    not bubble up to trigger a whole-pipeline Retry. Mock: first call 500s, second
    succeeds. After retry, the generator returns the VideoOutput and the provider was
    called twice."""
    call_count = 0

    class _FlakyProvider:
        async def generate_video(self, prompt, image_url=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise QihuServerError("360 video submit failed (500): 服务异常")
            return _make_video_ref()

    gen = SeedanceVideoGeneratorForViMax(api_key="k")
    gen._provider = _FlakyProvider()

    out = await gen.generate_single_video(prompt="a cat runs")

    assert call_count == 2  # 1 initial (500) + 1 retry (success)
    assert out is not None
    assert out.data == "https://example.com/v.mp4"

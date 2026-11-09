"""Tests for ThrottledProvider and AI Visual providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gateway.video_gen.providers import throttle as throttle_mod
from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.providers.throttle import (
    ProviderUnavailableError,
    RateLimitError,
    ThrottledAIImageProvider,
    ThrottledAIVideoProvider,
)


def _no_backoff(monkeypatch):
    """throttle 重试退避 2**attempt+rand → 0：重试次数断言不变，只是不真睡。"""
    monkeypatch.setattr(throttle_mod, "_backoff", lambda _attempt: 0.0)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeImageProvider:
    def __init__(self, responses=None, fail_count=0):
        self._responses = responses or [ImageRef(url="https://img.test/1.png", source="test")]
        self._fail_count = fail_count
        self._call_count = 0

    async def generate_image(self, prompt: str, **kwargs):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise RateLimitError("rate limited")
        return self._responses[min(self._call_count - 1, len(self._responses) - 1)]


class _FakeVideoProvider:
    def __init__(self, responses=None, fail_count=0):
        self._responses = responses or [VideoRef(url="https://vid.test/1.mp4", duration=5.0, source="test")]
        self._fail_count = fail_count
        self._call_count = 0

    async def generate_video(self, prompt: str, *, duration: float = 5.0, **kwargs):
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise RateLimitError("rate limited")
        return self._responses[min(self._call_count - 1, len(self._responses) - 1)]


# ---------------------------------------------------------------------------
# ThrottledAIImageProvider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_provider_success():
    inner = _FakeImageProvider()
    throttled = ThrottledAIImageProvider(inner, qps=100, max_retries=0)
    result = await throttled.generate_image("a cat")
    assert isinstance(result, ImageRef)
    assert result.url == "https://img.test/1.png"


@pytest.mark.asyncio
async def test_image_provider_retries_on_rate_limit(monkeypatch):
    _no_backoff(monkeypatch)
    inner = _FakeImageProvider(fail_count=2)
    throttled = ThrottledAIImageProvider(inner, qps=100, max_retries=3)
    result = await throttled.generate_image("a cat")
    assert isinstance(result, ImageRef)
    assert inner._call_count == 3  # 2 fails + 1 success


@pytest.mark.asyncio
async def test_image_provider_exhausts_retries(monkeypatch):
    _no_backoff(monkeypatch)
    inner = _FakeImageProvider(fail_count=5)
    throttled = ThrottledAIImageProvider(inner, qps=100, max_retries=2)
    with pytest.raises(RateLimitError):
        await throttled.generate_image("a cat")
    assert inner._call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_image_provider_unavailable_no_retry(monkeypatch):
    _no_backoff(monkeypatch)

    class _Unavailable:
        async def generate_image(self, prompt, **kw):
            raise ProviderUnavailableError("circuit open")

    throttled = ThrottledAIImageProvider(_Unavailable(), qps=100, max_retries=3)
    with pytest.raises(ProviderUnavailableError):
        await throttled.generate_image("a cat")


# ---------------------------------------------------------------------------
# ThrottledAIVideoProvider tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_provider_success():
    inner = _FakeVideoProvider()
    throttled = ThrottledAIVideoProvider(inner, qps=100, max_retries=0)
    result = await throttled.generate_video("sunset", duration=5.0)
    assert isinstance(result, VideoRef)
    assert result.url == "https://vid.test/1.mp4"


@pytest.mark.asyncio
async def test_video_provider_retries_on_rate_limit(monkeypatch):
    _no_backoff(monkeypatch)
    inner = _FakeVideoProvider(fail_count=3)
    throttled = ThrottledAIVideoProvider(inner, qps=100, max_retries=5)
    result = await throttled.generate_video("sunset", duration=5.0)
    assert isinstance(result, VideoRef)
    assert inner._call_count == 4


@pytest.mark.asyncio
async def test_video_provider_exhausts_retries(monkeypatch):
    _no_backoff(monkeypatch)
    inner = _FakeVideoProvider(fail_count=10)
    throttled = ThrottledAIVideoProvider(inner, qps=100, max_retries=2)
    with pytest.raises(RateLimitError):
        await throttled.generate_video("sunset", duration=5.0)
    assert inner._call_count == 3


# ---------------------------------------------------------------------------
# Provider registration test
# ---------------------------------------------------------------------------


def test_ai_visual_registered_in_worker_pipelines():
    import sys

    pipeline_dir = Path(__file__).parent.parent.parent / "docker" / "production-worker"
    if str(pipeline_dir) not in sys.path:
        sys.path.insert(0, str(pipeline_dir))

    # Earlier worker tests (test_worker_*, test_ai_video_*) stub a bare
    # `pipelines` module in sys.modules to avoid loading the real ffmpeg
    # helpers; that shadows the real package ("'pipelines' is not a package")
    # and breaks this import. Drop any stale `pipelines*` entries so the
    # import resolves to the real package on sys.path.
    for mod in [m for m in list(sys.modules) if m == "pipelines" or m.startswith("pipelines.")]:
        del sys.modules[mod]

    from pipelines.registry import PIPELINES, resolve_pipeline

    assert ("video_gen", "ai_visual") in PIPELINES
    fn = resolve_pipeline("video_gen", "ai_visual")
    assert callable(fn)

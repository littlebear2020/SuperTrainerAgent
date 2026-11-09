"""Task 1: seedance-ark 请求体必须携带 duration/ratio（修 P0-1/P0-2）。"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
from app.gateway.video_gen.visual_sources.ai_video import _size_to_ratio


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"id": "cgt-test-1"}


class _FakePollResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"status": "succeeded", "content": {"video_url": "https://tos/v.mp4"}}


class _FakeClient:
    """捕获最后一次 POST 的 json body。get 立即返回 poll 成功（测试只关心 submit body）。"""

    def __init__(self):
        self.last_body = None
        self.is_closed = False

    async def post(self, url, headers=None, json=None):
        self.last_body = json
        return _FakeResponse()

    async def get(self, url, headers=None):
        return _FakePollResponse()


def _make_provider():
    p = SeedanceArkVideoProvider(api_key="k", poll_interval=0)
    fake = _FakeClient()
    p._client = fake
    return p, fake


@pytest.mark.asyncio
async def test_submit_body_contains_duration_and_ratio():
    p, fake = _make_provider()
    await p.generate_video("a cat walks", duration=8.0, ratio="9:16")
    assert fake.last_body["duration"] == 8
    assert fake.last_body["ratio"] == "9:16"


@pytest.mark.asyncio
async def test_duration_clamped_to_ark_range():
    p, fake = _make_provider()
    await p.generate_video("x", duration=30.0)
    assert fake.last_body["duration"] == 15
    await p.generate_video("x", duration=1.0)
    assert fake.last_body["duration"] == 4


@pytest.mark.asyncio
async def test_ratio_omitted_when_none():
    p, fake = _make_provider()
    await p.generate_video("x", duration=5.0)
    assert "ratio" not in fake.last_body


def test_size_to_ratio():
    assert _size_to_ratio(1080, 1920) == "9:16"
    assert _size_to_ratio(1920, 1080) == "16:9"
    assert _size_to_ratio(1024, 1024) == "1:1"
    # 非支持集 → adaptive（Ark 支持集 21:9|16:9|4:3|1:1|3:4|9:16|adaptive）
    assert _size_to_ratio(1000, 1917) == "adaptive"


def test_provider_capability_attrs():
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider as P

    assert P(api_key="k").max_video_duration == 15.0


# ── 评审 Important-1: 降级路径 duration 须钳制到 fallback 自身上限 ──


class _FailingPrimary:
    """primary 抛 ProviderUnavailableError（属 _FALLBACK_EXC）→ 触发降级路径。"""

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        from app.gateway.video_gen.providers.throttle import ProviderUnavailableError

        raise ProviderUnavailableError("primary down")


class _RecordingFallback:
    """记录收到的 duration；刻意不设 max_video_duration 属性 → getattr 默认 8.0。"""

    def __init__(self):
        self.received_duration = None

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        self.received_duration = duration
        return None


@pytest.mark.asyncio
async def test_fallback_duration_clamped_to_fallback_cap():
    """primary=seedance-ark(cap 15s) 失败降级 fallback(cap ~8s) 时，12s 须钳到 8s，
    否则 360 拒收白烧一次降级（帧落占位图）。"""
    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider

    fallback = _RecordingFallback()
    wrapped = FallbackAIVideoProvider(primary=_FailingPrimary(), fallback=fallback)
    await wrapped.generate_video("x", duration=12.0)
    assert fallback.received_duration == 8.0

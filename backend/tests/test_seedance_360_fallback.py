"""E3（360 通道切换，2026-08-21）：360→Ark 降级链对调 + Ark 在途闸。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E3：
- 主备对调：``ai_video_provider=seedance`` 且 ``ark_api_key`` 存在时 fallback 挂
  ``seedance-ark``（触发沿用 ``_FALLBACK_EXC``：429/5xx/超时/传输错误；
  4xx 内容拒绝不降级）；primary=seedance-ark 时不再挂 360 fallback（方向已反转）；
- ``seedance_ark.py`` 提交路径加跨任务 ``asyncio.Semaphore``（默认 3，配置项
  ``ark_video_max_inflight``），超出排队不报错。
"""

from __future__ import annotations

import asyncio

import pytest
import respx

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider, ProviderRegistry
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


def _unwrap_throttle(provider):
    """剥 Throttle 取内层（只剥一层——E3 断言关注 primary/fallback 结构）。"""
    return provider._inner if isinstance(provider, ThrottledAIVideoProvider) else provider


def test_registry_seedance_primary_hangs_ark_fallback():
    """primary=seedance 且 ark key 存在 → Fallback(seedance, seedance-ark)。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    cfg = VideoGenConfig(qihu_api_key="k", ark_api_key="a", ai_video_provider="seedance")
    reg = ProviderRegistry(cfg)
    wrapped = reg.ai_video
    assert isinstance(wrapped, FallbackAIVideoProvider)
    primary = _unwrap_throttle(wrapped._primary)
    fallback = _unwrap_throttle(wrapped._fallback)
    assert isinstance(primary, SeedanceVideoProvider)
    assert isinstance(fallback, SeedanceArkVideoProvider)


def test_registry_ark_primary_no_360_fallback():
    """primary=seedance-ark → 不再挂 360 fallback（方向已反转，单 Throttle）。"""
    cfg = VideoGenConfig(qihu_api_key="k", ark_api_key="a", ai_video_provider="seedance-ark")
    reg = ProviderRegistry(cfg)
    assert isinstance(reg.ai_video, ThrottledAIVideoProvider), f"ark 主力不应再挂 360 fallback，实际: {type(reg.ai_video)}"


def test_registry_seedance_primary_without_ark_key_has_no_fallback():
    """ark key 缺失 → seedance 单 Throttle（无兜底可挂）。"""
    cfg = VideoGenConfig(qihu_api_key="k", ai_video_provider="seedance")
    reg = ProviderRegistry(cfg)
    assert isinstance(reg.ai_video, ThrottledAIVideoProvider)


class _RateLimitedPrimary:
    """429（RateLimitError ∈ _FALLBACK_EXC）→ 应触发降级。"""

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        from app.gateway.video_gen.providers.throttle import RateLimitError

        raise RateLimitError("360 rate limited")


class _ContentRejectedPrimary:
    """4xx 内容拒绝（QihuClientError ⊂ RuntimeError ∉ _FALLBACK_EXC）→ 不降级。"""

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        from app.gateway.video_gen.providers.qihu_video import QihuClientError

        raise QihuClientError("content rejected", 400)


class _ArkSink:
    def __init__(self):
        self.called = False

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        from app.gateway.video_gen.providers.base import VideoRef

        self.called = True
        return VideoRef(url="https://ark/v.mp4", duration=duration, source="seedance-ark")


@pytest.mark.asyncio
async def test_seedance_primary_429_falls_to_ark():
    sink = _ArkSink()
    wrapped = FallbackAIVideoProvider(primary=_RateLimitedPrimary(), fallback=sink)
    ref = await wrapped.generate_video("p", duration=5.0)
    assert sink.called and ref.source == "seedance-ark"


@pytest.mark.asyncio
async def test_seedance_primary_4xx_content_rejection_no_fallback():
    sink = _ArkSink()
    wrapped = FallbackAIVideoProvider(primary=_ContentRejectedPrimary(), fallback=sink)
    with pytest.raises(RuntimeError):
        await wrapped.generate_video("p", duration=5.0)
    assert not sink.called, "4xx 内容拒绝不得降级（requirement #4）"


# ── Ark 在途闸 ──────────────────────────────────────────────────────────────


class _DelayedClient:
    """post 慢速（模拟提交耗时），记录同时在途峰值。"""

    def __init__(self, delay: float = 0.05):
        self.delay = delay
        self.inflight = 0
        self.peak = 0
        self.completed = 0
        self.is_closed = False

    async def post(self, url, headers=None, json=None):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(self.delay)
        self.inflight -= 1
        self.completed += 1
        return _SubmitOkResponse()


class _SubmitOkResponse:
    status_code = 200
    text = "{}"

    def json(self):
        return {"id": "cgt-gate-1"}


@pytest.mark.asyncio
async def test_ark_submit_inflight_gate_caps_at_limit():
    """并发 5 个提交时在途 ≤3、其余排队、全部最终成功（mock 延时）。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    p = SeedanceArkVideoProvider(api_key="k", max_inflight=3)
    fake = _DelayedClient()
    p._client = fake

    results = await asyncio.gather(*[p.submit_video(f"prompt {i}", duration=5.0) for i in range(5)])
    assert all(t == "cgt-gate-1" for t in results), "排队不得产生异常上抛"
    assert fake.completed == 5
    assert fake.peak <= 3, f"在途峰值 {fake.peak} 超过上限 3"


@pytest.mark.asyncio
async def test_ark_gate_limit_from_config_via_registry():
    """配置项 ark_video_max_inflight 经 registry 接线到 provider。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    cfg = VideoGenConfig(qihu_api_key="k", ark_api_key="a", ai_video_provider="seedance", ark_video_max_inflight=2)
    reg = ProviderRegistry(cfg)
    wrapped = reg.ai_video
    assert isinstance(wrapped, FallbackAIVideoProvider)
    ark = _unwrap_throttle(wrapped._fallback)
    assert isinstance(ark, SeedanceArkVideoProvider)
    assert ark._max_inflight == 2

    fake = _DelayedClient()
    ark._client = fake
    await asyncio.gather(*[ark.submit_video(f"p{i}", duration=5.0) for i in range(4)])
    assert fake.peak <= 2, f"自定义上限 2 未生效，峰值 {fake.peak}"


@pytest.mark.asyncio
async def test_ark_submit_gate_is_cross_task():
    """跨任务共享：两个 provider 实例（同 loop）合计在途 ≤ 上限。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    shared = _DelayedClient()
    p1 = SeedanceArkVideoProvider(api_key="k", max_inflight=3)
    p2 = SeedanceArkVideoProvider(api_key="k", max_inflight=3)
    p1._client = shared
    p2._client = shared
    await asyncio.gather(*[p1.submit_video("a", duration=5.0), p2.submit_video("b", duration=5.0), p1.submit_video("c", duration=5.0), p2.submit_video("d", duration=5.0)])
    assert shared.peak <= 3
    assert shared.completed == 4


# ── AC4 缺口补齐（2026-08-24 实测）：鉴权类 4xx 须可降级 ──────────────────────


class _AuthRejectedPrimary:
    """401 鉴权失效（实测 360 返回 {"code":"1002","message":"鉴权不通过"}）——
    通道整体不可用而非单请求内容问题，应触发降级（AC4 坏 key 演练语义）。"""

    async def generate_video(self, prompt, *, duration=30.0, **kwargs):
        from app.gateway.video_gen.providers.throttle import ProviderUnavailableError

        raise ProviderUnavailableError("360 seedance submit failed (401): 鉴权不通过")


@respx.mock
@pytest.mark.asyncio
async def test_auth_401_maps_to_provider_unavailable():
    """seedance._submit 的 401/403 → ProviderUnavailableError（∈ _FALLBACK_EXC，
    进重试+降级）；其余 4xx（内容拒绝）维持 QihuClientError 不降级。"""
    import httpx
    import respx

    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    for sc in (401, 403):
        respx.post("https://api.360.cn/v1/videos/async_generations").mock(return_value=httpx.Response(sc, json={"error": {"code": "1002", "message": "鉴权不通过"}}))
        p = SeedanceVideoProvider(api_key="bad", poll_interval=0.01)
        with pytest.raises(Exception) as ei:
            await p.submit_video("x", duration=5.0)
        assert type(ei.value).__name__ == "ProviderUnavailableError", f"{sc} 须映射 ProviderUnavailableError，实际 {type(ei.value).__name__}"
        respx.reset()
    # 回归：400 内容拒绝仍 QihuClientError（不降级语义保持）
    respx.post("https://api.360.cn/v1/videos/async_generations").mock(return_value=httpx.Response(400, json={"error": {"code": "10940", "message": "content rejected"}}))
    from app.gateway.video_gen.providers.qihu_video import QihuClientError

    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    with pytest.raises(QihuClientError):
        await p.submit_video("x", duration=5.0)


@pytest.mark.asyncio
async def test_auth_401_falls_to_ark():
    """端到端：401 型故障 → 落 Ark（AC4 演练路径）。"""
    sink = _ArkSink()
    wrapped = FallbackAIVideoProvider(primary=_AuthRejectedPrimary(), fallback=sink)
    ref = await wrapped.generate_video("p", duration=5.0)
    assert sink.called and ref.source == "seedance-ark"

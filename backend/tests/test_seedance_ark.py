"""Tests for the ByteDance Ark seedance-ark video provider (Stage 5.1).

SeedanceArkVideoProvider calls Ark ``/api/v3/contents/generations/tasks`` (submit + poll),
NOT the 360 ``/v1/videos/async_generations`` that ``QihuVideoProvider`` uses. Key points
(see backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 5.1 + Stage 0 实测):

- ``generate_audio=false`` in the submit body (project自管 TTS — 防双音轨).
- Error classification: 429→RateLimitError, 5xx→ProviderUnavailableError (→
  FallbackAIVideoProvider degrades to Hailuo on infra), 4xx/content-review→RuntimeError
  (NOT in _FALLBACK_EXC → no fallback; requirement #4).
- i2v: reference image → ``content`` array entry ``{type:image_url, image_url:{url}, role:first_frame}``.
- poll: ``status:running→succeeded``, video URL at ``content.video_url``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef

_ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_SUBMIT = f"{_ARK_BASE}/contents/generations/tasks"


def _poll_url(task_id: str) -> str:
    return f"{_SUBMIT}/{task_id}"


class TestParseLastFrameUrl:
    """C4 P0-A（2026-08-14）：尾帧字段名探测提为纯函数，4 候选全覆盖 + 未命中告警。

    修前候选解析内联在 _poll 里且未命中静默 fallback 本地抽帧——不可观测。
    """

    @pytest.mark.parametrize(
        "key",
        ["last_frame_url", "last_frame", "tail_frame_url", "last_image_url"],
    )
    def test_each_candidate_hits(self, key):
        from app.gateway.video_gen.providers.seedance_ark import _parse_last_frame_url

        assert _parse_last_frame_url({key: "http://tail.png"}) == "http://tail.png"

    def test_first_candidate_wins(self):
        from app.gateway.video_gen.providers.seedance_ark import _parse_last_frame_url

        content = {"last_frame_url": "http://a.png", "last_frame": "http://b.png"}
        assert _parse_last_frame_url(content) == "http://a.png"

    def test_non_string_value_skipped(self):
        from app.gateway.video_gen.providers.seedance_ark import _parse_last_frame_url

        content = {"last_frame_url": 123, "last_frame": "http://tail.png"}
        assert _parse_last_frame_url(content) == "http://tail.png"

    def test_miss_logs_warning_and_returns_none(self, caplog):
        from app.gateway.video_gen.providers.seedance_ark import _parse_last_frame_url

        with caplog.at_level("WARNING"):
            assert _parse_last_frame_url({"video_url": "http://v"}) is None
        assert any("last-frame" in r.getMessage() for r in caplog.records), "未命中必须记 warning（不可观测=静默降级）"

    def test_none_content_returns_none(self):
        from app.gateway.video_gen.providers.seedance_ark import _parse_last_frame_url

        assert _parse_last_frame_url(None) is None


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_t2v_submits_and_polls_to_video_url() -> None:
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    task_id = "cgt-test-t2v"
    respx.post(_SUBMIT).mock(return_value=httpx.Response(200, json={"id": task_id}))
    poll = respx.get(_poll_url(task_id))
    poll.side_effect = [
        httpx.Response(200, json={"status": "running"}),
        httpx.Response(200, json={"status": "succeeded", "content": {"video_url": "https://tos/video.mp4"}}),
    ]
    provider = SeedanceArkVideoProvider(api_key="ark-key", poll_interval=0)
    ref = await provider.generate_video("a girl reading in a cafe", duration=5)
    assert isinstance(ref, VideoRef)
    assert ref.url == "https://tos/video.mp4"
    assert ref.source == "seedance-ark"
    # submit body: model + content[text] + generate_audio=false
    body = json.loads(respx.calls[0].request.content.decode())
    assert body["model"] == "doubao-seedance-2-0-260128"
    assert body["content"] == [{"type": "text", "text": "a girl reading in a cafe"}]
    assert body["generate_audio"] is False


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_i2v_first_frame_role() -> None:
    """i2v: image_url → content entry with role:first_frame."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    task_id = "cgt-test-i2v"
    respx.post(_SUBMIT).mock(return_value=httpx.Response(200, json={"id": task_id}))
    respx.get(_poll_url(task_id)).mock(
        return_value=httpx.Response(200, json={"status": "succeeded", "content": {"video_url": "https://tos/v.mp4"}}),
    )
    provider = SeedanceArkVideoProvider(api_key="ark-key", poll_interval=0)
    await provider.generate_video("a cat walking", duration=5, image_url="data:image/png;base64,xxx")
    body = json.loads(respx.calls[0].request.content.decode())
    # content = [text, image_url with role:first_frame]
    assert body["content"][0] == {"type": "text", "text": "a cat walking"}
    assert body["content"][1]["type"] == "image_url"
    assert body["content"][1]["image_url"]["url"] == "data:image/png;base64,xxx"
    assert body["content"][1]["role"] == "first_frame"
    assert body["generate_audio"] is False


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_last_frame_url_propagates() -> None:
    """B-2: content.last_frame_url 必须经 generate_video 带出到 VideoRef.last_frame_url。

    字段已实跑坐实（任务 cgt-20260814112111-4kt6w：TOS 预签名 PNG，24h 过期，
    候选元组第一位）——Tier E 续写的官方尾帧依赖此传播，修前无断言覆盖。
    """
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    task_id = "cgt-test-last-frame"
    respx.post(_SUBMIT).mock(return_value=httpx.Response(200, json={"id": task_id}))
    respx.get(_poll_url(task_id)).mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "succeeded",
                "content": {"video_url": "https://tos/video.mp4", "last_frame_url": "https://tos/last.png"},
            },
        ),
    )
    provider = SeedanceArkVideoProvider(api_key="ark-key", poll_interval=0)
    ref = await provider.generate_video("a girl reading", duration=5)
    assert ref.last_frame_url == "https://tos/last.png"


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_429_raises_rate_limit_for_fallback() -> None:
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post(_SUBMIT).mock(return_value=httpx.Response(429, text="rate limited"))
    provider = SeedanceArkVideoProvider(api_key="ark-key")
    with pytest.raises(RateLimitError):
        await provider.generate_video("test", duration=5)


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_5xx_raises_provider_unavailable_for_fallback() -> None:
    """5xx is infrastructure → ProviderUnavailableError (in _FALLBACK_EXC → degrade to Hailuo)."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import ProviderUnavailableError

    respx.post(_SUBMIT).mock(return_value=httpx.Response(503, text="unavailable"))
    provider = SeedanceArkVideoProvider(api_key="ark-key")
    with pytest.raises(ProviderUnavailableError):
        await provider.generate_video("test", duration=5)


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_content_review_failure_raises_runtime_error_no_fallback() -> None:
    """A poll returning status:failed with a content-review failure_reason → RuntimeError
    (NOT in _FALLBACK_EXC → no fallback; requirement #4: content review surfaces to user)."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    task_id = "cgt-failed"
    respx.post(_SUBMIT).mock(return_value=httpx.Response(200, json={"id": task_id}))
    respx.get(_poll_url(task_id)).mock(
        return_value=httpx.Response(
            200,
            json={"status": "failed", "content": {"fail_code": "InputImageSensitiveContentDetected"}, "error": {"code": "InputImageSensitiveContentDetected", "message": "sensitive content"}},
        ),
    )
    provider = SeedanceArkVideoProvider(api_key="ark-key", poll_interval=0)
    with pytest.raises(RuntimeError, match="sensitive content"):
        await provider.generate_video("a person", duration=5)


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_4xx_submit_raises_runtime_error() -> None:
    """4xx submit (auth/bad-request) → RuntimeError (no fallback)."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    respx.post(_SUBMIT).mock(return_value=httpx.Response(400, text="bad request"))
    provider = SeedanceArkVideoProvider(api_key="ark-key")
    with pytest.raises(RuntimeError, match="400"):
        await provider.generate_video("test", duration=5)


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_timeout_raises_timeout_error() -> None:
    """Poll never succeeds within max_wait → TimeoutError (in _FALLBACK_EXC → degrade)."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    task_id = "cgt-timeout"
    respx.post(_SUBMIT).mock(return_value=httpx.Response(200, json={"id": task_id}))
    respx.get(_poll_url(task_id)).mock(return_value=httpx.Response(200, json={"status": "running"}))
    provider = SeedanceArkVideoProvider(api_key="ark-key", poll_interval=0, max_wait=0.0)
    with pytest.raises(TimeoutError):
        await provider.generate_video("test", duration=5)


def test_registry_builds_seedance_ark_with_seedance_fallback() -> None:
    """E3（2026-08-21 360 主力切换）后方向已反转：ai_video_provider=seedance-ark →
    单 ThrottledAIVideoProvider（不再挂 360 fallback——360 故障演练中 Ark 是被
    降落方，不应往 360 回灌）。主备对调后的正向用例见
    tests/test_seedance_360_fallback.py。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    cfg = VideoGenConfig(
        qihu_api_key="qihu-key",
        ark_api_key="ark-key",
        ai_video_provider="seedance-ark",
    )
    reg = ProviderRegistry(cfg)
    assert isinstance(reg.ai_video, ThrottledAIVideoProvider)

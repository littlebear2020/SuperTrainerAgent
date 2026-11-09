import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef


@respx.mock
@pytest.mark.asyncio
async def test_generate_video_submits_and_polls():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    task_id = "vid_abc123"
    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": task_id, "status": "created"},
            },
        ),
    )
    poll_route = respx.get(f"https://api.360.cn/v1/videos/async_generations/{task_id}")
    poll_route.side_effect = [
        httpx.Response(200, json={"data": {"status": "processing"}}),
        httpx.Response(
            200,
            json={
                "data": {"status": "success", "url": "https://cdn.360.cn/vid001.mp4"},
            },
        ),
    ]
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("a cat running", duration=8.0)
    assert isinstance(ref, VideoRef)
    assert ref.url == "https://cdn.360.cn/vid001.mp4"
    assert ref.source == "qihu"
    assert ref.duration == 8.0


@respx.mock
@pytest.mark.asyncio
async def test_generate_video_with_reference_image():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": "v1", "status": "created"},
            },
        ),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"status": "success", "url": "https://cdn.360.cn/out.mp4"},
            },
        ),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video(
        "cinematic shot",
        duration=5.0,
        image_url="https://img.test/ref.png",
    )
    assert ref.url == "https://cdn.360.cn/out.mp4"
    submit_body = respx.calls[0].request.content.decode()
    parsed = json.loads(submit_body)
    assert any(c.get("type") == "image_url" for c in parsed["content"])


@respx.mock
@pytest.mark.asyncio
async def test_generate_video_raises_on_task_failure():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"id": "v_fail", "status": "created"},
            },
        ),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v_fail").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {"status": "failed", "failure_reason": "content policy violation"},
            },
        ),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01, max_wait=0.1)
    with pytest.raises(RuntimeError, match="content policy violation"):
        await provider.generate_video("test", duration=8.0)


@respx.mock
@pytest.mark.asyncio
async def test_generate_video_raises_on_failed_status_in_400_body():
    """360 returns task FAILED status embedded in an HTTP 400 error body (not 200).

    Real-world shape (vg_86d2767fe762): poll returns 400 with
    {"error":{"code":"10940","message":"<JSON with data.status=FAILED>"} }.
    The old code did `if status_code != 200: continue` — never parsing the
    embedded FAILED, so it polled the full max_wait then raised TimeoutError
    ("timed out after 400s") instead of the real failure_reason. This masked
    content-review rejections as fake timeouts.
    """
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"id": "v_400fail", "status": "created"}},
        ),
    )
    # 400 body: 360 wraps the real task state inside error.message as a JSON string
    inner = json.dumps({"data": {"id": "v_400fail", "status": "FAILED", "failure_reason": "Green net check failed for image (input): Input data may contain inappropriate content."}})
    respx.get("https://api.360.cn/v1/videos/async_generations/v_400fail").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "10940", "message": inner}},
        ),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01, max_wait=0.1)
    # Must raise RuntimeError with the real failure_reason, NOT a TimeoutError.
    with pytest.raises(RuntimeError, match="Green net check failed"):
        await provider.generate_video("test", duration=8.0)


@respx.mock
@pytest.mark.asyncio
async def test_generate_video_raises_on_rate_limit():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.throttle import RateLimitError

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(429, text="rate limited"),
    )
    provider = QihuVideoProvider(api_key="test-key")
    with pytest.raises(RateLimitError):
        await provider.generate_video("test", duration=8.0)


@respx.mock
@pytest.mark.asyncio
async def test_poll_accepts_succeeded_status():
    """Hailuo returns status 'success'; seedance (doubao-seedance-2-0) returns
    'succeeded'. The shared _poll must recognize both — a live C0 smoke test caught
    seedance timing out at max_wait because _poll only matched 'success'."""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v1", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v1").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/done.mp4"}}),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("prompt", duration=5.0)
    assert ref.url == "https://cdn/done.mp4"


@respx.mock
@pytest.mark.asyncio
async def test_poll_permanent_4xx_raises_immediately():
    """A1: 401/403/404 等永久 4xx 必须**立即** raise，不能烧满 max_wait 再抛 TimeoutError。

    修前所有非 200（非嵌入 FAILED）都 continue——一个 key 过期（401）的任务要轮满
    600s，最后抛的还是误导性的 TimeoutError（「超时」而不是「鉴权失效」）。
    seedance_ark._poll 早有同类修复（_RETRYABLE_POLL_STATUS），此为 qihu 系对齐。
    """
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v_auth", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v_auth").mock(
        return_value=httpx.Response(401, json={"error": {"code": "401", "message": "invalid api key"}}),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01, max_wait=10.0)
    import time

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="401"):
        await provider.generate_video("test", duration=8.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"401 应在第一个 poll_interval 后立即 raise，不应等到 max_wait（实际 {elapsed:.1f}s）"


@respx.mock
@pytest.mark.asyncio
async def test_poll_retryable_4xx_continues_polling():
    """A1: 408/409/425/429 等带「稍后再试」语义的 4xx 继续轮询（不立即 raise）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v_retry", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v_retry").side_effect = [
        httpx.Response(408, text="request timeout"),
        httpx.Response(200, json={"data": {"status": "success", "url": "https://cdn/done.mp4"}}),
    ]
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("test", duration=8.0)
    assert ref.url == "https://cdn/done.mp4"


@respx.mock
@pytest.mark.asyncio
async def test_poll_5xx_continues_polling():
    """A1: 5xx 是瞬态服务器故障，应继续轮询（修前也 continue，此处防回归）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "v_5xx", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/v_5xx").side_effect = [
        httpx.Response(500, json={"error": {"code": "10183", "message": "服务异常"}}),
        httpx.Response(200, json={"data": {"status": "success", "url": "https://cdn/done.mp4"}}),
    ]
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)
    ref = await provider.generate_video("test", duration=8.0)
    assert ref.url == "https://cdn/done.mp4"

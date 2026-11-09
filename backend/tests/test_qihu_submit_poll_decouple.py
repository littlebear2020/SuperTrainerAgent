"""A3: qihu 系 submit/poll 解耦——throttle 重试只重连 poll 不重新 submit。

qihu_video 新增 submit_video/poll_video 后，throttle.py:351 的 hasattr 分流自动生效。
seedance/happyhorse 子类各自的 _submit 签名不同，须各自 override submit_video。
"""

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef


def test_qihu_video_exposes_submit_poll():
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    assert hasattr(QihuVideoProvider, "submit_video")
    assert hasattr(QihuVideoProvider, "poll_video")


def test_seedance_exposes_submit_poll():
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    assert hasattr(SeedanceVideoProvider, "submit_video")
    assert hasattr(SeedanceVideoProvider, "poll_video")


def test_happyhorse_exposes_submit_poll():
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    assert hasattr(HappyhorseVideoProvider, "submit_video")
    assert hasattr(HappyhorseVideoProvider, "poll_video")


@pytest.mark.asyncio
async def test_throttle_retries_poll_without_resubmit_qihu(monkeypatch):
    """throttle 层在 qihu submit/poll 分离后，重试只重连 poll，submit 仅 1 次。"""
    from app.gateway.video_gen.providers import throttle as throttle_mod
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    # 退避 2**attempt+rand → 0：poll 重试次数断言不变，只是不真睡。
    monkeypatch.setattr(throttle_mod, "_backoff", lambda _attempt: 0.0)

    submits = 0
    polls = 0

    class _MockQihu(QihuVideoProvider):
        async def submit_video(self, prompt, *, duration=8.0, **kwargs) -> str:
            nonlocal submits
            submits += 1
            return "task-1"

        async def poll_video(self, task_id, *, duration=8.0, **kwargs) -> VideoRef:
            nonlocal polls
            polls += 1
            if polls < 3:
                raise TimeoutError("transient")
            return VideoRef(url="http://cdn/done.mp4", duration=duration, source="qihu")

    inner = _MockQihu(api_key="test-key")
    p = ThrottledAIVideoProvider(inner, qps=1000, max_retries=3)
    ref = await p.generate_video("test", duration=8.0)

    assert submits == 1, f"submit 应只调 1 次，实际 {submits}"
    assert polls == 3, f"poll 应重试到成功（3 次），实际 {polls}"
    assert ref.url == "http://cdn/done.mp4"


@respx.mock
@pytest.mark.asyncio
async def test_qihu_submit_poll_equivalent_to_generate():
    """submit_video + poll_video 组合与 generate_video 等价（行为回归）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "task-eq", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/task-eq").mock(
        return_value=httpx.Response(200, json={"data": {"status": "success", "url": "http://cdn/eq.mp4"}}),
    )
    provider = QihuVideoProvider(api_key="test-key", poll_interval=0.01)

    task_id = await provider.submit_video("test prompt", duration=8.0)
    assert task_id == "task-eq"

    ref = await provider.poll_video(task_id, duration=8.0)
    assert ref.url == "http://cdn/eq.mp4"
    assert ref.source == "qihu"
    assert ref.duration == 8.0


@respx.mock
@pytest.mark.asyncio
async def test_seedance_submit_poll_with_image_urls():
    """seedance submit_video 接受 image_urls（r2v）+ role，poll_video 返回正确 source。"""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": "sd-task", "status": "created"}}),
    )
    respx.get("https://api.360.cn/v1/videos/async_generations/sd-task").mock(
        return_value=httpx.Response(200, json={"data": {"status": "success", "url": "http://cdn/sd.mp4"}}),
    )
    provider = SeedanceVideoProvider(api_key="test-key", poll_interval=0.01)

    task_id = await provider.submit_video("cinematic shot", duration=5.0, image_urls=["http://img/ref1.png"], role="reference")
    assert task_id == "sd-task"

    ref = await provider.poll_video(task_id, duration=5.0)
    assert ref.url == "http://cdn/sd.mp4"
    assert ref.source == "seedance"

"""B1 断链修复（2026-08-09 评审）：throttle 成功路径必须把 Ref.cost_cny/source 透传到 sink。

修前 ``_record_provider_call`` 已支持 ``amount`` 参数，但 image/video 全部 6 个
调用点都没传——provider 提取的真账金额到 Ref 就断，``external_api_calls`` 的
``metadata_json.amount`` 生产恒 NULL，console ``media_cost_by_provider`` 恒空。
这是 BUG-B 同型事故（测试全绿生产失效）：test_provider_media_amount 只测
provider→Ref，test_console_media_cost 只测 row→聚合，中间 throttle→sink 无人测。
本文件用真实 Throttled 包裹 + 捕获 sink 调用补上这一环。
"""

import asyncio

import pytest

from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.providers.throttle import (
    ThrottledAIImageProvider,
    ThrottledAIVideoProvider,
)


class _FakeImageInner:
    async def generate_image(self, prompt, **kwargs):  # noqa: ANN001, ANN201
        return ImageRef(url="http://x/1.png", source="gpt-image-2", cost_cny=0.0204, raw_usage={"amount": "0.0204"})


class _FakeVideoInner:
    async def submit_video(self, prompt, *, duration=5.0, **kwargs):  # noqa: ANN001, ANN201
        return "task-1"

    async def poll_video(self, task_id, *, duration=5.0):  # noqa: ANN001, ANN202
        return VideoRef(url="http://x/1.mp4", duration=duration, source="qihu", cost_cny=0.15, raw_usage={"amount": "0.15"})


@pytest.fixture
def captured_sink(monkeypatch):  # noqa: ANN201
    """捕获 record_external_api_call 调用（throttle 在调用点才 import，patch 模块属性有效）。"""
    calls: list[dict] = []

    async def fake_record(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr("app.gateway._shared.llm_usage_sink.record_external_api_call", fake_record)
    return calls


@pytest.mark.asyncio
async def test_image_success_passes_amount_and_real_provider_to_sink(captured_sink):
    t = ThrottledAIImageProvider(_FakeImageInner(), qps=100, max_retries=0)
    ref = await t.generate_image("p")
    assert ref.cost_cny == pytest.approx(0.0204)

    await asyncio.sleep(0)  # flush fire-and-forget create_task
    await asyncio.sleep(0)
    assert len(captured_sink) == 1
    call = captured_sink[0]
    assert call["amount"] == pytest.approx(0.0204), "修前 amount=None——真账断在 throttle 层"
    assert call["provider"] == "gpt-image-2", "sink 应记真实 provider 名（Ref.source），非 pipeline 标签 ai_image"
    assert call["status"] == "ok"


@pytest.mark.asyncio
async def test_video_success_passes_amount_and_real_provider_to_sink(captured_sink):
    t = ThrottledAIVideoProvider(_FakeVideoInner(), qps=100, max_retries=0)
    ref = await t.generate_video("p", duration=5.0)
    assert ref.cost_cny == pytest.approx(0.15)

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(captured_sink) == 1
    call = captured_sink[0]
    assert call["amount"] == pytest.approx(0.15)
    assert call["provider"] == "qihu"
    assert call["status"] == "ok"


@pytest.mark.asyncio
async def test_no_amount_when_ref_has_none(captured_sink):
    """provider 未返回 amount（如 edits 免费端点）→ sink 行 amount=None，不写稀疏键。"""

    class _FreeInner:
        async def generate_image(self, prompt, **kwargs):  # noqa: ANN001, ANN201
            return ImageRef(url="http://x/2.png", source="gpt-image-2")

    t = ThrottledAIImageProvider(_FreeInner(), qps=100, max_retries=0)
    await t.generate_image("p")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(captured_sink) == 1
    assert captured_sink[0]["amount"] is None
    assert captured_sink[0]["provider"] == "gpt-image-2"

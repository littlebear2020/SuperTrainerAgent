"""E9（360 通道切换，2026-08-21）：帧内续写接通（return_last_frame + 尾帧 i2v）。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E9 + T8 实测
（temp/v1_probe/results_tailframe）：
- 透传 ``extra_body.return_last_frame=true``（T8：响应 ``extra_data`` 为 **JSON
  字符串**，``content.last_frame_url`` 即官方尾帧图；``usage`` 也在其中）；
- 对接 ai_video 续写闸：帧内第 2 段以尾帧为首帧走 i2v（**role=None 首帧路径**，
  非 r2v 参考图）；仅限帧内接续，帧与帧之间不链接；
- ``generate_audio: false`` 在续写各段均生效。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

_TAIL_URL = "https://tos/last-frame.png"
_EXTRA_DATA = json.dumps(
    {
        "id": "t1",
        "status": "succeeded",
        "content": {"last_frame_url": _TAIL_URL},
        "usage": {"completion_tokens": 108900, "total_tokens": 108900},
    }
)


def _mock_poll(task_id: str = "e9-1", extra_data: str | None = _EXTRA_DATA) -> None:
    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": task_id, "status": "created"}}),
    )
    data: dict = {"status": "succeeded", "url": "https://cdn/e9.mp4"}
    if extra_data is not None:
        data["extra_data"] = extra_data
    respx.get(f"https://api.360.cn/v1/videos/async_generations/{task_id}").mock(
        return_value=httpx.Response(200, json={"data": data}),
    )


def _posts() -> list[dict]:
    bodies = [json.loads(c.request.content.decode()) for c in respx.calls if c.request.method == "POST"]
    return bodies


def test_seedance_supports_tail_frame_declared_and_passthrough():
    """supports_tail_frame=True 声明 + 穿透 Throttle/Fallback 包裹链（BUG-B 铁律）。"""
    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    p = SeedanceVideoProvider(api_key="k")
    assert p.supports_tail_frame is True
    t = ThrottledAIVideoProvider(p, qps=100.0, max_retries=0)
    assert t.supports_tail_frame is True
    chain = FallbackAIVideoProvider(t, ThrottledAIVideoProvider(MagicMock(), qps=100.0, max_retries=0))
    assert chain.supports_tail_frame is True


@respx.mock
@pytest.mark.asyncio
async def test_return_last_frame_true_when_requested():
    """续写启用时（return_last_frame=True）请求体含 extra_body.return_last_frame=true。"""
    _mock_poll("e9-on")
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await p.generate_video("p", duration=5.0, return_last_frame=True)
    assert _posts()[0]["extra_body"]["return_last_frame"] is True
    assert ref.last_frame_url == _TAIL_URL


@respx.mock
@pytest.mark.asyncio
async def test_return_last_frame_absent_when_not_requested():
    """未启用时无此键（默认路径行为不变）。"""
    _mock_poll("e9-off")
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await p.generate_video("p", duration=5.0)
    assert "return_last_frame" not in _posts()[0]["extra_body"]


@respx.mock
@pytest.mark.asyncio
async def test_extra_data_json_string_parsed_for_last_frame_and_usage():
    """extra_data（JSON 字符串）解析正确提取 last_frame_url；usage tokens 进 raw_usage。"""
    _mock_poll("e9-parse")
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await p.generate_video("p", duration=5.0, return_last_frame=True)
    assert ref.last_frame_url == _TAIL_URL
    assert ref.raw_usage and ref.raw_usage.get("total_tokens") == 108900


@respx.mock
@pytest.mark.asyncio
async def test_extra_data_missing_or_invalid_tolerated():
    """异常容错：extra_data 缺失 / 非 JSON → last_frame_url=None 不炸（回落本地抽帧）。"""
    _mock_poll("e9-missing", extra_data=None)
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await p.generate_video("p", duration=5.0, return_last_frame=True)
    assert ref.last_frame_url is None
    assert ref.url == "https://cdn/e9.mp4"

    _mock_poll("e9-badjson", extra_data="{not-json")
    ref2 = await p.generate_video("p", duration=5.0, return_last_frame=True)
    assert ref2.last_frame_url is None
    assert ref2.url == "https://cdn/e9.mp4"


@respx.mock
@pytest.mark.asyncio
async def test_extend_style_call_maps_to_i2v_without_role():
    """续写第 2 段调用形态（image_url=尾帧 + role='first_frame'）→ 360 归一为
    首帧 i2v（extra_body 无 role 键），generate_audio:false 同段生效。"""
    _mock_poll("e9-i2v")
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await p.generate_video("p", duration=5.0, image_url=_TAIL_URL, role="first_frame", return_last_frame=False)
    body = _posts()[0]
    assert "role" not in body["extra_body"], "首帧 i2v 不得设全局 role（会被当 r2v 参考图）"
    assert any(c.get("type") == "image_url" for c in body["content"]), "尾帧须作为首帧图进 content"
    assert body["extra_body"]["generate_audio"] is False


# ── ai_video 续写闸对接（第 1 段带官方尾帧 → 第 2 段以尾帧为首帧）──────────────


@respx.mock
@pytest.mark.asyncio
async def test_extend_gate_uses_official_tail_as_first_frame(tmp_path):
    """对接：mock 第 1 段产物带尾帧 → 第 2 段请求以尾帧为首帧（i2v）。"""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    respx.get(_TAIL_URL).mock(return_value=httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"x" * 1024))

    provider = MagicMock()
    provider.supports_tail_frame = True
    provider.max_video_duration = 15.0
    provider.min_video_duration = 4.0
    provider.integer_seconds_only = True
    calls: list[dict] = []

    async def _gen(prompt, *, duration=8.0, **kwargs):
        calls.append({"prompt": prompt, "duration": duration, **kwargs})
        if len(calls) == 1:
            return VideoRef(url="http://seg1", duration=8.0, source="seedance", last_frame_url=_TAIL_URL)
        return VideoRef(url="http://seg2", duration=5.0, source="seedance")

    provider.generate_video = _gen
    source = AIVideoVisualSource(provider, concurrency=1, extend_enabled=True, extend_min_gap=3.0)

    frame = StoryboardFrame(index=0, duration=12.0, narration="n", image_prompt="p")
    storyboard = Storyboard(title="t", config=StoryboardConfig(width=1280, height=720, video_type="ai_video"), frames=[frame])
    orig = tmp_path / "visuals" / "shot_0000.mp4"
    orig.parent.mkdir(parents=True, exist_ok=True)
    orig.write_bytes(b"x" * 2048)

    with (
        patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=13.0),
        patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new=AsyncMock(side_effect=lambda url, f, td, dest_name=None: f"visuals/ext_{f.index:04d}.mp4")),
    ):
        ok = await source._try_extend_video(frame, VideoRef(url="http://seg1", duration=8.0, source="seedance", last_frame_url=_TAIL_URL), gap=7.0, task_dir=tmp_path, original_rel="visuals/shot_0000.mp4", storyboard=storyboard)

    assert ok is True
    assert len(calls) == 1, "本测试直接调 _try_extend_video——第 2 段即 calls[0]"
    assert calls[0]["image_url"].endswith(".png"), "第 2 段须以（官方尾帧下载/抽帧产物）为首帧 image_url"
    assert calls[0]["role"] == "first_frame", "调用层语义：首帧（seedance 侧归一为无 role i2v）"


# ── 2026-09-01 计费修复：顶层空 usage 不吞 extra_data 真账 ──────────────────
# 真实 360 seedance 响应顶层恒带 "usage": {}（空对象），真账在 extra_data.usage。
# 修前 _extract_cost 遇空 dict 返 (None, {}) → _video_ref 的「raw_usage is None」
# 回退永不命中 → total_tokens 丢失 → 视频分量记账 ¥0（本地 327/327 行实证）。
# 既有 _mock_poll 响应无顶层 usage 字段，恰好没覆盖真实形态（bug 漏网原因）。


@respx.mock
@pytest.mark.asyncio
async def test_empty_top_level_usage_falls_back_to_extra_data():
    """真实响应形态：顶层 usage={} + extra_data.usage 有 total_tokens → raw_usage 取到真账。"""
    task_id = "e9-empty-usage"
    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": task_id, "status": "created"}}),
    )
    respx.get(f"https://api.360.cn/v1/videos/async_generations/{task_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                # 真实形态：空 usage 在响应**顶层**（与 data 平级）——_poll 返回
                # resp.json() 全 body（qihu_video.py:372），_extract_cost 读顶层。
                "usage": {},
                "data": {
                    "status": "succeeded",
                    "url": "https://cdn/e9.mp4",
                    "extra_data": _EXTRA_DATA,
                },
            },
        ),
    )
    p = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    ref = await p.generate_video("p", duration=5.0)
    assert ref.raw_usage and ref.raw_usage.get("total_tokens") == 108900, "顶层空 usage 必须回退 extra_data.usage——修前空 dict 吞掉回退条件"


def test_extract_cost_empty_dict_treated_as_absent():
    """_extract_cost 形态矩阵：空 {} → (None, None)；含键无 amount 不误伤；amount 正常。"""
    from app.gateway.video_gen.providers.base import _extract_cost

    assert _extract_cost({"usage": {}}) == (None, None), "空 dict 须按无 usage 处理（回退条件才能命中）"
    cost, raw = _extract_cost({"usage": {"total_tokens": 5}})
    assert cost is None and raw == {"total_tokens": 5}, "含键无 amount 的 usage 不得误伤（gpt-image-2 估算链）"
    cost, raw = _extract_cost({"usage": {"amount": "0.020396"}})
    assert cost == 0.020396 and raw == {"amount": "0.020396"}
    assert _extract_cost({}) == (None, None)
    assert _extract_cost({"usage": None}) == (None, None)

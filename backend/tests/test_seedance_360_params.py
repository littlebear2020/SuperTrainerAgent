"""E1（360 通道切换，2026-08-21）：360 seedance 参数透传。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E1 + 实测（temp/v1_probe）：
- ``extra_body.ratio`` 跟随任务参数（T1 实测真实生效；比例一律跟随任务，禁止硬编码）；
- ``extra_body.watermark: false`` 默认写入（T4 实测生效）；
- ``extra_body.generate_audio: false`` 显式写入（T8 实测 360 默认 true，不关会给视频
  带上不需要的 AI 音轨、与 TTS 链路冲突）；
- ``extra_body.resolution`` 接配置（未配置不发）；
- duration ceil 行为不变。
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider


def _mock_ok(task_id: str = "e1-1") -> None:
    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(200, json={"data": {"id": task_id, "status": "created"}}),
    )
    respx.get(f"https://api.360.cn/v1/videos/async_generations/{task_id}").mock(
        return_value=httpx.Response(200, json={"data": {"status": "succeeded", "url": "https://cdn/e1.mp4"}}),
    )


def _last_body() -> dict:
    """最后一次 submit POST 的请求体（poll GET 不计入）。"""
    posts = [c for c in respx.calls if c.request.method == "POST"]
    return json.loads(posts[-1].request.content.decode())


@pytest.mark.parametrize("ratio", ["9:16", "16:9", "1:1"])
@respx.mock
@pytest.mark.asyncio
async def test_ratio_passthrough_into_extra_body(ratio: str):
    """传 ratio 时请求体 extra_body.ratio == ratio（T1 实测 9:16 真实生效 720x1280）。"""
    _mock_ok("e1-ratio")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=8.0, ratio=ratio)
    assert _last_body()["extra_body"]["ratio"] == ratio


@respx.mock
@pytest.mark.asyncio
async def test_ratio_absent_when_not_passed():
    """不传 ratio 时请求体无 extra_body.ratio 键（行为同现状，Ark 路径回归同款）。"""
    _mock_ok("e1-noratio")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=8.0)
    assert "ratio" not in _last_body()["extra_body"]


@respx.mock
@pytest.mark.asyncio
async def test_default_watermark_false_and_generate_audio_false():
    """请求体默认含 watermark: false 与 generate_audio: false（T4/T8 实测）。"""
    _mock_ok("e1-defaults")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=8.0)
    extra = _last_body()["extra_body"]
    assert extra["watermark"] is False
    assert extra["generate_audio"] is False


@respx.mock
@pytest.mark.asyncio
async def test_generate_audio_false_even_with_reference_images():
    """带参考图（r2v）场景 generate_audio: false 同样生效（续写各段口径一致）。"""
    _mock_ok("e1-r2v")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=8.0, image_urls=["https://img/a.png", "https://img/b.png"])
    extra = _last_body()["extra_body"]
    assert extra["generate_audio"] is False
    assert extra["role"] == "reference"  # 既有 role 语义不回归


@respx.mock
@pytest.mark.asyncio
async def test_resolution_from_ctor_when_configured():
    """resolution 接配置：配置时写入 extra_body.resolution。"""
    _mock_ok("e1-res")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01, resolution="720p")
    await provider.generate_video("prompt", duration=8.0)
    assert _last_body()["extra_body"]["resolution"] == "720p"


@respx.mock
@pytest.mark.asyncio
async def test_resolution_absent_when_unset():
    """resolution 未配置（默认 None）时不发该键——交 360 侧默认。"""
    _mock_ok("e1-nores")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=8.0)
    assert "resolution" not in _last_body()["extra_body"]


@respx.mock
@pytest.mark.asyncio
async def test_duration_ceil_unchanged():
    """duration ceil 行为不变（既有用例回归口径）。"""
    _mock_ok("e1-dur")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=7.2)
    assert _last_body()["duration"] == 8
    await provider.generate_video("prompt", duration=5.0)
    assert _last_body()["duration"] == 5


@respx.mock
@pytest.mark.asyncio
async def test_duration_double_written_to_extra_body():
    """时长透传修复（2026-09-01）：extra_body.duration = ceil(请求值)——360 对
    seedance 只认 extra_body.duration（探针 A：顶层 10 → 产出 5.04s 被忽略；
    B/C：extra_body 10/12 → 10.04s/12.04s 生效）。顶层 duration 保留双写
    （通用接口 required 字段，删了有 400 风险）。"""
    _mock_ok("e1-dur2")
    provider = SeedanceVideoProvider(api_key="k", poll_interval=0.01)
    await provider.generate_video("prompt", duration=7.2)
    body = _last_body()
    assert body["duration"] == 8, "顶层 duration 保留双写（既有语义不回归）"
    assert body["extra_body"]["duration"] == 8
    await provider.generate_video("prompt", duration=10.0)
    body = _last_body()
    assert body["duration"] == 10
    assert body["extra_body"]["duration"] == 10
    # 整数秒原样（探针 B/C 口径：extra_body 值 == ceil(请求值)）
    await provider.generate_video("prompt", duration=12.0)
    assert _last_body()["extra_body"]["duration"] == 12


def test_capability_supported_ratios_covers_three_ac_tiers():
    """E1 能力声明对齐：ratio 真实下发后，静态表 supported_ratios 须覆盖 AC3 三档
    （16:9 / 1:1 / 9:16）——修前缺 1:1，前端按能力校验会拒 1:1 任务。"""
    from app.gateway.video_gen.providers.capabilities import video_capability

    ratios = video_capability("seedance").supported_ratios
    for tier in ("16:9", "1:1", "9:16"):
        assert tier in ratios, f"seedance 能力表缺比例 {tier}: {ratios}"

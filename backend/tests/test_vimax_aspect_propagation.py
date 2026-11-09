"""Task 2 (G1 + G15): ViMax 竖屏任务必须出竖屏。

两处独立断点：

1. **生图**：vendored 代码四处硬编码 ``size="1600x900"`` /
   ``Image.new("RGB", (1600, 900))``，作为显式 kwarg 短路掉桥接层正确的
   ``_aspect_to_size``（`provider_bridge.py`: ``size = kwargs.get("size") or
   _aspect_to_size(self._aspect_ratio)``）。

2. **生视频**：``SeedanceVideoGeneratorForViMax`` 组装 ``call_kwargs`` 时**从不放
   ``ratio``**，而 provider 侧 ``seedance_ark.generate_video`` 是支持 ``ratio`` 的
   → Ark 回落自己的 16:9 缺省。

范围提醒：provider 侧（seedance_ark）已修好，**不要重复修**；缺口只在 ViMax bridge。
"""

import inspect
from pathlib import Path

import pytest

VIMAX_PKG = Path(__file__).resolve().parents[1] / "packages" / "vimax" / "vimax"


def test_no_hardcoded_1600x900_in_vendored_vimax():
    """vendored 代码不得再用 1600x900 硬编码短路 _aspect_to_size。"""
    offenders = []
    for path in VIMAX_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]  # 注释里提到旧尺寸（说明修了什么）不算违规
            if "1600x900" in code or "1600, 900" in code:
                offenders.append(f"{path.relative_to(VIMAX_PKG)}:{lineno}: {line.strip()}")
    assert not offenders, "vendored 仍有硬编码 16:9 尺寸：\n" + "\n".join(offenders)


def test_seedream_bridge_uses_aspect_when_no_explicit_size():
    """未显式传 size 时，生图尺寸由 aspect_ratio 决定（9:16 → 1152x2048）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import _aspect_to_size

    assert _aspect_to_size("9:16") == "1152x2048"
    assert _aspect_to_size("16:9") == "2048x1152"
    assert _aspect_to_size(None) == "1152x2048"


def test_video_bridge_accepts_aspect_ratio():
    """ViMax 视频桥必须能接收 aspect_ratio（此前完全没有该状态）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import (
        SeedanceVideoGeneratorForViMax,
    )

    params = inspect.signature(SeedanceVideoGeneratorForViMax.__init__).parameters
    assert "aspect_ratio" in params, "SeedanceVideoGeneratorForViMax 不接受 aspect_ratio —— 生视频永远拿不到用户选的画面比例"


@pytest.mark.asyncio
async def test_video_bridge_forwards_ratio_to_provider():
    """9:16 任务 → provider 收到 ratio='9:16'（当前代码此用例必红）。"""
    from app.gateway.video_gen.engines.vimax import provider_bridge as pb

    captured: dict = {}

    class _FakeProvider:
        async def generate_video(self, prompt, **kwargs):
            captured.update(kwargs)

            class _Ref:
                url = "http://x/v.mp4"
                duration = 5.0

            return _Ref()

    gen = pb.SeedanceVideoGeneratorForViMax(
        api_key="k",
        backend="seedance-ark",
        aspect_ratio="9:16",
    )
    gen._provider = _FakeProvider()

    await gen.generate_single_video("a shot", reference_image_paths=None)

    assert captured.get("ratio") == "9:16", f"生视频未下发 ratio（实收 {captured.get('ratio')!r}）→ Ark 回落 16:9 缺省"


def test_build_vimax_config_wires_video_aspect_ratio():
    """build_vimax_config 必须把画面比例透传到 video_generator.init_args。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import build_vimax_config

    cfg = build_vimax_config(
        working_dir="/tmp/x",
        llm_model="m",
        llm_base_url="http://llm",
        llm_api_key="k",
        image_api_key="k",
        video_api_key="k",
        image_aspect_ratio="9:16",
        video_backend="seedance-ark",
    )

    video_args = cfg["video_generator"]["init_args"]
    assert video_args.get("aspect_ratio") == "9:16", "video_generator.init_args 缺 aspect_ratio —— 生视频侧拿不到用户选的比例"

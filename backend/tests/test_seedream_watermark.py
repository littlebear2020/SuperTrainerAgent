"""P1-E1: seedream 请求体必须显式 watermark=False。

Ark ``watermark`` 字段控制右下角"AI生成"水印（docs/BYTEDANCE_ARK_API_REFERENCE.md:60，
示例显式置 false 关闭）；happyhorse.py:92 已显式关闭，seedream 与 ViMax 桥漏了。
"""

import pytest

from app.gateway.video_gen.engines.vimax import provider_bridge as bridge_mod
from app.gateway.video_gen.providers import seedream as seedream_mod


class _Resp:
    status_code = 200

    def json(self):
        return {"data": [{"url": "http://tos/x.png"}]}


class _Client:
    is_closed = False

    def __init__(self):
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "json": json})
        return _Resp()


@pytest.mark.asyncio
async def test_seedream_provider_disables_watermark():
    """SeedreamImageProvider.generate_image 请求体须含 watermark=False。"""
    client = _Client()
    p = seedream_mod.SeedreamImageProvider(api_key="k")
    p._client = client
    await p.generate_image("a cat", size="1152x2048")
    assert client.calls, "provider must POST"
    assert client.calls[0]["json"]["watermark"] is False


@pytest.mark.asyncio
async def test_vimax_bridge_disables_watermark():
    """SeedreamGeneratorForViMax.generate_single_image 请求体须含 watermark=False。"""
    client = _Client()
    gen = bridge_mod.SeedreamGeneratorForViMax.__new__(bridge_mod.SeedreamGeneratorForViMax)
    gen._api_key = "k"
    gen._model = "doubao-seedream-5-0-pro-260628"
    gen._base_url = "https://ark.example.com/api/v3"
    gen._aspect_ratio = "9:16"
    gen._client = client
    # 跳过预算/成本闸门（本测试只验证 body.watermark）
    gen._max_calls = None
    gen._max_cost_cny = None
    gen._call_count = 0
    gen.cost_cny = 0.0
    await gen.generate_single_image("a cat")
    assert client.calls, "bridge must POST"
    assert client.calls[0]["json"]["watermark"] is False

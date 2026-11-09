"""360-Gemini text-to-image provider via OpenAI-compatible API.

Wire format extracted from trainerai-imagine/scripts/providers/qihu360.ts.
Endpoint: POST {base_url}/v1/images/generations
Response: {"data": [{"url": "..."} | {"b64_json": "..."}]}

E4（2026-08-21 360 主力切换）：本类经 registry 仅服务 ``seedream-360``
（``bytedance/doubao-seedream-5.0-pro``）。据 T3/T4 实测（temp/v1_probe）：
- 参考图能力走 ``POST /v1/images/edits``，参考图经 ``extra_body.image``
  （单图字符串 / 多图数组，官方《图片编辑》口径）；
- 尺寸一律走 ``extra_body.size``（顶层 size 实测被**静默改派**：请求 2048x2048
  实得 2368x1776；extra_body.size 三档精确：2048x1152 / 2048x2048 / 1152x2048）；
- ``extra_body.watermark: false`` 生效；``sequential_image_generation`` 对
  5.0-pro 实测 400，**禁发**。
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .base import ImageRef, _append_request_id, _extract_cost
from .throttle import ProviderUnavailableError, RateLimitError

logger = logging.getLogger(__name__)

# T4 实测的 seedream 精确三档（16:9 / 1:1 / 9:16 的 2K 档）。
_SEEDREAM_SIZE_TIERS: tuple[tuple[int, int], ...] = ((2048, 1152), (2048, 2048), (1152, 2048))


def _map_to_seedream_size(size: str | None) -> str:
    """把任意 WxH 归一到最近的 seedream 三档（按对数比例距离）。

    顶层 size 被静默改派、非三档值不可信（T4），故一律映射到三档再下发。
    解析失败回落 1:1 档（比例中性的保守默认）。
    """
    try:
        w, h = (int(p) for p in str(size).lower().split("x", 1))
        if w <= 0 or h <= 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return "2048x2048"
    import math

    target = math.log(w / h)
    best = min(_SEEDREAM_SIZE_TIERS, key=lambda t: abs(math.log(t[0] / t[1]) - target))
    return f"{best[0]}x{best[1]}"


class QihuImageProvider:
    """360-Gemini text-to-image provider (OpenAI-compatible endpoint)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "google/gemini-3.1-flash-image-preview",
        connect_timeout: float = 15.0,
        read_timeout: float = 240.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = httpx.Timeout(connect_timeout, read=read_timeout, write=30.0, pool=30.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def generate_image(self, prompt: str, **kwargs: Any) -> ImageRef:
        """Generate an image via the 360 gateway.

        E4（2026-08-21）：``reference_image_paths`` 非空时走 ``/v1/images/edits``
        （``extra_body.image`` 承载参考图）；纯文生图走 ``/v1/images/generations``。
        两路尺寸均下发 ``extra_body.size`` 三档映射（顶层 size 实测被静默改派）。
        """
        client = self._get_client()
        model = kwargs.get("model", self._model)
        size = kwargs.get("size", "1152x2048")  # default 9:16 2k
        refs = kwargs.get("reference_image_paths") or []

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        extra_body: dict[str, Any] = {
            "size": _map_to_seedream_size(size),
            "watermark": False,
        }
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            # ⚠️ 顶层不发 size（T4 实测被静默改派）；禁发 sequential_image_generation
            # （对 5.0-pro 实测 400）。
            "extra_body": extra_body,
        }
        if refs:
            # E4：参考图（单图字符串 / 多图数组，官方《图片编辑》口径）。
            normalized = [self._normalize_reference(r) for r in refs]
            extra_body["image"] = normalized[0] if len(normalized) == 1 else normalized
            endpoint = f"{self._base_url}/v1/images/edits"
        else:
            endpoint = f"{self._base_url}/v1/images/generations"

        resp = await client.post(endpoint, headers=headers, json=body)

        if resp.status_code == 429:
            raise RateLimitError(f"360 image API rate limited: {resp.text}")
        # 2026-08-08 P1-1：5xx 是瞬态供应商故障，映射为 ProviderUnavailableError——
        # 进入 Throttle 重试元组与 _FALLBACK_EXC（修前抛裸 RuntimeError，不在任何
        # 重试/降级元组内，一次瞬时 500 即废帧）。4xx 仍是永久错误，不重试不降级。
        if resp.status_code >= 500:
            raise ProviderUnavailableError(_append_request_id(resp, f"qihu {resp.status_code}: {resp.text[:200]}"))
        if resp.status_code != 200:
            raise RuntimeError(_append_request_id(resp, f"360 image API error ({resp.status_code}): {resp.text}"))

        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"360 image API returned empty data: {data}")

        item = items[0]
        url: str | None = None

        if item.get("url"):
            url = item["url"]
        elif item.get("b64_json"):
            url = f"data:image/png;base64,{item['b64_json']}"
        else:
            raise RuntimeError(f"360 image API: no url or b64_json in response: {item}")

        w, h = 0, 0
        try:
            parts = extra_body["size"].split("x")
            w, h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

        cost_cny, raw_usage = _extract_cost(data)
        logger.info("qihu image generated (cost=%.4f)" % (cost_cny or 0))
        return ImageRef(url=url, width=w, height=h, source="qihu", cost_cny=cost_cny, raw_usage=raw_usage)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @staticmethod
    def _normalize_reference(ref: str) -> str:
        """E4：参考图归一——URL/data-URI 直通，本地文件转 base64 data-URI
        （远端 360 抓不到本地路径；与 gpt_image2 同规）。"""
        if ref.startswith(("http://", "https://", "data:")):
            return ref
        if not os.path.exists(ref):
            logger.warning("reference_image_paths entry is neither URL nor existing file: %s", ref)
            return ref
        import base64

        with open(ref, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"

"""ByteDance Ark seedream text-to-image provider (Stage 4.1).

Copy of :class:`QihuImageProvider`'s shape but pointed at the Ark direct endpoint
(``ark.cn-beijing.volces.com/api/v3/images/generations``) with ``Bearer $ARK_API_KEY``
+ the Ark-native model id ``doubao-seedream-5-0-pro-260628``. Error classification
differs from qihu — this is the key to making image fallback work (requirement #4):

- 5xx → :class:`ProviderUnavailableError` (infrastructure → FallbackAIImageProvider
  degrades to qihu on transient server faults).
- 429 → :class:`RateLimitError` (infrastructure → fallback).
- 4xx → :class:`RuntimeError` (auth/content — NOT in ``_FALLBACK_EXC`` → no fallback;
  content-review rejections must surface to the user, not bounce to a different gateway).

The general path now supports image-to-image via the Ark-native ``image:[data_uri_list]``
multi-reference field (Task6), reusing the same Ark path proven in the ViMax bridge
(:class:`SeedreamGeneratorForViMax`, §4.2). ``reference_image_paths`` (non-empty) is
converted to data-URIs and added as ``body["image"]``; empty/None → pure text-to-image.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from .base import ImageRef, _append_request_id, _extract_cost
from .throttle import ProviderUnavailableError, RateLimitError

logger = logging.getLogger(__name__)


def _to_data_uri(path: str | None) -> str | None:
    """Normalize a reference path for the Ark seedream ``image`` field.

    URLs / data-URIs pass through; a local file → ``data:image/png;base64,...``.
    Mirrors ``provider_bridge._to_image_url``（ViMax 桥用同一 Ark ``image:[list]``
    多参考图编辑形式，§4.2 实测可用）。None → None（t2i，不加 image 字段）。
    """
    if path is None:
        return None
    if path.startswith(("http://", "https://", "data:")):
        return path
    if not os.path.exists(path):
        return path  # let the API surface the error with the original value
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class SeedreamImageProvider:
    """ByteDance Ark seedream text-to-image provider (OpenAI-compatible endpoint)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seedream-5-0-pro-260628",
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
        """Generate an image from a text prompt via the Ark seedream API.

        C (ByteDance 接入后修订)：现消费 ``reference_image_paths``——非空时把每个 ref
        经 ``_to_data_uri`` 转 data-URI，加入 body ``image: [data_uri_list]`` 字段
        （Ark 原生多参考图编辑，§4.2 实测可用）。空/None → 不加 image 字段（纯 t2i）。
        沿用 ViMax 桥 ``SeedreamGeneratorForViMax`` 已验证的同一路径。
        """
        client = self._get_client()
        model = kwargs.get("model", self._model)
        size = kwargs.get("size", "1152x2048")  # default 9:16 2k (matches qihu default)

        refs = kwargs.get("reference_image_paths") or []
        image_data_uris = [u for u in (_to_data_uri(p) for p in refs) if u]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            # P1-E1: 显式关闭右下角"AI生成"水印（Ark 默认开启，会烧进成片每一帧；
            # happyhorse.py:92 已显式关闭，此处对齐）。
            "watermark": False,
        }
        if image_data_uris:
            body["image"] = image_data_uris

        resp = await client.post(
            f"{self._base_url}/images/generations",
            headers=headers,
            json=body,
        )

        if resp.status_code == 429:
            raise RateLimitError(f"Ark seedream API rate limited: {resp.text}")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(_append_request_id(resp, f"Ark seedream API server error ({resp.status_code}): {resp.text}"))
        if resp.status_code != 200:
            # 4xx (auth/content) → RuntimeError (NOT in _FALLBACK_EXC → no fallback; requirement #4).
            raise RuntimeError(_append_request_id(resp, f"Ark seedream API error ({resp.status_code}): {resp.text}"))

        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"Ark seedream API returned empty data: {data}")

        item = items[0]
        url: str | None = None
        if item.get("url"):
            url = item["url"]
        elif item.get("b64_json"):
            url = f"data:image/png;base64,{item['b64_json']}"
        else:
            raise RuntimeError(f"Ark seedream API: no url or b64_json in response: {item}")

        w, h = 0, 0
        try:
            parts = size.split("x")
            w, h = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

        cost_cny, raw_usage = _extract_cost(data)
        logger.info("seedream image generated (cost=%.4f)" % (cost_cny or 0))
        return ImageRef(url=url, width=w, height=h, source="seedream", cost_cny=cost_cny, raw_usage=raw_usage)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

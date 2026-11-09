"""360 openai/gpt-image-2 provider — text-to-image + reference-image editing.

Unlike QihuImageProvider (text-only, /v1/images/generations), this provider
also supports reference-image editing via /v1/images/edits with a JSON body
carrying an ``images`` array (URLs or base64 data URLs). This is the
reference-image capability ViMax's character-consistency chain hard-depends
on (plan §0.4 / §决策确认「关键技术发现」).

Wire format note: the exact real-API body shape is confirmed by the 0.0
spike; tests in test_gpt_image2_provider.py pin the provider's behavior.
"""

from __future__ import annotations

import base64
import logging
import math
import os
from typing import Any

import httpx

from .base import ImageRef, _append_request_id, _extract_cost
from .throttle import RateLimitError

logger = logging.getLogger(__name__)

# D2 修复批（2026-09-23）：gpt-image-2.5 系实测可用尺寸白名单——
# temp/360_spike_0923_r2_sizes.py 对 flare/sunburst 双模型实测：
# 1024x1024 / 1280x720 / 1536x1024 通过；800x640 / 540x675 / 520x640 /
# 1920x1080 均 400（remotion_course 六槽位中四个在拒收列）。白名单外尺寸
# 由 _map_size_to_whitelist 就近贴档（长宽比最近 → 面积最近），
# ImageRef.size_mapped_from 记原始尺寸供留痕；v1 gpt-image-2 零变化。
_GPT_IMAGE_25_ALLOWED_SIZES: tuple[tuple[int, int], ...] = (
    (1024, 1024),
    (1280, 720),
    (1536, 1024),
)
_GPT_IMAGE_25_ALLOWED_SIZE_STRS: frozenset[str] = frozenset(f"{w}x{h}" for w, h in _GPT_IMAGE_25_ALLOWED_SIZES)


def _is_gpt_image_25(model: str) -> bool:
    return model.startswith("openai/gpt-image-2.5")


def _map_size_to_whitelist(size: str) -> str:
    """白名单外尺寸 → 就近档：先按长宽比最近（对数距离，几何对称），再按面积最近。

    解析失败的 size 原样返回（交由上游报错，不在此吞异常）。
    """
    try:
        w_str, h_str = size.lower().split("x", 1)
        w, h = int(w_str), int(h_str)
    except (ValueError, IndexError):
        return size
    if w <= 0 or h <= 0:
        return size

    def _key(cand: tuple[int, int]) -> tuple[float, int]:
        cw, ch = cand
        aspect_dist = abs(math.log((w / h) / (cw / ch)))
        return (aspect_dist, abs(cw * ch - w * h))

    cw, ch = min(_GPT_IMAGE_25_ALLOWED_SIZES, key=_key)
    return f"{cw}x{ch}"


class GptImage2Provider:
    """360 openai/gpt-image-2 provider (text-to-image + reference-image edit)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "openai/gpt-image-2",
        quality: str | None = None,
        connect_timeout: float = 15.0,
        read_timeout: float = 300.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        # 2026-09-23 spike：gpt-image-2.5 系 quality 为**必填**（缺省即 400
        # "invalid field: quality"；low/high 实测通过）；gpt-image-2（v1）不传，
        # 保持既有 wire format。档位由 registry 构造分支按 provider 定位配置。
        self._quality = quality
        self._timeout = httpx.Timeout(connect_timeout, read=read_timeout, write=30.0, pool=30.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def generate_image(
        self,
        prompt: str,
        *,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageRef:
        """Generate an image via 360 gpt-image-2.

        D2 (2026-08-09 实测 3/3 成功)：纯文生图走 ``/v1/images/generations``（修前
        强制走 ``/v1/images/edits``，源于 0.0 spike 结论「generations is broken」——
        该结论已被本轮探针证伪）。带参考图时仍走 ``/v1/images/edits``（参考图只能
        走 edits 的 images 数组）。
        """
        client = self._get_client()
        model = kwargs.get("model", self._model)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        refs = reference_image_paths or []
        body: dict[str, Any] = {"model": model, "prompt": prompt}
        # D2 修复批：2.5 系白名单外尺寸贴档（v1 原样透传，wire format 零变化）。
        size = kwargs.get("size")
        effective_size = size
        size_mapped_from: str | None = None
        if size and _is_gpt_image_25(model) and size.lower() not in _GPT_IMAGE_25_ALLOWED_SIZE_STRS:
            effective_size = _map_size_to_whitelist(size)
            size_mapped_from = size
            logger.info("gpt-image-2.5 requested size %s not supported, mapped to %s", size, effective_size)
        if effective_size:
            body["size"] = effective_size
        if self._quality:
            body["quality"] = self._quality

        if refs:
            # Reference images only work with /v1/images/edits (images array)
            body["images"] = [self._normalize_reference(r) for r in refs]
            url = f"{self._base_url}/v1/images/edits"
        else:
            # D2: pure text-to-image uses /v1/images/generations (3/3 success, 2026-08-09)
            url = f"{self._base_url}/v1/images/generations"

        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise RateLimitError(f"360 image API rate limited: {resp.text}")
        if resp.status_code != 200:
            raise RuntimeError(_append_request_id(resp, f"360 image API error ({resp.status_code}): {resp.text}"))

        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise RuntimeError(f"360 image API returned empty data: {data}")
        item = items[0]

        out_url: str | None = None
        if item.get("url"):
            out_url = item["url"]
        elif item.get("b64_json"):
            out_url = f"data:image/png;base64,{item['b64_json']}"
        else:
            raise RuntimeError(f"360 image API: no url or b64_json in response: {item}")

        w, h = 0, 0
        if effective_size:
            try:
                parts = effective_size.split("x")
                w, h = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass

        cost_cny, raw_usage = _extract_cost(data)
        logger.info("gpt-image-2 image generated (cost=%.4f)" % (cost_cny or 0))
        return ImageRef(url=out_url, width=w, height=h, source="gpt-image-2", cost_cny=cost_cny, raw_usage=raw_usage, size_mapped_from=size_mapped_from)

    @staticmethod
    def _normalize_reference(ref: str) -> str:
        """Pass URLs through; convert local file paths to base64 data URLs.

        Per plan: 360 accepts URL or Base64 for reference fields. URLs returned
        by 360 (xstore) can be reused directly; user-uploaded local files must
        be base64-encoded.
        """
        if ref.startswith(("http://", "https://", "data:")):
            return ref
        if not os.path.exists(ref):
            # Not a URL and not an existing file — pass through and let the API reject it.
            logger.warning("reference_image_paths entry is neither URL nor existing file: %s", ref)
            return ref
        with open(ref, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

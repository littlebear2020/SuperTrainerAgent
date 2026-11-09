"""360 Nano-Banana image provider (google/gemini-3-pro-image-preview).

ViMax's native image backend. Holds character identity across reference editing
(top-level ``images`` wire format beats the official ``-edit`` model + ``image_urls``
format). Last validated: 2026-07-06 (two A/B rounds, scripts
``backend/scripts/vimax_image_ab_test.py`` + ``vimax_nano_format_ab_test.py``).
See spec §5 for full A/B details:
``docs/superpowers/specs/2026-07-06-model-consolidation-360-zhinao-design.md``

A/B verdicts:
  1. nano-banana vs gpt-image-2 → nano-banana wins (character consistency).
  2. current wire format (top-level ``images``) vs official 360 doc format
     (``-edit`` model + ``extra_body.image_urls``) → current wins. So we keep
     the current format; do NOT blindly switch to the official ``-edit`` format.

Wire format (confirmed by real-API calls):
  - no reference → POST /v1/images/generations + extra_body.generation_config
    (Nano-Banana requires generation_config per 360 spec for text-to-image)
  - with reference → POST /v1/images/edits + images:[url|b64] (i2i editing)

Reference fields accept URL or Base64; 360-returned xstore URLs reuse
directly, user-uploaded local files are base64-encoded.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from .base import ImageRef, _append_request_id, _extract_cost
from .throttle import RateLimitError

logger = logging.getLogger(__name__)


class NanoBananaEmptyResponseError(RuntimeError):
    """360 returned a 200 with no image data (empty `data` or no url/b64_json).

    Transient — observed intermittently under ViMax's high-frequency portrait
    generation. Retryable; distinct from real errors (4xx/5xx) which should NOT
    retry.
    """


# Transient transport errors that warrant a retry at the provider level (cheaper
# than escalating to a full-pipeline retry). "Server disconnected without sending
# a response" is httpx.RemoteProtocolError — the 360 image API drops connections
# under ViMax's sustained load. Retrying here (2s backoff, 3 attempts) absorbs
# the transient without failing the whole render.
_RETRYABLE_TRANSPORT_ERRORS = (
    NanoBananaEmptyResponseError,
    httpx.RemoteProtocolError,  # "Server disconnected without sending a response"
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    TimeoutError,
)


class NanoBananaImageProvider:
    """360 google/gemini-3-pro-image-preview (Nano-Banana) — ViMax's native backend."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "google/gemini-3-pro-image-preview",
        connect_timeout: float = 15.0,
        read_timeout: float = 300.0,
        aspect_ratio: str = "1:1",
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        # Image output shape. vimax real-run vg_8159cd6cc3fa came back 768x768
        # (square) despite a 16:9 request: this was hardcoded "1:1" in extra_body.
        # i2v (Hailuo) then inherited the square first frame. Now configurable.
        self._aspect_ratio = aspect_ratio
        self._timeout = httpx.Timeout(connect_timeout, read=read_timeout, write=30.0, pool=30.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        retry=retry_if_exception_type(_RETRYABLE_TRANSPORT_ERRORS),
        reraise=True,
    )
    async def generate_image(
        self,
        prompt: str,
        *,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageRef:
        client = self._get_client()
        model = kwargs.get("model", self._model)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        refs = reference_image_paths or []
        # Shared generation_config (aspect ratio etc.) — used by BOTH paths. The i2i
        # edits path needs aspectRatio too: first frames (i2i) otherwise stay square
        # (1024x1024) even with aspect_ratio set, and Hailuo i2v then inherits the
        # square. Rerun vg_0362585808e1 confirmed this (portraits went 16:9, first
        # frames stayed 1:1 because only the text-to-image path carried aspectRatio).
        image_config = {
            "aspectRatio": kwargs.get("aspect_ratio", self._aspect_ratio),
            "imageSize": "1K",
            "personGeneration": "ALLOW_ALL",
        }
        if refs:
            # i2i editing — /v1/images/edits + images array (confirmed working for Nano-Banana)
            body: dict[str, Any] = {
                "model": model,
                "prompt": prompt,
                "images": [self._normalize_reference(r) for r in refs],
                "extra_body": {"generation_config": {"imageConfig": image_config}},
            }
            url = f"{self._base_url}/v1/images/edits"
        else:
            # text-to-image — /v1/images/generations + extra_body.generation_config
            # (Nano-Banana requires generation_config per 360 spec)
            body = {
                "model": model,
                "prompt": prompt,
                "extra_body": {"generation_config": {"imageConfig": image_config}},
            }
            url = f"{self._base_url}/v1/images/generations"

        resp = await client.post(url, headers=headers, json=body)
        if resp.status_code == 429:
            raise RateLimitError(f"360 image API rate limited: {resp.text}")
        if resp.status_code != 200:
            raise RuntimeError(_append_request_id(resp, f"360 image API error ({resp.status_code}): {resp.text}"))

        data = resp.json()
        items = data.get("data", [])
        if not items:
            raise NanoBananaEmptyResponseError(f"360 image API returned empty data: {data}")
        item = items[0]

        out_url: str | None = None
        if item.get("url"):
            out_url = item["url"]
        elif item.get("b64_json"):
            out_url = f"data:image/png;base64,{item['b64_json']}"
        else:
            raise NanoBananaEmptyResponseError(f"360 image API: no url or b64_json in response: {item}")

        cost_cny, raw_usage = _extract_cost(data)
        logger.info("nano-banana image generated (cost=%.4f)" % (cost_cny or 0))
        return ImageRef(url=out_url, width=0, height=0, source="nano-banana", cost_cny=cost_cny, raw_usage=raw_usage)

    @staticmethod
    def _normalize_reference(ref: str) -> str:
        """Pass URLs through; convert local file paths to base64 data URLs."""
        if ref.startswith(("http://", "https://", "data:")):
            return ref
        if not os.path.exists(ref):
            logger.warning("reference_image_paths entry is neither URL nor existing file: %s", ref)
            return ref
        with open(ref, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

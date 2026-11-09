"""DashScope AI image generation provider.

Uses Alibaba Cloud DashScope (通义万相) API for text-to-image generation.
API docs: https://help.aliyun.com/document_detail/2712195.html
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import ImageRef, _append_request_id

logger = logging.getLogger(__name__)

_DASHSCOPE_API = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"


class DashScopeImageProvider:
    """DashScope text-to-image provider."""

    def __init__(self, *, api_key: str, model: str = "wanx-v1", size: str = "1024*1024") -> None:
        self._api_key = api_key
        self._model = model
        self._size = size
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120)
        return self._client

    async def generate_image(self, prompt: str, **kwargs: Any) -> ImageRef:
        """Generate an image from text prompt via DashScope.

        DashScope uses an async task model: submit → poll → get result URL.
        """
        client = self._get_client()
        model = kwargs.get("model", self._model)
        size = kwargs.get("size", self._size)

        # Submit task
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        body = {
            "model": model,
            "input": {"prompt": prompt},
            "parameters": {"size": size, "n": 1},
        }

        resp = await client.post(_DASHSCOPE_API, headers=headers, json=body)
        if resp.status_code == 429:
            from .throttle import RateLimitError

            raise RateLimitError(f"DashScope rate limited: {resp.text}")
        if resp.status_code != 200:
            raise RuntimeError(_append_request_id(resp, f"DashScope submit failed: {resp.status_code} {resp.text}"))

        data = resp.json()
        task_id = data.get("output", {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope no task_id in response: {data}")

        # Poll for result
        import asyncio

        task_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        poll_headers = {"Authorization": f"Bearer {self._api_key}"}

        for _ in range(60):  # max 60 * 5s = 300s
            await asyncio.sleep(5)
            poll_resp = await client.get(task_url, headers=poll_headers)
            if poll_resp.status_code != 200:
                continue
            poll_data = poll_resp.json()
            status = poll_data.get("output", {}).get("task_status", "")

            if status == "SUCCEEDED":
                results = poll_data.get("output", {}).get("results", [])
                if results:
                    url = results[0].get("url", "")
                    return ImageRef(url=url, source="dashscope")
                raise RuntimeError("DashScope succeeded but no results")

            if status == "FAILED":
                msg = poll_data.get("output", {}).get("message", "unknown error")
                raise RuntimeError(f"DashScope task failed: {msg}")

        raise TimeoutError("DashScope task timed out after 300s")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

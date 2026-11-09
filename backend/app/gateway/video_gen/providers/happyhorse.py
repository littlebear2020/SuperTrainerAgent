"""alibaba happyhorse async video generation (写实人物后端).

Same 360 submit+poll endpoint as Hailuo/seedance (POST /v1/videos/async_generations
+ GET poll), but a DIFFERENT request body: everything lives in ``extra_body.input``
+ ``extra_body.parameters`` (no top-level content/duration). Status returns UPPERCASE
``SUCCEEDED``; the shared ``_poll`` lowercases and the ``succeeded`` acceptance covers it.

Why happyhorse: it accepts REALISTIC PERSON content that doubao-seedance rejects
(``InputImageSensitiveContentDetected.PrivacyInformation``). Live-verified: a realistic
detective portrait (the exact image seedance blocked) → happyhorse i2v → 3.2MB mp4.
So happyhorse is the vimax video backend for realistic/character content; seedance
handles cartoon/anime. See docs/notes (decision: 写实→happyhorse, 卡通→seedance).

Subclasses ``QihuVideoProvider`` to reuse the verified poll loop + httpx client +
error classification; only ``generate_video`` / ``_submit`` are overridden for the
happyhorse body shape. i2v (first_frame) when an image is supplied, else t2v.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import VideoRef, _append_request_id, _extract_cost
from .qihu_video import QihuServerError, QihuVideoProvider
from .seedream import _to_data_uri
from .throttle import RateLimitError

logger = logging.getLogger(__name__)


class HappyhorseVideoProvider(QihuVideoProvider):
    """alibaba happyhorse i2v/t2v video generation (submit + poll), same endpoint."""

    # ── T3（2026-08-18）：图生视频能力声明——实际支持 media:[{type:first_frame}]
    # 单图 i2v（官方 create-video-generation.md 示例3）但修前类上无声明，ai_video
    # 的 supports_image_urls 闸门误判「不支持」。类属性遮蔽基类 H3 门控 property。
    supports_image_urls = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "alibaba/happyhorse-1.0-i2v",
        t2v_model: str = "alibaba/happyhorse-1.0-t2v",
        poll_interval: float = 15.0,
        max_wait: float = 600.0,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            poll_interval=poll_interval,
            max_wait=max_wait,
            timeout=timeout,
        )
        self._t2v_model = t2v_model

    async def generate_video(
        self,
        prompt: str,
        *,
        duration: float = 5.0,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> VideoRef:
        """i2v when ``image_url`` is supplied (first_frame); t2v otherwise.

        T3（2026-08-18）：兼容复数 ``image_urls``（ai_video.py 调用口径）——取
        ``[0]`` 映到单数；修前签名是单数具名参数，复数被 ``**kwargs`` 静默吞。
        """
        if image_urls:
            image_url = self._first_frame_only(image_urls)
        task_id = await self._submit(prompt, duration=duration, image_url=image_url)
        video_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="happyhorse", cost_cny=cost_cny, raw_usage=raw_usage)

    async def submit_video(
        self,
        prompt: str,
        *,
        duration: float = 5.0,
        image_url: str | None = None,
        image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """A3: 只提交——happyhorse 的 _submit 签名与基类不同（无 ratio）。

        T3（2026-08-18）：复数 image_urls 与 generate_video 同口径（取 [0]）。"""
        if image_urls:
            image_url = self._first_frame_only(image_urls)
        return await self._submit(prompt, duration=duration, image_url=image_url)

    async def poll_video(self, task_id: str, *, duration: float = 5.0, **kwargs: Any) -> VideoRef:
        """A3: 只轮询已提交的任务。"""
        video_url, poll_body = await self._poll(task_id)
        cost_cny, raw_usage = _extract_cost(poll_body)
        return VideoRef(url=video_url, duration=duration, source="happyhorse", cost_cny=cost_cny, raw_usage=raw_usage)

    async def _submit(
        self,
        prompt: str,
        *,
        duration: float,
        image_url: str | None,
    ) -> str:
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # happyhorse body: everything in extra_body.input/parameters (no top-level
        # content/duration). i2v uses media:[{type:first_frame, url}]; t2v omits media.
        input_obj: dict[str, Any] = {"prompt": prompt}
        model = self._model
        if image_url:
            # T3（2026-08-18）：data-URI 归一——本地路径（预览图/角色图）远端 360
            # 抓不到（官方口径：url 须可访问）。是否接受 data-URI 按探针 3 结论
            # 回填计划（backend/docs/360-api/ create 文档 happyhorse 示例只示 URL）。
            input_obj["media"] = [{"type": "first_frame", "url": _to_data_uri(image_url)}]
        else:
            model = self._t2v_model
        body = {
            "model": model,
            "extra_body": {
                "input": input_obj,
                "parameters": {"resolution": "720P", "duration": int(duration), "watermark": False},
            },
        }
        resp = await client.post(
            f"{self._base_url}/v1/videos/async_generations",
            headers=headers,
            json=body,
        )
        if resp.status_code == 429:
            raise RateLimitError(f"360 happyhorse API rate limited: {resp.text}")
        if resp.status_code != 200:
            msg = f"360 happyhorse submit failed ({resp.status_code}): {resp.text}"
            msg = _append_request_id(resp, msg)
            if resp.status_code >= 500:
                raise QihuServerError(msg)
            raise RuntimeError(msg)
        data = resp.json()
        task_data = data.get("data", {})
        task_id = task_data.get("id")
        if not task_id:
            raise RuntimeError(f"360 happyhorse API: no task id in response: {data}")
        logger.info("Happyhorse task submitted: %s", task_id)
        return task_id

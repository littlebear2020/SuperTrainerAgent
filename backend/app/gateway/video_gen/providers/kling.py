"""Kling AI video generation provider — **未接线**（2026-08-03 Task 7）。

Uses Kuaishou Kling API for text-to-video generation.
This is a Phase 3 stub — actual API integration requires Kling API credentials.

⚠️ 本模块**不在 registry 接线中**：``KNOWN_VIDEO_PROVIDERS`` / ``_VIDEO_PROVIDER_KEY`` /
``_VIDEO_PROVIDER_LABELS`` / ``_try_make_ai_video_provider`` 四处已于 Task 7 摘除。
修前配了 ``kling_api_key`` 就报 ``available=True``，用户一选就 NotImplementedError，
而 fallback 也救不了（NotImplementedError 不在 ``_FALLBACK_EXC``）。
文件保留作未来接入骨架——真正接入时：先补实现 + 单测，再把四处接线加回去
（``tests/test_provider_registry_no_phantoms.py`` 会拦住「只加名单不加实现」）。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import VideoRef

logger = logging.getLogger(__name__)


class KlingVideoProvider:
    """Kling text-to-video provider.

    The Kling API uses an async task model similar to DashScope:
    submit creation task → poll for completion → get video URL.

    Full API spec: https://platform.kuaishou.com/docs
    """

    def __init__(self, *, api_key: str, api_secret: str = "") -> None:
        self._api_key = api_key
        self._api_secret = api_secret

    async def generate_video(self, prompt: str, *, duration: float = 5.0, **kwargs: Any) -> VideoRef:
        """Generate video from text prompt.

        This is a stub implementation. The full implementation would:
        1. Submit creation task to Kling API
        2. Poll for completion (task_id based)
        3. Return the video URL

        For now, raises NotImplementedError with guidance.
        """
        raise NotImplementedError("Kling video generation requires valid API credentials. Configure kling_api_key in config.yaml video_gen section.")

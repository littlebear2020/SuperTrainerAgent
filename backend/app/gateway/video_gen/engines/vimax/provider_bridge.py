"""ViMax provider bridge (阶段 2.3, plan §5.3 + Stage 4.2/5.2).

Adapts TrainerAI's providers to ViMax's ``ImageGenerator`` / ``VideoGenerator``
Protocols (vendored ``packages/vimax``), so the ViMax pipeline uses TrainerAI
backends instead of ViMax's own yunwu/google generators. ``RenderBackend`` loads
these classes via ``class_path`` + ``init_args`` from the config built by
:func:`build_vimax_config`.

Backends (C5 2026-10-12 / current state):
- image → 360-gateway seedream 5.0 Pro (``Seedream360GeneratorForViMax``, C5b —
  wraps ``QihuImageProvider``: 参考图 /v1/images/edits + extra_body.image、
  extra_body.size 三档、watermark=false). Ark-direct ``SeedreamGeneratorForViMax``
  保留为回滚位 (``vimax_image_backend: ark``); ``NanoBananaGeneratorForViMax``
  stays defined as a legacy rollback fallback. 360 侧不认字节"信任产物"机制——
  人物参考图拦截风险以 C5c 角色 spike 实测为准.
- video → 360 seedance r2v (``SeedanceVideoProvider`` via ``SeedanceVideoGeneratorForViMax``,
  default since C5a). Ark-direct seedance-ark 保留为回滚位 (``vimax_video_backend:
  seedance-ark``); hailuo/happyhorse explicit fallback backends. The bridge does NOT
  accept真实人物照片 (only 模型产物 / asset://); real-person photos still trip 人脸审查.

Known seam: ViMax passes reference frames as local file paths; the video API wants
a URL. ``_to_image_url`` converts local files to ``data:image/png;base64`` URIs
(mirrors ``NanoBananaImageProvider._normalize_reference``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from vimax.interfaces.image_output import ImageOutput
from vimax.interfaces.video_output import VideoOutput

from app.gateway.video_gen.pricing import IMAGE_UNIT_COST_CNY as _IMAGE_UNIT_COST_CNY
from app.gateway.video_gen.pricing import VIDEO_UNIT_COST_CNY as _VIDEO_UNIT_COST_CNY
from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.providers.content_review import (
    is_content_review_rejection as _shared_is_content_review_rejection,
)
from app.gateway.video_gen.providers.nano_banana import NanoBananaImageProvider
from app.gateway.video_gen.providers.qihu_image import QihuImageProvider
from app.gateway.video_gen.providers.qihu_video import QihuServerError, QihuVideoProvider
from app.gateway.video_gen.providers.throttle import ProviderUnavailableError, RateLimitError


def _resolve_image_unit_cost(provider: str) -> float:
    """Read per-image unit cost from the media_pricing cache, fallback to hardcoded constant.

    C3 constraint: DB failure must NOT block generation — always returns a usable price.
    per_image can be pre-charged (the count is known before generation).
    """
    try:
        from app.gateway.routers.console import _load_media_pricing_sync

        pricing = _load_media_pricing_sync()
        entry = pricing.get((provider, "image"))
        if entry and entry.get("unit") == "per_image":
            return entry["unit_price"]
    except Exception:
        pass
    return _IMAGE_UNIT_COST_CNY


def _resolve_video_unit_cost(provider: str) -> float:
    """Read per-second unit cost from the media_pricing cache, fallback to hardcoded constant.

    C3 constraint: per_kilo_token cannot be pre-charged (token count unknown
    before generation) → always falls back to the hardcoded constant for the
    budget gate. per_second can be pre-charged if duration is known.
    """
    try:
        from app.gateway.routers.console import _load_media_pricing_sync

        pricing = _load_media_pricing_sync()
        entry = pricing.get((provider, "video"))
        if entry and entry.get("unit") == "per_second":
            return entry["unit_price"]
    except Exception:
        pass
    return _VIDEO_UNIT_COST_CNY


_BRIDGE_MODULE = "app.gateway.video_gen.engines.vimax.provider_bridge"

logger = logging.getLogger(__name__)


async def _track_bridge_call(provider: str, method: str, status: str, elapsed: float = 0.0, *, model: str | None = None) -> None:
    """F1: record external API call for ViMax bridge (bypasses throttle).

    C1: ``model`` is passed through as metadata for future per-model pricing.
    """
    try:
        from app.gateway._shared.llm_usage_sink import record_external_api_call

        extra: dict[str, Any] | None = {"model": model} if model else None
        await record_external_api_call(provider=provider, method=method, status=status, elapsed_ms=int(elapsed * 1000) if elapsed else None, extra=extra)
    except Exception:
        pass  # tracking must never break business flow


# Process-wide cap on concurrent 360 image/video API calls from ViMax. ViMax gathers
# all shots' frame + video generation concurrently; without a cap this hammers the
# 360 gateway → per-shot slowdown (vg_00f494b2292a hit happyhorse 600s timeouts under
# load) and rate limiting. Lazy, module-level so the image + video generators share
# one cap. Sized from VideoGenConfig.vimax_api_concurrency on first build_vimax_config.
_API_SEM: asyncio.Semaphore | None = None
_API_SEM_LIMIT = 3


def _api_semaphore() -> asyncio.Semaphore:
    global _API_SEM
    if _API_SEM is None:
        _API_SEM = asyncio.Semaphore(max(1, _API_SEM_LIMIT))
    return _API_SEM


def _to_image_url(path: str | None) -> str | None:
    """Normalize a ViMax reference path for the 360 video API's ``image_url``.

    ViMax saves first frames / portraits as LOCAL files (ImageOutput.save), but the
    360 video API wants a URL. Mirror ``NanoBananaImageProvider._normalize_reference``:
    URLs / data-URIs pass through; a local file becomes a ``data:image/png;base64``
    URI. Returns ``None`` for ``None`` (text-to-video, no first frame).
    """
    if path is None:
        return None
    if path.startswith(("http://", "https://", "data:")):
        return path
    if not os.path.exists(path):
        return path  # let the provider surface the error with the original value
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class VimaxBudgetExceeded(RuntimeError):
    """Raised when a ViMax generator exceeds its per-task call budget (阶段 4.5).

    The image/video API calls are vimax's main cost; capping them mid-pipeline is
    the meaningful hard-cutoff (LLM-call cost tracking is a separate gap).
    """


# Unit costs (CNY per call) per plan §8.3. Used for the ¥ budget hard-cutoff (4.5).
# P1-8: 收口到 pricing.py，与 visual_factory.py 共用同一常量

# 360's Green net check rejects input images it flags as inappropriate content
# (real failure: vg_86d2767fe762 — disaster/dramatic ViMax first-frames tripped
# the image review). Match the failure_reason so the bridge can fall back to t2v.
# Stage 5.2: extended to also match Ark-direct content-review messages
# (InputImageSensitiveContentDetected → "sensitive content" in the RuntimeError).
# 2026-08-03 Task 12：正则与判定函数已搬到 ``providers/content_review.py``
# 供 ai_video 复用（那边要在审核拒绝时换中性化提示词重试）。此处保留原私名别名，
# 使既有调用点零改动；两处共用同一份正则，不会再漂移。
_is_content_review_rejection = _shared_is_content_review_rejection


class NanoBananaGeneratorForViMax:
    """ViMax ``ImageGenerator`` backed by TrainerAI's ``NanoBananaImageProvider``."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "google/gemini-3-pro-image-preview",
        max_calls: int | None = None,
        max_cost_cny: float | None = None,
        aspect_ratio: str | None = None,
        **_: Any,
    ) -> None:
        # aspect_ratio=None → let the provider default (1:1). When set (e.g. "16:9")
        # the image output shape changes, which Hailuo i2v then inherits — fixing the
        # vimax real-run vg_8159cd6cc3fa square-output regression.
        provider_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url, "model": model}
        if aspect_ratio is not None:
            provider_kwargs["aspect_ratio"] = aspect_ratio
        self._provider = NanoBananaImageProvider(**provider_kwargs)
        self._aspect_ratio = aspect_ratio
        self._max_calls = max_calls
        self._max_cost_cny = max_cost_cny
        self._call_count = 0
        self.cost_cny = 0.0

    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageOutput:
        # ``reference_image_paths`` is optional: ViMax's ``generate_front_portrait``
        # (character_portraits_generator.py) calls this with ONLY ``prompt=`` — the
        # first portrait has no reference image. ViMax's own Protocol lists it as the
        # 2nd positional arg, but its real callers omit it, so it MUST default. See
        # plan §2.3 ("bridge accepts the no-reference call shape").
        if self._max_calls is not None and self._call_count >= self._max_calls:
            raise VimaxBudgetExceeded(f"image generation budget exceeded: {self._call_count}/{self._max_calls} calls")
        _img_cost = _resolve_image_unit_cost("nano-banana")
        if self._max_cost_cny is not None and self.cost_cny + _img_cost > self._max_cost_cny:
            raise VimaxBudgetExceeded(f"image generation cost exceeded: ¥{self.cost_cny:.2f}+{_img_cost} > ¥{self._max_cost_cny:.2f}")
        self._call_count += 1
        self.cost_cny += _img_cost
        generate_kwargs: dict[str, Any] = {"reference_image_paths": reference_image_paths or None}
        if self._aspect_ratio is not None:
            generate_kwargs["aspect_ratio"] = self._aspect_ratio
        async with _api_semaphore():
            _nano_start = time.monotonic()
            try:
                ref: ImageRef = await self._provider.generate_image(
                    prompt,
                    **generate_kwargs,
                    **kwargs,
                )
                await _track_bridge_call("nano_banana", "generate_image", "success", time.monotonic() - _nano_start, model="nano-banana")
            except Exception:
                await _track_bridge_call("nano_banana", "generate_image", "error", time.monotonic() - _nano_start, model="nano-banana")
                raise
        return ImageOutput(fmt="url", ext="png", data=ref.url)


# Task 13: ViMax aspect_ratio → seedream `size` (WxH)。seedream 用 size 字段表尺寸（非
# aspect_ratio）；原 NanoBanana 桥透传 aspect_ratio 给 provider extra_body.aspectRatio。
# 水平 16:9 → 2048x1152（与 9:16 默认 1152x2048 镜像，保 ~2k 像素预算）。
# 注：尺寸系从 9:16 默认镜像而来；实现时核实 Ark seedream 5.0 Pro 接受的 size 集合。
_ASPECT_TO_SIZE: dict[str, str] = {
    "16:9": "2048x1152",
    "9:16": "1152x2048",
    "1:1": "1152x1152",
    "horizontal": "2048x1152",
    "vertical": "1152x2048",
    "square": "1152x1152",
}


def _aspect_to_size(aspect_ratio: str | None) -> str:
    """ViMax aspect_ratio → seedream ``size``。None/未知 → 默认 9:16 (1152x2048)。"""
    if not aspect_ratio:
        return "1152x2048"
    return _ASPECT_TO_SIZE.get(aspect_ratio, "1152x2048")


class SeedreamGeneratorForViMax:
    """ViMax ``ImageGenerator`` backed by ByteDance Ark seedream 5.0 Pro (阶段 4.2).

    Switches ViMax image generation from the hardcoded NanoBanana (v3.4 A/B verdict)
    to Ark-direct seedream, preserving the character-consistency multi-reference
    mechanism via the ``image: [b64_data_uri_list]`` body field — the Ark-native
    multi-reference editing path (NOT ``reference_image``; NOT ``/images/edits``
    which 404s — Stage 0 实测). Empty/None refs → no ``image`` field (text-only t2i).

    角色首帧 (front portrait) generated by this class is a 字节"信任产物" — safe
    as Stage 5.2 seedance i2v input (not blocked by 人脸审查, §0.4). This does NOT
    mean the bridge accepts 真实人物照片 as reference — real-person photos still
    trip 人脸审查 (service.py:447-451); the trust-product rule only covers
    Seedream-generated outputs fed forward to seedance.

    Error classification mirrors :class:`SeedreamImageProvider` (§4.1):
    429→:class:`RateLimitError`, 5xx→:class:`ProviderUnavailableError`,
    4xx→:class:`RuntimeError`. :class:`NanoBananaGeneratorForViMax` stays defined as
    a rollback fallback (revert ``build_vimax_config``'s image class_path to switch
    back). See ``backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md`` §阶段 4.2 + §1.7.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seedream-5-0-pro-260628",
        max_calls: int | None = None,
        max_cost_cny: float | None = None,
        aspect_ratio: str | None = None,
        connect_timeout: float = 15.0,
        read_timeout: float = 240.0,
        **_: Any,
    ) -> None:
        # aspect_ratio is accepted (so build_vimax_config's image_aspect_ratio wiring
        # doesn't break construction). Seedream uses the `size` body field for
        # dimensions — Now honored via _aspect_to_size (Task 13). The NanoBanana bridge
        # forwards aspect_ratio to its provider; seedream maps it to `size`. Default
        # aspect (None) → 1152x2048 (9:16), matching ViMax's vertical-video default.
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._aspect_ratio = aspect_ratio
        self._max_calls = max_calls
        self._max_cost_cny = max_cost_cny
        self._call_count = 0
        self.cost_cny = 0.0
        self._timeout = httpx.Timeout(connect_timeout, read=read_timeout, write=30.0, pool=30.0)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageOutput:
        # ``reference_image_paths`` is optional: ViMax's ``generate_front_portrait``
        # (character_portraits_generator.py) calls this with ONLY ``prompt=`` — the
        # first portrait has no reference image. Mirror NanoBananaGeneratorForViMax's
        # contract (plan §2.3 "bridge accepts the no-reference call shape").
        if self._max_calls is not None and self._call_count >= self._max_calls:
            raise VimaxBudgetExceeded(f"image generation budget exceeded: {self._call_count}/{self._max_calls} calls")
        _seedream_cost = _resolve_image_unit_cost("seedream")
        if self._max_cost_cny is not None and self.cost_cny + _seedream_cost > self._max_cost_cny:
            raise VimaxBudgetExceeded(f"image generation cost exceeded: ¥{self.cost_cny:.2f}+{_seedream_cost} > ¥{self._max_cost_cny:.2f}")
        self._call_count += 1
        self.cost_cny += _seedream_cost

        # Convert reference_image_paths → base64 data-URI list via _to_image_url (the
        # same helper the video side uses for local-file → data-URL). ViMax passes
        # LOCAL file paths (saved portraits); _to_image_url turns them into
        # ``data:image/png;base64,...`` URIs the Ark ``image`` field expects.
        # Empty/None → no ``image`` field (text-only t2i, §4.2).
        refs = reference_image_paths or []
        image_data_uris = [u for u in (_to_image_url(p) for p in refs) if u]
        # Task 13: honor aspect_ratio (was hardcoded 9:16, breaking 16:9 ViMax → vertical
        # first frames → seedance-ark i2v inherited vertical). 显式 size kwarg 优先。
        size = kwargs.get("size") or _aspect_to_size(self._aspect_ratio)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # Minimal body matching the verified-working §4.1 SeedreamImageProvider form.
        # NOTE: seedream 5.0 Pro rejects `sequential_image_generation` (HTTP 400
        # InvalidParameter "not supported by the current model") + the extra
        # output_format/response_format params are unneeded (defaults return a TOS URL).
        # P1-E1: watermark 不属"unneeded"——它控制右下角"AI生成"水印（Ark 默认开，烧进成片
        # 每一帧），与 URL 格式无关；显式 False 与 SeedreamImageProvider/happyhorse 对齐。
        body: dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "size": size,
            "watermark": False,
        }
        if image_data_uris:
            body["image"] = image_data_uris

        client = self._get_client()
        _bridge_start = time.monotonic()
        # C5: wrap client.post + resp.json() to add error tracking for unhandled
        # network/decode exceptions (httpx.ConnectError etc. are NOT RuntimeError
        # subclasses → would escape without tracking). Re-raise after tracking.
        async with _api_semaphore():
            try:
                resp = await client.post(
                    f"{self._base_url}/images/generations",
                    headers=headers,
                    json=body,
                )
            except Exception:
                _bridge_elapsed = time.monotonic() - _bridge_start
                await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
                raise
        _bridge_elapsed = time.monotonic() - _bridge_start

        # Error classification mirrors SeedreamImageProvider (§4.1): 429→RateLimit,
        # 5xx→ProviderUnavailable (infrastructure → fallback), 4xx→RuntimeError (auth/
        # content — NOT fallback; content-review rejections must surface to the user).
        if resp.status_code == 429:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise RateLimitError(f"Ark seedream (ViMax) API rate limited: {resp.text}")
        if resp.status_code >= 500:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise ProviderUnavailableError(f"Ark seedream (ViMax) API server error ({resp.status_code}): {resp.text}")
        if resp.status_code != 200:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise RuntimeError(f"Ark seedream (ViMax) API error ({resp.status_code}): {resp.text}")

        try:
            data = resp.json()
        except Exception:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise
        items = data.get("data", [])
        if not items:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise RuntimeError(f"Ark seedream (ViMax) API returned empty data: {data}")
        item = items[0]
        url: str | None = None
        if item.get("url"):
            url = item["url"]
        elif item.get("b64_json"):
            url = f"data:image/png;base64,{item['b64_json']}"
        else:
            await _track_bridge_call("seedream", "generate_image", "error", _bridge_elapsed, model=self._model)
            raise RuntimeError(f"Ark seedream (ViMax) API: no url or b64_json in response: {item}")

        await _track_bridge_call("seedream", "generate_image", "success", _bridge_elapsed, model=self._model)
        return ImageOutput(fmt="url", ext="png", data=url)

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class Seedream360GeneratorForViMax:
    """ViMax ``ImageGenerator`` backed by 360-gateway seedream 5.0 Pro (C5b, 2026-10-12).

    Switches ViMax image generation from Ark-direct seedream to the 360 gateway
    (``bytedance/doubao-seedream-5.0-pro``, 点号命名) by wrapping the mature
    :class:`QihuImageProvider` — 参考图走 ``/v1/images/edits`` + ``extra_body.image``、
    尺寸走 ``extra_body.size`` 三档映射（顶层 size 被静默改派，T4 实测）、
    ``watermark: false``，全部由 provider 实现（E4 口径）；本桥只补 ViMax 契约
    （预算闸 / ``_track_bridge_call`` 计量 / aspect→size 换算）。
    :class:`SeedreamGeneratorForViMax` (Ark) 保留为回滚位: ``vimax_image_backend: ark``。

    角色首帧注意：360 侧不认 Ark 的字节"信任产物"机制——人物参考图是否被
    ``InputImageSensitiveContentDetected`` 拦截以 C5c 角色 spike 实测为准
    （2026-07 Stage 5.2 历史拦截即 360 网关行为；被拦则视频侧落 hailuo/happyhorse
    并在 changelog 记录裁决）。
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "bytedance/doubao-seedream-5.0-pro",
        max_calls: int | None = None,
        max_cost_cny: float | None = None,
        aspect_ratio: str | None = None,
        **_: Any,
    ) -> None:
        self._provider = QihuImageProvider(api_key=api_key, base_url=base_url, model=model)
        self._model = model
        self._aspect_ratio = aspect_ratio
        self._max_calls = max_calls
        self._max_cost_cny = max_cost_cny
        self._call_count = 0
        self.cost_cny = 0.0

    async def generate_single_image(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> ImageOutput:
        # ``reference_image_paths`` is optional: ViMax's ``generate_front_portrait``
        # calls this with ONLY ``prompt=`` (first portrait has no reference) — mirror
        # the Ark bridge's contract (plan §2.3 "bridge accepts the no-reference call shape").
        if self._max_calls is not None and self._call_count >= self._max_calls:
            raise VimaxBudgetExceeded(f"image generation budget exceeded: {self._call_count}/{self._max_calls} calls")
        _img_cost = _resolve_image_unit_cost("seedream-360")
        if self._max_cost_cny is not None and self.cost_cny + _img_cost > self._max_cost_cny:
            raise VimaxBudgetExceeded(f"image generation cost exceeded: ¥{self.cost_cny:.2f}+{_img_cost} > ¥{self._max_cost_cny:.2f}")
        self._call_count += 1
        self.cost_cny += _img_cost
        # size 优先级同 Ark 桥：显式 kwargs["size"] > aspect_ratio 三档映射。
        # provider 内部再经 _map_to_seedream_size 归一到三档（顶层 size 不可信）。
        size = kwargs.pop("size", None) or _aspect_to_size(self._aspect_ratio)
        async with _api_semaphore():
            _q360_start = time.monotonic()
            try:
                ref: ImageRef = await self._provider.generate_image(
                    prompt,
                    reference_image_paths=reference_image_paths or None,
                    size=size,
                    **kwargs,
                )
                await _track_bridge_call("seedream-360", "generate_image", "success", time.monotonic() - _q360_start, model=self._model)
            except Exception:
                await _track_bridge_call("seedream-360", "generate_image", "error", time.monotonic() - _q360_start, model=self._model)
                raise
        return ImageOutput(fmt="url", ext="png", data=ref.url)

    async def close(self) -> None:
        await self._provider.close()


# B2: happyhorse's "video latent download failed" (InternalError.Algo) is a
# transient 5xx-class backend fault that recovers on retry. hailuo's 4xx
# content-review rejection is NOT retryable. Match ONLY the precise transient
# patterns — NOT the generic "Video task ... failed" prefix (that prefix is
# shared by ALL _poll failures including content-review rejections, which must
# NOT be retried — retrying wastes a billing cycle and the rejection won't
# self-heal). Verified: qihu_video.py:197 raises RuntimeError for every _poll
# failure; the discriminators are the embedded failure_reason substrings.
_RETRYABLE_RUNTIME_PATTERNS = (
    "internalerror.algo",
    "video latent download failed",
)


def _is_retryable_runtime_error(exc: BaseException, backend: str) -> bool:
    """True if a RuntimeError from the video provider is worth retrying.

    Two gates (both must pass — defense in depth):
    1. backend == "happyhorse" — only happyhorse's latent-download class is
       retryable in practice (hailuo's _poll failures are content-review; seedance-ark
       content-review rejections surface via _is_content_review_rejection → t2v fallback,
       not tenacity retry).
    2. The error message matches a known-transient pattern (InternalError.Algo
       / Video latent download failed). NOT the generic "Video task ... failed"
       prefix — that's shared by content-review rejections too.
    """
    if not isinstance(exc, RuntimeError):
        return False
    if backend != "happyhorse":
        return False
    msg = str(exc).lower()
    return any(p in msg for p in _RETRYABLE_RUNTIME_PATTERNS)


class _RetryableVideoError(Exception):
    """Tagged wrapper so tenacity retries happyhorse latent failures only."""


class SeedanceVideoGeneratorForViMax:
    """ViMax ``VideoGenerator`` backed by a TrainerAI video provider.

    backend selects the provider (C3, 决策4 + Stage 5.2):
    - ``seedance-ark`` (default via auto): ``SeedanceArkVideoProvider`` — Ark-direct
      ``/api/v3/contents/generations/tasks`` (字节默认首选). i2v: 1 ref → ``image_url``
      (first_frame); 2 refs → ``image_urls`` [first_frame, last_frame]; 0 refs → t2v.
      Reference images are Seedream-generated角色首帧 — 字节"信任产物" (§0.4), safe as
      seedance-ark i2v input (NOT blocked by 人脸审查). The bridge does NOT accept
      真实人物照片 (only Seedream产物 / asset://); real-person photos still trip 人脸审查.
    - ``hailuo``: ``QihuVideoProvider`` i2v — first reference image is the first frame.
    - ``seedance``: ``SeedanceVideoProvider`` r2v — ALL reference images forwarded
      as multi-reference (``extra_body.role=reference``), aligning with ViMax
      ``ReferenceImageSelector`` output.
    - ``happyhorse`` (fallback): ``HappyhorseVideoProvider`` i2v — accepts both realistic
      and anime portraits; used when seedance-ark has infra failure or content review.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        model: str = "minimax/MiniMax-Hailuo-2.3-no-safety_checker",
        backend: str = "hailuo",
        max_calls: int | None = None,
        max_cost_cny: float | None = None,
        max_wait: float | None = None,
        aspect_ratio: str | None = None,
        **_: Any,
    ) -> None:
        # max_wait: per-task poll budget (seconds). None → provider's default.
        provider_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url, "model": model}
        if max_wait is not None:
            provider_kwargs["max_wait"] = max_wait
        if backend == "seedance-ark":
            from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

            self._provider = SeedanceArkVideoProvider(**provider_kwargs)
        elif backend == "seedance":
            from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

            self._provider = SeedanceVideoProvider(**provider_kwargs)
        elif backend == "happyhorse":
            from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

            self._provider = HappyhorseVideoProvider(**provider_kwargs)
        else:
            self._provider = QihuVideoProvider(**provider_kwargs)
        self._backend = backend
        # G15: 画面比例。此前视频桥完全没有该状态 → call_kwargs 从不带 ratio →
        # Ark 回落自己的 16:9 缺省，9:16 任务出横屏。
        self._aspect_ratio = aspect_ratio
        self._max_calls = max_calls
        self._max_cost_cny = max_cost_cny
        self._call_count = 0
        self.cost_cny = 0.0

    def _charge_video_call(self) -> None:
        """为一次即将发生的**远端建单**扣费；超限抛 VimaxBudgetExceeded。

        计费口径（E2 / Lane 2 2026-10-15）：跟着「一次 submit（或合体入口一次
        调用）」走——poll 重连复用同一远端任务，不产生新账单，不计费。内容审核
        后的 t2v 回退是新订单，重新计费。P1-3 的「每次真实 provider 调用都过
        预算闸」语义保持（d2048f3cc 的逐次计费口径就此作废——合体重试会把一次
        逻辑镜头计 2-3 次是 bug 不是口径，见 ``_generate_video_with_retry``）。

        C3: per_kilo_token providers (seedance-ark) cannot be pre-charged
        (token count unknown before generation) → always falls back to the
        hardcoded constant. per_second providers use DB price if available.

        ⚠️ Approximation: per_second unit_price is charged per *call* (not
        per actual second) because duration is unknown before generation
        completes. This makes the gate looser than intended for long videos
        (e.g. 0.5/s × 8s video costs ¥4 but gate only pre-charges ¥0.5).
        The max_calls count gate remains a hard ceiling regardless.
        """
        if self._max_calls is not None and self._call_count >= self._max_calls:
            raise VimaxBudgetExceeded(f"video generation budget exceeded: {self._call_count}/{self._max_calls} calls")
        _vid_cost = _resolve_video_unit_cost(self._backend)
        if self._max_cost_cny is not None and self.cost_cny + _vid_cost > self._max_cost_cny:
            raise VimaxBudgetExceeded(f"video generation cost exceeded: ¥{self.cost_cny:.2f}+{_vid_cost} > ¥{self._max_cost_cny:.2f}")
        self._call_count += 1
        self.cost_cny += _vid_cost

    async def generate_single_video(
        self,
        prompt: str,
        reference_image_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> VideoOutput:
        # ``reference_image_paths`` is optional: ViMax may call text-to-video shots
        # (no first frame) with only ``prompt=``. Mirror the image bridge's rationale
        # (ViMax's own callers omit the arg that its Protocol lists as positional).
        # Reference images → provider kwarg. seedance-ark (§5.2): 1 ref → image_url
        # (first_frame); 2 refs → image_urls [first_frame, last_frame]; 0 refs → t2v.
        # seedance (r2v) forwards ALL reference images (multi-reference); hailuo/
        # happyhorse (i2v) uses only the first as the first frame.
        # ViMax passes LOCAL file paths (saved frames); _to_image_url converts them to
        # data URLs the video API accepts (mirrors nano_banana reference handling).
        # seedance-ark refs are Seedream产物 (信任产物 §0.4) — NOT真实人物照片.
        if self._backend == "seedance-ark":
            data_urls = [u for u in (_to_image_url(p) for p in (reference_image_paths or [])) if u]
            if len(data_urls) >= 2:
                call_kwargs: dict[str, Any] = {"image_urls": data_urls}
            elif len(data_urls) == 1:
                call_kwargs = {"image_url": data_urls[0]}
            else:
                call_kwargs = {"image_url": None}
        else:
            first_ref = (reference_image_paths or [None])[0]
            image_url = _to_image_url(first_ref)
            if self._backend == "seedance" and reference_image_paths:
                image_urls = [u for u in (_to_image_url(p) for p in reference_image_paths) if u]
            else:
                image_urls = None
            call_kwargs = {"image_urls": image_urls} if image_urls else {"image_url": image_url}
        # Retry transient faults LOCALLY (per-shot) instead of letting them bubble up
        # → _wrap_pipeline → whole-pipeline Retry. Two retryable fault classes:
        #   - TimeoutError: i2v/r2v stalls the full max_wait then times out.
        #   - QihuServerError (5xx): the 360 API returns a transient server fault
        #     (e.g. 500 '服务异常', vg_1b893b5f09e1) that recovers on retry.
        # VimaxBudgetExceeded / plain RuntimeError (4xx) are NOT retried — a client
        # error or a cost cap won't fix itself; retrying wastes a billing cycle.
        # G15: 下发画面比例。provider 侧 (seedance_ark.generate_video) 早已支持
        # ``ratio``，缺口只在本桥从不放它 → Ark 用自己的 16:9 缺省，用户选 9:16
        # 拿到横屏成片。显式 kwargs 优先（调用方可覆盖）。
        if self._aspect_ratio and "ratio" not in kwargs:
            call_kwargs["ratio"] = self._aspect_ratio
        used_image = bool(call_kwargs.get("image_url") or call_kwargs.get("image_urls"))
        async with _api_semaphore():
            try:
                # E2 (Lane 2 2026-10-15): per-call 状态桶让 remote_task_id 穿过
                # tenacity 重试存活（重试以相同实参重调，同一 dict 累积状态）。
                # 不能放实例属性：生成器实例被 asyncio.gather 跨 shot 共享，会竞态。
                retry_state: dict[str, Any] = {"remote_task_id": None}
                vref: VideoRef = await self._generate_video_with_retry(prompt, _retry_state=retry_state, **call_kwargs, **kwargs)
            except RuntimeError as exc:
                # P4: if i2v was rejected by 360's image content review (Green net
                # check), retry once as text-to-video (drop the image) so disaster/
                # dramatic shots that trip the image review can still render. Only
                # when an image was actually used (pure t2v shots have nothing to drop).
                if used_image and _is_content_review_rejection(exc):
                    logger.warning(
                        "i2v content-review rejected for prompt %r (%s); retrying as t2v (no image)",
                        prompt[:80],
                        str(exc)[:120],
                    )
                    t2v_kwargs = {k: v for k, v in call_kwargs.items() if k not in ("image_url", "image_urls")}
                    # t2v 回退是新的远端订单：新状态桶 + 新一次计费（见 _charge_video_call 口径）。
                    vref = await self._generate_video_with_retry(prompt, _retry_state={"remote_task_id": None}, **t2v_kwargs, **kwargs)
                else:
                    raise
        return VideoOutput(fmt="url", ext="mp4", data=vref.url)

    @retry(
        stop=stop_after_attempt(3),  # B2: latent download is flaky — 1 initial + 2 retries
        wait=wait_exponential(multiplier=2, max=30),
        retry=retry_if_exception_type((TimeoutError, QihuServerError, _RetryableVideoError)),
        reraise=True,  # re-raise the original exception (not RetryError) when exhausted
    )
    async def _generate_video_with_retry(self, prompt: str, _retry_state: dict[str, Any] | None = None, **kwargs: Any) -> VideoRef:
        """E2 (Lane 2 2026-10-15)：submit/poll 解耦重试，对齐 throttle.py:444-467 形态。

        修前把 (submit + poll) 合体的 ``generate_video`` 当整体重试：**轮询**阶段
        超时 → 重新 submit → 远端开出第二单、旧单照跑照计费（双计费）。现在：
        提交成功即把 remote_task_id 记入 ``_retry_state``；后续重试只重连
        ``poll_video``（不建新单不计费）。仅当提交未取得 task_id（submit 失败）
        才允许重新提交——此时远端大概率未开出任务，重提是唯一恢复路径。

        split 检测取 **type 级** 属性：裸 AsyncMock 会自动生成任意属性名，实例级
        hasattr 会把 mock 误判进 split 通道（存量测试注入的 ``generate_video``
        side_effect 整条失效）；mock 落回合体入口 = 旧行为，零改动兼容。
        """
        if _retry_state is None:
            _retry_state = {}
        provider_type = type(self._provider)
        split = callable(getattr(provider_type, "submit_video", None)) and callable(getattr(provider_type, "poll_video", None))
        remote_task_id = _retry_state.get("remote_task_id")
        poll_kwargs: dict[str, Any] = {"duration": kwargs["duration"]} if "duration" in kwargs else {}
        # 计费在 try 外：预算闸拒绝（VimaxBudgetExceeded）不是 provider 调用，
        # 不得进 _track_bridge_call 错误账（与修前口径一致）。split 通道只在
        # 需要建单时扣费；poll 重连（task_id 已取得）不计。
        if (split and remote_task_id is None) or not split:
            self._charge_video_call()
        _video_start = time.monotonic()
        try:
            if split:
                if remote_task_id is None:
                    remote_task_id = await self._provider.submit_video(prompt, **kwargs)
                    _retry_state["remote_task_id"] = remote_task_id
                vref = await self._provider.poll_video(remote_task_id, **poll_kwargs)
            else:
                vref = await self._provider.generate_video(prompt, **kwargs)
            await _track_bridge_call(self._backend, "generate_video", "success", time.monotonic() - _video_start, model=self._backend)
            return vref
        except (TimeoutError, QihuServerError) as exc:
            await _track_bridge_call(self._backend, "generate_video", "error", time.monotonic() - _video_start, model=self._backend)
            logger.warning(
                "i2v failed for prompt %r (%s), retrying",
                prompt[:80],
                type(exc).__name__,
            )
            raise
        except RuntimeError as exc:
            await _track_bridge_call(self._backend, "generate_video", "error", time.monotonic() - _video_start, model=self._backend)
            if _is_retryable_runtime_error(exc, self._backend):
                logger.warning(
                    "happyhorse i2v transient failure for prompt %r, retrying: %s",
                    prompt[:80],
                    str(exc)[:120],
                )
                raise _RetryableVideoError(str(exc)) from exc
            raise  # non-retryable RuntimeError (e.g. hailuo 4xx) — propagate as-is
        except Exception:
            # C5: httpx network exceptions (ConnectError, ReadError etc.) are NOT
            # RuntimeError subclasses → would escape without tracking. Track then re-raise.
            await _track_bridge_call(self._backend, "generate_video", "error", time.monotonic() - _video_start, model=self._backend)
            raise

    async def close(self) -> None:
        """T4（Lane 2, 2026-10-15）：透传内层 provider 的 httpx client 关闭。

        修前桥零调用方关闭——每 vimax 任务泄漏 1 个 AsyncClient（叠加图像桥 2 个）。
        由 VimaxEngine.render 的 finally 统一调用（vendored pipeline 持有本桥实例）。
        """
        close = getattr(self._provider, "close", None)
        if callable(close):
            await close()


def build_vimax_config(
    *,
    working_dir: Path,
    llm_model: str,
    llm_base_url: str,
    llm_api_key: str,
    image_api_key: str,
    image_base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
    image_model: str = "doubao-seedream-5-0-pro-260628",
    image_backend: str = "ark",
    video_api_key: str,
    video_base_url: str = "https://api.360.cn",
    video_model: str = "minimax/MiniMax-Hailuo-2.3-no-safety_checker",
    max_image_calls: int | None = None,
    max_video_calls: int | None = None,
    max_cost_cny: float | None = None,
    image_aspect_ratio: str | None = None,
    pipeline: str = "script2video",
    video_max_wait: int | None = None,
    video_backend: str = "hailuo",
    api_concurrency: int = 3,
    character_reference: str | None = None,
    character_sheets: list[dict] | None = None,
) -> dict[str, Any]:
    """Build the ViMax pipeline config dict (§5.3).

    Translates TrainerAI model/provider settings into the shape ViMax's
    ``Script2VideoPipeline.init_from_config`` expects: ``chat_model.init_args``
    (model_provider/model/base_url/api_key — §5.3 v3.2: translated from TrainerAI
    ``ModelConfig``, not taken from ``create_chat_model``), and the image/video
    generators as ``class_path`` + ``init_args`` pointing at the host bridge.
    ``max_image_calls`` / ``max_video_calls`` wire the 阶段 4.5 budget hard-cutoff
    into the bridge generators (None = uncapped).

    ``image_backend`` (C5b 2026-10-12): ``"360"`` → ``Seedream360GeneratorForViMax``
    (调用方须配套传 qihu 凭据 + 点号命名模型 ``bytedance/doubao-seedream-5.0-pro``)；
    ``"ark"``（默认，存量调用方零改动）→ Ark 直连桥。未知值 ValueError（typo 防呆）。
    翻转开关在 service 层 config 字段（``vimax_image_backend``），不在本默认值。
    """
    # Size the process-wide API concurrency cap (lazy semaphore picks this up on first
    # use). Lower than ViMax's default all-shots-concurrent → less 360 gateway load.
    global _API_SEM_LIMIT
    _API_SEM_LIMIT = max(1, api_concurrency)
    image_backend_normalized = str(image_backend).strip().lower()
    if image_backend_normalized not in ("360", "ark"):
        raise ValueError(f"image_backend 未知值 {image_backend!r}——合法值: 360(360 网关通道，生产默认由 service 层 vimax_image_backend 决定) | ark(本参数默认/回滚位)；typo 会静默落错通道，必须显式报错（2026-10-12 C5 防呆）")
    image_class = f"{_BRIDGE_MODULE}.Seedream360GeneratorForViMax" if image_backend_normalized == "360" else f"{_BRIDGE_MODULE}.SeedreamGeneratorForViMax"
    image_init: dict[str, Any] = {"api_key": image_api_key, "base_url": image_base_url, "model": image_model}
    if image_aspect_ratio is not None:
        image_init["aspect_ratio"] = image_aspect_ratio
    video_init: dict[str, Any] = {"api_key": video_api_key, "base_url": video_base_url, "model": video_model, "backend": video_backend}
    # G15: 画面比例同时透传到生视频侧。此前只有 image_init 拿到它，视频侧永远
    # 回落 provider 缺省 16:9 —— 生图竖屏、生视频横屏，成片仍是横的。
    if image_aspect_ratio is not None:
        video_init["aspect_ratio"] = image_aspect_ratio
    if video_max_wait is not None:
        video_init["max_wait"] = video_max_wait
    if max_image_calls is not None:
        image_init["max_calls"] = max_image_calls
    if max_video_calls is not None:
        video_init["max_calls"] = max_video_calls
    if max_cost_cny is not None:
        # T5（2026-10-15 口径注记）：同一数值注入两侧，但**图侧与视频侧各自独立
        # 上限**——两桥各自累计 cost_cny、各自对 max_cost_cny 比较（见两侧
        # generate_single_* 的预算闸），不是共享总额池：任务理论最大开销可达
        # 2×max_cost_cny。改为共享池会动计费预期，用户另决（本批只注记不改语义）。
        image_init["max_cost_cny"] = max_cost_cny
        video_init["max_cost_cny"] = max_cost_cny
    return {
        "chat_model": {
            "init_args": {
                "model_provider": "openai",
                "model": llm_model,
                "base_url": llm_base_url,
                "api_key": llm_api_key,
            },
        },
        "image_generator": {
            # C5b (2026-10-12): image backend 由 image_backend 按值切换——360 网关
            # seedream（Seedream360GeneratorForViMax；生产默认通道=由 service 层 vimax_image_backend 传入）| Ark 直连
            # （SeedreamGeneratorForViMax，回滚位）。NanoBananaGeneratorForViMax
            # stays defined as a legacy rollback fallback.
            "class_path": image_class,
            "init_args": image_init,
        },
        "video_generator": {
            "class_path": f"{_BRIDGE_MODULE}.SeedanceVideoGeneratorForViMax",
            "init_args": video_init,
        },
        "working_dir": str(working_dir),
        # pipeline knob: _default_pipeline_factory reads this to pick the ViMax pipeline
        # class (idea2video vs script2video). idea2video = idea → story → script → render.
        "pipeline": pipeline,
        # HR-T4: 用户角色参考图路径（CourseProject.character_reference），供 vendored
        # CharacterPortraitsGenerator 注入 front portrait 的 reference_image_paths。
        "character_reference": character_reference,
        # B5c: 用户三视图（front/side/back dict list），供 vendored CharacterPortraitsGenerator
        # 注入 side/back 作额外参考图（front 已是 character_reference 生成了 front portrait）。
        "character_sheets": character_sheets,
    }

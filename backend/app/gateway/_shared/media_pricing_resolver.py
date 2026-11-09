"""Media pricing lookup — the single resolver (C2, 2026-08-27 成本可信化批次).

查价收口单点。全仓 ``media_pricing`` 的读取从 ``routers/console.py`` 整体下沉到
本模块（修掉 ``_shared/task_cost.py`` 反向 import routers 的分层倒挂；
``console.py`` re-export 保兼容，既有引用零改动——app.py 预热、media_pricing.py
缓存失效、闸/指标 4 处 ``_load_media_pricing_sync`` 消费点）。

两层键形（P0-2 收口的结构保证）：

- **provider 级行**（``model_id IS NULL``）保持扁平 ``(provider, media_type)→行``
  键形不变——闸/指标 4 处消费点（``visual_factory``/``video_gen/pricing``/
  ``provider_bridge``×2）零改动，只认 provider 级语义（A13）；
- **模型级行**（``model_id`` 非空）另存 ``(provider, model_id, media_type)→行``，
  仅经 :func:`resolve_media_price` 命中：**模型级（先经 ``PROVIDER_MODEL_ALIASES``
  归一）→ provider 级 → None**。估算侧消费点（task_cost 按任务、console 面板
  逐行 / by_provider 显示）全部改走本函数，按构造一致。
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from app.gateway._shared.media_constants import PROVIDER_MODEL_ALIASES
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.media_pricing.model import MediaPricingRow

logger = logging.getLogger(__name__)

#: 价格主数据种子（C7 单点产出，2026-08-27 成本可信化批次）：唯一权威定义在
#: resolver（估算逻辑的邻居），``routers/media_pricing.py`` 由此 import 再导出--
#: 消灭种子价与估算不同源的漂移温床（审计问题 4）。
#: 语义：DB 无行时 GET 的展示兜底层（``source="default"``）；估算读取的是 DB 行
#: （``_load_media_pricing``），种子不直接参与计价。
#: C0 修正（2026-08-27）：seedance-ark 0.023->0.046（Ark 官网 46 元/百万 tokens，
#: 不含视频输入档）；seedance 弃 per_second 0.5 改 per_kilo_token 0.046（A2：
#: provider 级默认按 2-0 档，fast/mini 分键随 C2）；happyhorse 弃用（A3）。
#: C1 填实（2026-08-27，随迁移 l4e8f0a9b2c6 落库，全部 estimated）：
#: - gpt-image-2 per_image 0.04 -> per_kilo_token 0.204（实测 token 计费，取输出率）；
#: - seedream 0.22 -> 0.5（Ark 控制台 ¥0.30~0.60/张取中值）；
#: - seedream-360 新增 0.5/张（A18 用户拍板：360 折算价远低于 Ark -> 取高价侧 Ark 价）；
#: - doubao_tts 0.5 -> 0.12/千字（A19 用户截图：火山 120 元/百万 token 输入，
#:   1 字≈1 token 折算）；
#: - minimax-h3 新增 0.0008/千 token（360 表价 ¥0.8/1M）→ 2026-09-01 T3 修正
#:   per_second 0.58：千 token 价是 360 **文本**表价误配到视频模型——MiniMax
#:   官方视频按**时长**计价（768P $0.08/秒 × 7.2），且 H3 轮询响应无
#:   usage/tokens 面，per_kilo_token 永远估不出（vg_22ee06b39d19 的 68/68 行
#:   generate_video unpriced → ~510s ≈¥296 视频分量整体漏记）。DB 行随迁移
#:   m6a7b8c9d0e1 同改（估算读 DB 行；种子只是无行时的展示兜底）；
#: - dashscope 不落种子（无在册调用，落 0 价=¥0 假账；unpriced 可见即可）。
DEFAULT_MEDIA_PRICING: list[dict] = [
    {"provider": "seedream", "media_type": "image", "unit": "per_image", "unit_price": 0.5, "currency": "CNY"},
    {"provider": "seedream-360", "media_type": "image", "unit": "per_image", "unit_price": 0.5, "currency": "CNY"},
    {"provider": "seedance-ark", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"},
    {"provider": "gpt-image-2", "media_type": "image", "unit": "per_kilo_token", "unit_price": 0.204, "currency": "CNY"},
    {"provider": "seedance", "media_type": "video", "unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"},
    {"provider": "minimax-h3", "media_type": "video", "unit": "per_second", "unit_price": 0.58, "currency": "CNY"},
    {"provider": "doubao_tts", "media_type": "tts", "unit": "per_kilo_char", "unit_price": 0.12, "currency": "CNY"},
    {"provider": "qihu_tts", "media_type": "tts", "unit": "per_kilo_char", "unit_price": 0.3, "currency": "CNY"},
]

# ---------------------------------------------------------------------------
# Process-level cache (TTL-bounded), moved verbatim from routers/console.py C3.
# ---------------------------------------------------------------------------

_media_pricing_cache: dict[tuple[str, str], dict] | None = None
_media_pricing_model_cache: dict[tuple[str, str, str], dict] | None = None
_media_pricing_cache_ts: float = 0.0
_MEDIA_PRICING_TTL = 60.0  # seconds


def _row_dict(r: MediaPricingRow, *, with_model_id: bool = False) -> dict:
    entry = {
        "unit": r.unit,
        "unit_price": r.unit_price,
        "currency": r.currency,
    }
    if with_model_id:
        entry["model_id"] = r.model_id
    return entry


async def _load_media_pricing() -> dict[tuple[str, str], dict]:
    """Load media_pricing into the process cache (TTL-bounded).

    Returns the **provider-level flat map only** — keyed ``(provider, media_type)``
    with the same shape the budget gates have always consumed. Model-level rows
    (``model_id`` set) are loaded into a separate cache served by
    :func:`resolve_media_price`; they must never leak into the flat map (they
    would collide with the provider-level row on the same 2-tuple key).
    """
    global _media_pricing_cache, _media_pricing_model_cache, _media_pricing_cache_ts
    now = time.monotonic()
    if _media_pricing_cache is not None and (now - _media_pricing_cache_ts) < _MEDIA_PRICING_TTL:
        return _media_pricing_cache

    sf = get_session_factory()
    if sf is None:
        return {}
    try:
        async with sf() as session:
            rows = (await session.execute(select(MediaPricingRow))).scalars().all()
        _media_pricing_cache = {(r.provider, r.media_type): _row_dict(r) for r in rows if r.model_id is None}
        _media_pricing_model_cache = {(r.provider, r.model_id, r.media_type): _row_dict(r, with_model_id=True) for r in rows if r.model_id is not None}
        _media_pricing_cache_ts = now
    except Exception:
        logger.debug("media_pricing load failed", exc_info=True)
        if _media_pricing_cache is None:
            _media_pricing_cache = {}
        if _media_pricing_model_cache is None:
            _media_pricing_model_cache = {}
    return _media_pricing_cache


def _load_media_pricing_sync() -> dict[tuple[str, str], dict]:
    """Sync accessor for the process cache. Returns the cached provider-level
    pricing map without triggering a DB query — the async ``_load_media_pricing``
    is responsible for refreshing. If the cache is empty (startup), returns empty
    dict and callers fall back to hardcoded constants.
    """
    return _media_pricing_cache or {}


def invalidate_media_pricing_cache() -> None:
    """Force the next ``_load_media_pricing`` call to re-read from DB.

    Called after PUT/PATCH/DELETE on /api/admin/media-pricing and during startup
    warmup.
    """
    global _media_pricing_cache, _media_pricing_model_cache, _media_pricing_cache_ts
    _media_pricing_cache = None
    _media_pricing_model_cache = None
    _media_pricing_cache_ts = 0.0


def resolve_media_price(provider: str | None, model_id: str | None, media_type: str | None) -> dict | None:
    """Resolve the pricing entry for one call: **model-level (alias-normalized)
    → provider-level → None**.

    ``model_id`` comes from ``external_api_calls.metadata_json.model`` — which
    carries three namespaces in the wild (registry sink_label / real model id /
    ViMax backend name; see ``PROVIDER_MODEL_ALIASES``), so the lookup aliases
    first and falls back to a direct ``(provider, model_id, media_type)`` hit so
    operator-configured model-level rows with arbitrary keys keep working.

    Sync on purpose: reads the caches populated by ``_load_media_pricing`` /
    warmup — same freshness contract as ``_load_media_pricing_sync``.
    """
    flat = _media_pricing_cache or {}
    model_map = _media_pricing_model_cache or {}

    canon = PROVIDER_MODEL_ALIASES.get(model_id) if model_id else None
    if model_id:
        if canon is not None:
            hit = model_map.get((canon[0], canon[1], media_type))
            if hit is not None:
                return hit
        hit = model_map.get((provider, model_id, media_type))
        if hit is not None:
            return hit

    # provider 级兜底：别名规范化后的 provider 优先（存量 qihu 历史行等
    # provider 列与模型不匹配的场景），再退到行自身的 provider。
    if canon is not None:
        hit = flat.get((canon[0], media_type))
        if hit is not None:
            return hit
    return flat.get((provider, media_type))


def _estimate_call_cost(metadata: dict | None, method: str, pricing_entry: dict | None) -> float | None:
    """Estimate the cost of a single external API call from metadata + pricing.

    Returns None if the call cannot be estimated (no pricing or no data).
    Caller must exclude FREE_METHODS before calling this.
    """
    if pricing_entry is None:
        return None
    unit = pricing_entry["unit"]
    price = pricing_entry["unit_price"]
    md = metadata or {}

    if unit == "per_image":
        # One call = one image; no metadata field needed
        return price
    if unit == "per_kilo_token":
        tokens = md.get("total_tokens")
        if tokens is not None:
            return (float(tokens) / 1000.0) * price
        return None  # can't estimate without token count
    if unit == "per_second":
        duration = md.get("duration")
        if duration is not None:
            return float(duration) * price
        return None  # can't estimate without duration
    if unit == "per_kilo_char":
        char_count = md.get("char_count")
        if char_count is not None:
            return (float(char_count) / 1000.0) * price
        return None  # can't estimate without char count
    return None

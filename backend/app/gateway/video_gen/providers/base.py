"""Provider protocol definitions for video generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


def _extract_request_id(resp: Any) -> str | None:
    """Extract a request id from an HTTP response for diagnostics (A2).

    Reads two locations:
    1. ``x-request-id`` header (360 / Ark standard).
    2. ``error.request_id`` in the JSON body (some providers embed it).
    3. ``error.message`` that is itself a JSON string containing ``request_id``.

    Returns the first non-empty value found, or None.
    """
    # Header (works for httpx.Response or any dict-like headers)
    headers = getattr(resp, "headers", None)
    if headers is not None:
        for h in ("x-request-id", "X-Request-Id", "request-id"):
            val = headers.get(h) if hasattr(headers, "get") else None
            if val:
                return str(val)
    # JSON body
    try:
        body = resp.json() if hasattr(resp, "json") else None
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict):
        rid = err.get("request_id") or err.get("requestId")
        if rid:
            return str(rid)
        # error.message may itself be a JSON string with request_id
        import json

        msg = err.get("message")
        if isinstance(msg, str):
            try:
                inner = json.loads(msg)
                if isinstance(inner, dict):
                    rid = inner.get("request_id") or inner.get("requestId")
                    if rid:
                        return str(rid)
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def _append_request_id(resp: Any, msg: str) -> str:
    """Append ``[request_id=...]`` to an error message if a request id is found."""
    rid = _extract_request_id(resp)
    if rid:
        return f"{msg} [request_id={rid}]"
    return msg


def _extract_cost(data: dict[str, Any]) -> tuple[float | None, dict[str, Any] | None]:
    """B1: Extract (cost_cny, raw_usage) from a provider response dict.

    360/Ark gateways return ``{"usage": {"amount": "0.020396"}}``.
    Returns (float(amount), usage_dict) if present; (None, None) otherwise.
    Never raises — missing/ malformed usage is the common case, not an error.
    """
    if not isinstance(data, dict):
        return None, None
    usage = data.get("usage")
    # 2026-09-01 计费修复：usage 为完全空 dict（360 seedance 顶层 usage 恒 {}）时
    # 按「无 usage」返 (None, None)——否则调用方「raw_usage is None」回退条件
    # （seedance.py E9 extra_data.usage 兜底）永不命中，total_tokens 真账被空 dict
    # 吞掉（实测：本地 generate_video 327/327 行缺 total_tokens，视频分量记 ¥0）。
    # 含键但无 amount 的 usage（gpt-image-2 的 {"total_tokens": N}）不受影响。
    if not isinstance(usage, dict) or not usage:
        return None, None
    try:
        amount = usage.get("amount")
        if amount is not None:
            return float(amount), usage
    except (TypeError, ValueError):
        pass
    return None, usage


@dataclass
class Clip:
    url: str
    duration: float = 0.0
    source: str = ""
    thumbnail_url: str = ""


@dataclass
class ImageRef:
    url: str
    width: int = 0
    height: int = 0
    source: str = ""
    # B1: 供应商返回的真账金额（360/Ark 的 usage.amount 字段）；无则为 None。
    cost_cny: float | None = None
    # B1: 原始 usage dict，留给 console 对账/审计；无则为 None。
    raw_usage: dict[str, Any] | None = None
    # D2 修复批（2026-09-23）：gpt-image-2.5 系请求尺寸不在实测白名单时被就近
    # 贴档——本字段记原始请求尺寸（如 "800x640"），供 ai_image 层写
    # image_size_mapped 降级留痕；未映射/其他 provider 恒 None。
    size_mapped_from: str | None = None


@dataclass
class VideoRef:
    url: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    source: str = ""
    # B1: 供应商返回的真账金额（360/Ark 的 usage.amount 字段）；无则为 None。
    cost_cny: float | None = None
    # B1: 原始 usage dict，留给 console 对账/审计；无则为 None。
    raw_usage: dict[str, Any] | None = None
    # Phase 2: return_last_frame 产出的官方尾帧 URL（信任产物豁免）；无则为 None。
    last_frame_url: str | None = None


class AIVisualImageProvider(Protocol):
    async def generate_image(
        self,
        prompt: str,
        *,
        reference_image_paths: list[str] | None = None,
        **kwargs,
    ) -> ImageRef: ...

    # reference_image_paths is optional + backward compatible: existing impls
    # using **kwargs absorb it; GptImage2Provider uses it for /v1/images/edits.
    # ViMax's character-consistency chain (plan §2.3 bridge) hard-depends on it.


class AIVisualVideoProvider(Protocol):
    async def generate_video(self, prompt: str, *, duration: float, **kwargs) -> VideoRef: ...

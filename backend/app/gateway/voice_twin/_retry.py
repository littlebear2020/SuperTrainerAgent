"""共享 TTS 瞬时重试策略（D2，2026-08-09）。

从 production_service 抽出的公共 helper：传输层瞬断 + 结构化 429/5xx，
指数退避 base_delay×2^n，默认 3 次。克隆路径（upload_audio / synthesize_sse）
与 production 合成路径共用——此前克隆路径裸调无重试，一次抖动即训练失败。

分类判断直接吃 D5 的结构化错误（isinstance + status_code），不做消息正则。
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

# 传输层瞬断（与 video-gen tts.py _RETRYABLE_TTS_ERRORS 对齐）
TRANSPORT_TTS_ERRORS: tuple[type[Exception], ...] = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectTimeout,
    asyncio.TimeoutError,  # T1: SSE 整调用 wait_for 超时（停滞/慢滴）必须可重试
)


def tts_sse_timeout() -> float:
    """SSE 整调用超时（秒），env ``TTS_SSE_TIMEOUT`` 可调，默认 120。

    T1（2026-08-14）：httpx 标量 timeout 的 read 语义是"等下一块数据"的间隔——
    完全停滞（零字节）会触发 read timeout；慢滴（间隔 < 阈值、断续有字节）不触发，
    由 ``asyncio.wait_for`` 包整个 SSE 消费循环兜底整调用上限。超时抛
    ``asyncio.TimeoutError``（∈ TRANSPORT_TTS_ERRORS → 自动重试 + 降级链）。
    120s 是决策 D2：doubao 是主力路径，40 字以内拆分段足够，且避免误降级全片换音色。
    """
    return float(os.environ.get("TTS_SSE_TIMEOUT", "120"))


def volcengine_sse_timeout() -> float:
    """volcengine SSE 整调用超时（秒），env ``VOLCENGINE_SSE_TIMEOUT`` 可调，默认 180。

    2026-09-02 C1：与 tts_sse_timeout 同置一处，独立 env/默认值——180s =
    doubao 120s 参照 + volcengine 长文本（试听上限 5000 字）余量，上线后按实际
    触发情况校准。语义同 tts_sse_timeout（见上）。
    """
    return float(os.environ.get("VOLCENGINE_SSE_TIMEOUT", "180"))


def structured_tts_http_errors() -> tuple[type[Exception], ...]:
    """三家 TTS 客户端的结构化 HTTP 错误类型（D5）。延迟 import 防循环。"""
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError
    from app.gateway.voice_twin.qihu_tts_client import QihuTTSHTTPError
    from app.gateway.voice_twin.volcengine_client import VolcengineTTSHTTPError

    return (DoubaoTTSHTTPError, VolcengineTTSHTTPError, QihuTTSHTTPError)


def is_retryable_tts_error(e: BaseException) -> bool:
    """值得瞬时重试的 TTS 错误：传输层瞬断，或 429/5xx。

    覆盖两类形态：D5 结构化错误（isinstance + status_code 字段）与
    httpx.HTTPStatusError（客户端 raise_for_status 的原始形态，如克隆上传）。
    4xx（鉴权/参数）与 SSE 业务码（status_code=None）不重试——重试不会让
    坏请求变好，只会浪费 TTS 配额。
    """
    if isinstance(e, TRANSPORT_TTS_ERRORS):
        return True
    structured = structured_tts_http_errors()
    if isinstance(e, structured):
        status = getattr(e, "status_code", None)
        return status is not None and (status == 429 or status >= 500)
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        return status == 429 or status >= 500
    return False


async def call_with_tts_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    label: str = "TTS",
) -> T:
    """对一次 TTS 客户端调用做瞬时重试。

    重试耗尽后抛 ``RuntimeError("{label} failed after {n} retries")``，
    原始错误挂在 ``__cause__`` 上（降级链按 cause 分类）。不可重试错误
    （4xx/业务码/参数错）立即原样抛出。
    """
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as e:
            if not is_retryable_tts_error(e):
                raise
            last_err = e
            wait = base_delay * (2**attempt)
            reason = "transport" if isinstance(e, TRANSPORT_TTS_ERRORS) else f"HTTP {getattr(e, 'status_code', '?')}"
            logger.warning("%s %s, retrying in %.1fs (attempt %d/%d): %s", label, reason, wait, attempt + 1, max_retries, str(e)[:200])
            await asyncio.sleep(wait)
    raise RuntimeError(f"{label} failed after {max_retries} retries") from last_err

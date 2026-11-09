"""Video-gen orchestration package: script / storyboard / TTS helpers."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# E5: LLM 调用默认有界超时（秒）。LLM hang 时任务会一直挂到 stall_timeout(1800s) 才被杀；
# 这里给单次调用一个更紧的上限，让 hang 在可诊断时间内失败。
DEFAULT_LLM_TIMEOUT = 120


async def _invoke_llm_bounded(llm, messages, *, timeout: float | None = None):
    """E5: 给单次 LLM ainvoke 套 asyncio.wait_for，hang 时抛带上下文的 RuntimeError（不无限挂）。

    超时抛 RuntimeError 而非裸 asyncio.TimeoutError，让调用方能区分"模型超时"与其他异常，
    并在日志/任务错误里给出可诊断信息。调用方按需自行决定重试。
    ``timeout=None`` 回落 ``DEFAULT_LLM_TIMEOUT``（T3：逐帧兜底传 60s 收紧单帧预算）。

    C1: 成功/失败都记账（status 三态），不影响原有错误语义。
    """
    from app.gateway._shared.llm_usage_sink import record_llm_usage

    if timeout is None:
        timeout = DEFAULT_LLM_TIMEOUT

    try:
        resp = await asyncio.wait_for(llm.ainvoke(messages), timeout)
        # C1: record successful LLM call
        usage = getattr(resp, "usage_metadata", None)
        rm = getattr(resp, "response_metadata", None)
        model_name = None
        if isinstance(rm, dict):
            model_name = rm.get("model_name") or rm.get("model")
        await record_llm_usage(
            model_name=model_name,
            usage_metadata=usage,
            response_metadata=rm,
            caller_tag="video_gen_bottleneck",
            status="success",
        )
        return resp
    except TimeoutError as e:
        # C1: record timeout failure (no usage, just a count)
        await record_llm_usage(
            model_name=None,
            usage_metadata=None,
            response_metadata=None,
            caller_tag="video_gen_bottleneck",
            status="error",
        )
        raise RuntimeError(f"LLM 调用超时（{timeout}s），可能模型 hang 或网络阻塞") from e
    except Exception:
        # C1: record other failures (no usage, just a count)
        await record_llm_usage(
            model_name=None,
            usage_metadata=None,
            response_metadata=None,
            caller_tag="video_gen_bottleneck",
            status="error",
        )
        raise

"""AssistService — lightweight LLM rewrite service, bypasses LangGraph."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field, model_validator

from app.gateway._shared.task_framework.metrics import metrics
from app.gateway.content.schemas import DocType

logger = logging.getLogger(__name__)


def _stream_idle_timeout() -> float:
    """流式无数据超时(秒),env ``ASSIST_STREAM_IDLE_TIMEOUT`` 可调,默认 60。

    2026-09-02 C2:盯"等下一片数据"的间隔——上游"连接挂着但无数据"(僵死)时
    自动放弃;正常流式输出不会被误掐(慢流的 chunk 间隔远小于 60s)。
    """
    return float(os.environ.get("ASSIST_STREAM_IDLE_TIMEOUT", "60"))


def _stream_first_chunk_timeout() -> float:
    """首片前静默超时(秒),env ``ASSIST_STREAM_FIRST_CHUNK_TIMEOUT`` 可调,默认 240。

    对齐模型层首片预算(factory.py ``_DEFAULT_STREAM_CHUNK_TIMEOUT_SECONDS``=240):
    reasoning 模型(doubao-thinking/qwen)首片合法地需要 90~150s。2026-09-03
    复核反馈问题 1:C2 的 60s idle 超时若同样覆盖首片,full_doc 思考档会被真实
    误杀——首片前走 240s 档,首片到达后回落 60s idle 档。
    """
    return float(os.environ.get("ASSIST_STREAM_FIRST_CHUNK_TIMEOUT", "240"))


class AssistRequest(BaseModel):
    op: str = Field(..., pattern="^(rewrite|polish|translate|expand|summarize|grammar|tone|shorten|continue)$")
    text: str = Field(...)
    context: str | None = Field(None, max_length=2000)
    target_lang: str | None = None
    tone: str | None = None
    instruction: str | None = Field(None, max_length=1000)
    mode: str | None = Field(None, pattern="^(rewrite|shorten|expand)$")
    full_doc: bool = False
    doc_type: DocType | None = None

    @model_validator(mode="after")
    def _validate_text_length(self) -> AssistRequest:
        limit = 20000 if self.full_doc else 4000
        if len(self.text) > limit:
            raise ValueError(f"text exceeds {limit} characters (full_doc={self.full_doc})")
        return self


OP_PROMPTS: dict[str, str] = {
    "rewrite": "请用更清晰的表达改写以下文本，保持原意和长度。仅输出改写结果，不要解释：\n\n{text}",
    "polish": "请润色以下文本，改善流畅性和表达力。仅输出润色结果，不要解释：\n\n{text}",
    "translate": "请将以下文本翻译为{target_lang}。仅输出翻译结果：\n\n{text}",
    "expand": "请将以下文本扩写至原文 1.5-2 倍长度，补充细节和例证。仅输出扩写结果：\n\n{text}",
    "summarize": "请将以下文本概括为 1-2 句话。仅输出概括结果：\n\n{text}",
    "shorten": "请精简以下文本，在保持原意和关键信息的前提下减少篇幅。仅输出精简后的完整文本，不要解释：\n\n{text}",
    "grammar": "请仅修正以下文本的语法和拼写错误，不改变内容。仅输出修正结果：\n\n{text}",
    "tone": "请将以下文本调整为{tone}语气。仅输出结果：\n\n{text}",
    "continue": "请基于以下文本的文风、主题和上下文，自然续写一段（约 150-300 字）。仅输出续写的新内容，不要重复原文，不要解释：\n\n{text}",
}


DOC_TYPE_STYLE_HINTS: dict[DocType, str] = {
    "article": "面向公众号/大众读者，语言口语化、可读性强，少用术语",
    "case": "商业案例分析，数据驱动，结构化呈现结论-依据",
    "script": "口播/分镜脚本，短句，口语节奏，适合朗读",
    "podcast": "对话感，口语化，适合播客听众",
    "course": "教学/技术文档，严谨、步骤清晰、术语准确",
    "general": "通用写作",
}


class AssistService:
    """Lightweight rewrite service: direct LLM, no LangGraph."""

    def __init__(self, *, model_name: str | None = None, concurrency: int = 50) -> None:
        # Fix 1.1: 两个模型档——选区编辑（短、要快）走 fast（thinking 关），
        # 全文编辑（重质量）走 quality（thinking 开）。同一底座，质量差异来自
        # thinking 配置（thinking_enabled=False → reasoning_effort="minimal"）。
        self._llm_fast = self._build_llm(model_name, thinking_enabled=False)
        self._llm_quality = self._build_llm(model_name, thinking_enabled=True)
        self._sem = asyncio.Semaphore(concurrency)

    def _build_llm(self, explicit: str | None, *, thinking_enabled: bool) -> Any:
        from deerflow.models import create_chat_model

        # OPS-1（2026-10-07）：删占位候选名回退链——ASSIST_MODEL/BASIC_MODEL
        # 从未出现在任何部署配置（config.yaml/example 均无此二键），遍历它们
        # 每次启动必落一条 warning 且无实际作用。explicit 保留（构造注入），
        # 缺省直接走默认模型（create_chat_model(None)）。
        return create_chat_model(explicit, thinking_enabled=thinking_enabled)

    _MODE_HINTS = {"rewrite": "改写", "shorten": "精简", "expand": "扩写"}

    # Op-flavored hint for the instruction branch when req.mode is absent (the
    # selection follow-up refine case). polish/summarize aren't in _MODE_HINTS,
    # so without this map a polish/summarize follow-up would read as 改写
    # regardless of the round's op (W9 §4.4 bug).
    _OP_HINTS: dict[str, str] = {
        "rewrite": "改写",
        "polish": "润色",
        "expand": "扩写",
        "summarize": "概要",
        "shorten": "精简",
        "grammar": "纠错",
        "tone": "语气",
        "translate": "翻译",
        "continue": "续写",
    }

    def _build_prompt(self, req: AssistRequest) -> str:
        if req.instruction:
            if req.mode:
                # Instruction-only callers (mode explicitly set) keep the
                # mode-based hint — regression: that path is unchanged.
                mode_hint = self._MODE_HINTS.get(req.mode, "编辑")
            else:
                # Follow-up / selection refine (no mode): honor req.op so the
                # refine carries the round's op flavor (polish→润色,
                # summarize→概要) instead of defaulting to 改写.
                mode_hint = self._OP_HINTS.get(req.op, "编辑")
            prompt = f"你是一个专业的 Markdown 编辑助手。\n\n编辑模式：{mode_hint}\n用户要求：{req.instruction}\n\n待编辑文本：\n```markdown\n{req.text}\n```\n\n只输出编辑后的 Markdown 文本，不要包含解释、标题或代码围栏。"
        else:
            template = OP_PROMPTS.get(req.op, OP_PROMPTS["rewrite"])
            prompt = template.format(
                text=req.text,
                target_lang=req.target_lang or "英文",
                tone=req.tone or "正式",
            )
        if req.doc_type and req.doc_type != "general":
            hint = DOC_TYPE_STYLE_HINTS[req.doc_type]
            prompt = f"风格要求：{hint}\n\n{prompt}"
        if req.context:
            prompt = f"上下文：\n{req.context}\n\n{prompt}"
        return prompt

    async def stream(self, req: AssistRequest, *, user_id: str, request: Request | None = None) -> AsyncGenerator[str, None]:
        start = time.monotonic()
        labels = {"op": req.op}
        use_v2 = req.instruction is not None
        # D2: Track streaming usage for accounting
        _stream_usage = None
        _stream_rm = None
        _disconnected = False
        _recorded = False  # guard against double recording (success + finally)
        try:
            async with self._sem:
                prompt = self._build_prompt(req)
                if use_v2:
                    yield f"data: {json.dumps({'type': 'status', 'content': '正在分析文本...'})}\n\n"
                # P0-3: full_doc 允许 20000 字输入，输出须与之匹配——16384 对
                # gemini-3.6-flash (max 32768) 安全。
                # P1-4: 必须经 bind() 传递——RunnableConfig 的 max_tokens/
                # temperature 会被 langchain 静默忽略（实证 _astream kwargs={}）。
                # Fix 1.1: full_doc 走质量档（thinking 开），选区编辑走快速档。
                max_tokens = 16384 if req.full_doc else 2000
                llm = self._llm_quality if req.full_doc else self._llm_fast
                llm = llm.bind(max_tokens=max_tokens, temperature=0.7)
                # 无数据超时(2026-09-02 C2):逐 chunk 盯"等下一片数据"的间隔——
                # 上游"连接挂着但无数据"(僵死)时自动放弃;正常流式输出不会被误掐
                # (慢流每个 chunk 间隔远小于 60s)。首片前静默走 240s 档,对齐模型
                # 层首片预算,防思考档误杀(2026-09-03 复核反馈问题 1)。超时不再
                # 裸 raise 断连(200 头已发出,断连=前端只能收到网络错):发
                # type:error 错误帧,由前端 streamAssist 识别后提示"AI 响应超时,
                # 请重试"(2026-09-03 review 闭环);不发 [DONE],metrics/usage 按
                # error 记账。
                astream_it = llm.astream([{"role": "user", "content": prompt}]).__aiter__()
                _timed_out = False
                _got_first_chunk = False
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            astream_it.__anext__(),
                            timeout=_stream_idle_timeout() if _got_first_chunk else _stream_first_chunk_timeout(),
                        )
                        _got_first_chunk = True
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        # C2 闭环:发错误帧后收尾(_timed_out 挡住 [DONE]/ok 记账)
                        yield f"data: {json.dumps({'type': 'error', 'content': 'AI 响应超时,请重试'})}\n\n"
                        _timed_out = True
                        break
                    # D2: collect usage from the last chunk
                    chunk_usage = getattr(chunk, "usage_metadata", None)
                    if chunk_usage:
                        _stream_usage = chunk_usage
                    chunk_rm = getattr(chunk, "response_metadata", None)
                    if chunk_rm:
                        _stream_rm = chunk_rm
                    # Disconnect detection: stop streaming if client is gone
                    if request and await request.is_disconnected():
                        _disconnected = True
                        break
                    text = getattr(chunk, "content", None)
                    if isinstance(text, list):
                        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
                    if text:
                        yield f"data: {json.dumps({'text': text, 'type': 'text', 'content': text})}\n\n"
                # 超时后不发 [DONE],防前端把错误收尾当正常完成
                if not _timed_out:
                    yield "data: [DONE]\n\n"
            if _timed_out:
                # C2 闭环:超时已发错误帧收尾——metric 记 error;usage 按是否
                # 已收到部分用量记 error_downstream / error,不走 except 的裸
                # raise(那会让连接断掉,前端拿不到错误帧)
                metrics.inc("assist_request_total", labels={**labels, "status": "error"}, help_text="Total assist requests")
                from app.gateway._shared.llm_usage_sink import record_llm_usage

                rm_model = _stream_rm.get("model_name") or _stream_rm.get("model") if isinstance(_stream_rm, dict) else None
                if _stream_usage is not None:
                    await record_llm_usage(
                        model_name=rm_model,
                        usage_metadata=_stream_usage,
                        response_metadata=_stream_rm,
                        caller_tag="writer_assist",
                        domain="writer_assist",
                        status="error_downstream",
                        user_id=user_id,
                    )
                else:
                    await record_llm_usage(
                        model_name=None,
                        caller_tag="writer_assist",
                        domain="writer_assist",
                        status="error",
                        user_id=user_id,
                    )
                _recorded = True
            else:
                metrics.inc("assist_request_total", labels={**labels, "status": "ok"}, help_text="Total assist requests")
                # D2: Record successful completion (skip if disconnected — handled in finally)
                if not _disconnected:
                    from app.gateway._shared.llm_usage_sink import record_llm_usage

                    rm_model = _stream_rm.get("model_name") or _stream_rm.get("model") if isinstance(_stream_rm, dict) else None
                    await record_llm_usage(
                        model_name=rm_model,
                        usage_metadata=_stream_usage,
                        response_metadata=_stream_rm,
                        caller_tag="writer_assist",
                        domain="writer_assist",
                        user_id=user_id,
                    )
                    _recorded = True
        except Exception as e:
            logger.exception("assist stream failed")
            metrics.inc("assist_request_total", labels={**labels, "status": "error"}, help_text="Total assist requests")
            # C2 同类洞兜底(2026-09-03 复核反馈问题 2):通用 provider 异常也发
            # 错误帧——200 头已发出,裸 raise 前端只能拿到无意义网络错。帧先行、
            # 记账在后(记账自身失败不吞帧)。
            yield f"data: {json.dumps({'type': 'error', 'content': f'AI 服务异常：{str(e)[:120]}'})}\n\n"
            # D2: Record error — usage may be available if we got partway through streaming
            from app.gateway._shared.llm_usage_sink import record_llm_usage

            # Extract model_name safely — rm_model may not be defined if exception
            # occurred before the success-path assignment.
            err_model = _stream_rm.get("model_name") or _stream_rm.get("model") if isinstance(_stream_rm, dict) else None
            if _stream_usage is not None:
                # Partial usage collected before exception → error_downstream
                await record_llm_usage(
                    model_name=err_model,
                    usage_metadata=_stream_usage,
                    response_metadata=_stream_rm,
                    caller_tag="writer_assist",
                    domain="writer_assist",
                    status="error_downstream",
                    user_id=user_id,
                )
            else:
                await record_llm_usage(
                    model_name=None,
                    caller_tag="writer_assist",
                    domain="writer_assist",
                    status="error",
                    user_id=user_id,
                )
            _recorded = True
        finally:
            # D2: If disconnected with usage → error_downstream (token was consumed).
            # Only record here if nothing was recorded in the try/except above
            # (avoids double counting: disconnect falls through to success path
            # which skips recording via the _disconnected guard).
            if _disconnected and _stream_usage is not None and not _recorded:
                from app.gateway._shared.llm_usage_sink import record_llm_usage

                rm_model = _stream_rm.get("model_name") or _stream_rm.get("model") if isinstance(_stream_rm, dict) else None
                await record_llm_usage(
                    model_name=rm_model,
                    usage_metadata=_stream_usage,
                    response_metadata=_stream_rm,
                    caller_tag="writer_assist",
                    domain="writer_assist",
                    status="error_downstream",
                    user_id=user_id,
                )
            elapsed = time.monotonic() - start
            metrics.observe("assist_request_duration_seconds", elapsed, labels=labels, help_text="Assist request duration in seconds")

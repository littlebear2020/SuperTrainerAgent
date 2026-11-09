"""Unified LLM usage accounting sink (C1).

Writes one ``RunRow`` per LLM call with ``operation_kind="llm_bookkeeping"``
so the console can report non-conversation LLM spend (video-gen, ViMax,
writer/gallery/suggestions) without polluting run counts or failure rates.

Design decisions (from the 2026-08-08 plan §1.4):

- **One row per call** — no upsert/merge, no races. 10-person deployment =
  ~1000 rows/day; console queries filter on ``operation_kind``.
- **status three-state**: ``success`` / ``error_downstream`` / ``error``.
  Failed calls also count (no precision loss); ``error`` rows have no
  tokens (objective reality — no estimation/fabrication).
- **amount sparse key** — same convention as ``cache_read_tokens``: only
  present when the gateway returns a real-bill amount.

All writes are fire-and-forget: a DB failure logs but never throws, because
accounting must not break business flows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context management
# ---------------------------------------------------------------------------


@dataclass
class AccountingContext:
    task_id: str
    user_id: str
    domain: str


_accounting_ctx: ContextVar[AccountingContext | None] = ContextVar("llm_accounting_ctx", default=None)


def set_llm_accounting_context(task_id: str, user_id: str, domain: str) -> None:
    """Set the accounting context for the current async task.

    Called once at ``_wrap_pipeline`` entry to cover all 4 dispatch points
    (create / resume / reaper / tool). Reads happen in ``_invoke_llm_bounded``
    and the ViMax wrapper.
    """
    _accounting_ctx.set(AccountingContext(task_id=task_id, user_id=user_id, domain=domain))


def get_llm_accounting_context() -> AccountingContext | None:
    return _accounting_ctx.get()


def _resolve_user_id() -> str:
    """User id from contextvar, falling back to the harness global."""
    ctx = _accounting_ctx.get()
    if ctx is not None:
        return ctx.user_id
    try:
        from deerflow.runtime.user_context import get_effective_user_id

        return get_effective_user_id()
    except Exception:
        return "default"


# ---------------------------------------------------------------------------
# Amount extraction (two-level read, same logic as journal._extract_amount)
# ---------------------------------------------------------------------------


def extract_amount_from_metadata(response_metadata: Any) -> float | None:
    """Two-level read for the gateway's real-bill amount.

    - First ``response_metadata["amount"]`` (B1b streaming patch slot)
    - Then ``response_metadata["token_usage"]["amount"]`` (non-streaming)
    """
    try:
        val = response_metadata.get("amount") if isinstance(response_metadata, Mapping) else None
        if val is None:
            token_usage = response_metadata.get("token_usage") if isinstance(response_metadata, Mapping) else None
            if isinstance(token_usage, Mapping):
                val = token_usage.get("amount")
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Core write
# ---------------------------------------------------------------------------


async def record_llm_usage(
    *,
    model_name: str | None,
    usage_metadata: Mapping[str, Any] | None = None,
    response_metadata: Mapping[str, Any] | None = None,
    caller_tag: str = "",
    status: str = "success",
    user_id: str | None = None,
    domain: str | None = None,
    task_type: str | None = None,
) -> None:
    """Write one ``llm_bookkeeping`` row.

    Args:
        model_name: Provider-reported model name.
        usage_metadata: LangChain normalized usage (input_tokens etc.).
            May be ``None`` for ``status="error"`` calls.
        response_metadata: LangChain response metadata (may contain amount).
            May be ``None`` for ``status="error"`` calls.
        caller_tag: Short label identifying the call site.
        status: One of ``success`` / ``error_downstream`` / ``error``.
        user_id: Override user id; falls back to contextvar → get_effective_user_id.
        domain: Override domain; falls back to contextvar.
        task_type: Sub-classification within the domain (e.g. ``vimax``).

    Never raises — accounting failures are logged only.
    """
    try:
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.run.model import RunRow

        sf = get_session_factory()
        if sf is None:
            # Memory backend — nowhere to write.
            return

        ctx = _accounting_ctx.get()
        uid = user_id or (ctx.user_id if ctx else _resolve_user_id())
        dom = domain or (ctx.domain if ctx else "unknown")
        tid = ctx.task_id if ctx else None

        # Token totals (empty for error calls)
        input_tk = int(usage_metadata.get("input_tokens", 0) or 0) if usage_metadata else 0
        output_tk = int(usage_metadata.get("output_tokens", 0) or 0) if usage_metadata else 0
        total_tk = int(usage_metadata.get("total_tokens", 0) or 0) if usage_metadata else 0
        if total_tk == 0:
            total_tk = input_tk + output_tk

        # Per-model bucket (sparse keys: amount, cache_read_tokens)
        bucket: dict[str, Any] = {}
        if input_tk > 0 or output_tk > 0:
            bucket = {"input_tokens": input_tk, "output_tokens": output_tk, "total_tokens": total_tk}

        # Cache read tokens (sparse)
        if usage_metadata:
            details = usage_metadata.get("input_token_details")
            if isinstance(details, Mapping):
                cache_read = int(details.get("cache_read") or 0)
                if cache_read > 0:
                    bucket["cache_read_tokens"] = cache_read

        # Amount (sparse, two-level read)
        if response_metadata is not None:
            amount = extract_amount_from_metadata(response_metadata)
            if amount is not None:
                bucket["amount"] = amount

        metadata: dict[str, Any] = {"domain": dom, "caller_tag": caller_tag}
        if task_type:
            metadata["task_type"] = task_type

        row = RunRow(
            run_id=f"llm_{uuid4().hex[:12]}",
            thread_id=tid or dom,
            user_id=uid,
            status=status,
            operation_kind="llm_bookkeeping",
            model_name=model_name,
            total_input_tokens=input_tk,
            total_output_tokens=output_tk,
            total_tokens=total_tk,
            llm_call_count=1,
            token_usage_by_model={model_name or "unknown": bucket} if bucket else {},
            metadata_json=metadata,
        )

        async with sf() as session:
            session.add(row)
            await session.commit()

    except Exception:
        logger.debug("llm_usage_sink: write failed", exc_info=True)


# ---------------------------------------------------------------------------
# Fire-and-forget (for sync callers like ViMax wrapper _generate)
# ---------------------------------------------------------------------------


def record_llm_usage_fire_and_forget(**kwargs: Any) -> None:
    """Sync entry point: schedule ``record_llm_usage`` without blocking.

    Three tiers:
    1. Running event loop → ``asyncio.create_task``
    2. No running loop but a known main loop exists → ``run_coroutine_threadsafe``
    3. Neither → drop + log (better than blocking a sync caller)
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(record_llm_usage(**kwargs))
    except RuntimeError:
        # No running loop in this thread — try the main loop via threadsafe.
        if _main_loop is not None and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(record_llm_usage(**kwargs), _main_loop)
        else:
            logger.debug("llm_usage_sink: no running loop for fire-and-forget write, dropping")


_main_loop: asyncio.AbstractEventLoop | None = None


def register_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the main event loop for fire-and-forget calls from sync threads.

    Called once at application startup (``lifespan`` or equivalent). Allows
    ViMax sync paths running in thread pools to schedule accounting writes
    back onto the main event loop via ``run_coroutine_threadsafe``.
    """
    global _main_loop
    _main_loop = loop


# ---------------------------------------------------------------------------
# External API call tracking (F1)
# ---------------------------------------------------------------------------


async def record_external_api_call(
    *,
    provider: str,
    method: str,
    status: str,
    elapsed_ms: int | None = None,
    amount: float | None = None,
    user_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write one ``external_api_calls`` row (F1).

    B1: ``amount`` is the media provider's real-bill cost (360/Ark ``usage.amount``).
    Sparse — only present when the gateway returns it. Kept in metadata_json to avoid
    a schema migration; console reads it from there.

    C1: ``extra`` is merged into metadata_json alongside amount. Used for duration,
    width/height, total_tokens, resolution, model, char_count — the raw data needed
    for cost estimation and type classification.

    批次 C Phase C: ``task_id`` 取自 accounting ctx（``get_llm_accounting_context``），
    任务 ctx 外的调用（如音色克隆上传，发生在建音色而非跑 production 任务）
    task_id=None——正确行为。调用点审计（2026-08-13）：全部调用点都包一层 helper
    （throttle._track / vimax provider_bridge / voice_twin qihu·doubao·volcengine
    client），在 record 内部读 ctx 即全覆盖，无需改任何调用方签名。

    Never raises — tracking failures are logged only.
    """
    try:
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

        sf = get_session_factory()
        if sf is None:
            return

        uid = user_id or _resolve_user_id()
        ctx = get_llm_accounting_context()

        metadata: dict[str, Any] = {}
        if amount is not None:
            metadata["amount"] = amount
        if extra:
            metadata.update(extra)

        row = ExternalApiCallRow(
            call_id=f"apicall_{uuid4().hex[:12]}",
            user_id=uid,
            task_id=ctx.task_id if ctx else None,
            provider=provider,
            method=method,
            status=status,
            elapsed_ms=elapsed_ms,
            metadata_json=metadata if metadata else None,
        )

        async with sf() as session:
            session.add(row)
            await session.commit()

    except Exception:
        logger.debug("llm_usage_sink: external_api_call write failed", exc_info=True)

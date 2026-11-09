"""Run lifecycle service layer.

Centralizes the business logic for creating runs, formatting SSE
frames, and consuming stream bridge events.  Router modules
(``thread_runs``, ``runs``) are thin HTTP handlers that delegate here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import convert_to_messages

from app.gateway.deps import get_checkpointer, get_run_context, get_run_manager, get_stream_bridge, get_thread_store
from app.gateway.utils import sanitize_log_param
from deerflow.agents.middlewares.dynamic_context_middleware import _DYNAMIC_CONTEXT_REMINDER_KEY, _REMINDER_DATE_KEY
from deerflow.agents.middlewares.view_image_middleware import _IMAGE_CONTEXT_MESSAGE_MARKER_KEY
from deerflow.runtime import (
    END_SENTINEL,
    HEARTBEAT_SENTINEL,
    ConflictError,
    DisconnectMode,
    RunManager,
    RunRecord,
    RunStatus,
    StreamBridge,
    UnsupportedStrategyError,
    run_agent,
)
from deerflow.runtime.runs.naming import resolve_root_run_name
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------


def format_sse(event: str, data: Any, *, event_id: str | None = None) -> str:
    """Format a single SSE frame.

    Field order: ``event:`` -> ``data:`` -> ``id:`` (optional) -> blank line.
    This matches the LangGraph Platform wire format consumed by the
    ``useStream`` React hook and the Python ``langgraph-sdk`` SSE decoder.
    """
    payload = json.dumps(data, default=str, ensure_ascii=False)
    parts = [f"event: {event}", f"data: {payload}"]
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append("")
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Input / config helpers
# ---------------------------------------------------------------------------


def normalize_stream_modes(raw: list[str] | str | None) -> list[str]:
    """Normalize the stream_mode parameter to a list.

    Default matches what ``useStream`` expects: values + messages-tuple.
    """
    if raw is None:
        return ["values"]
    if isinstance(raw, str):
        return [raw]
    return raw if raw else ["values"]


# Server-owned message metadata (D-3): these additional_kwargs keys are written
# only by middleware inside the agent runtime. A client that forges them could
# suppress the dynamic-context injection (fake reminder_date) or make its own
# message invisible to the model (fake view-image context marker), so they are
# stripped from every untrusted input message at this boundary.
_SERVER_OWNED_MESSAGE_METADATA_KEYS = frozenset(
    {
        _DYNAMIC_CONTEXT_REMINDER_KEY,
        _REMINDER_DATE_KEY,
        _IMAGE_CONTEXT_MESSAGE_MARKER_KEY,
    }
)


def _strip_external_message_metadata(message: Any) -> Any:
    """Remove server-owned metadata from an untrusted input message."""
    if not isinstance(message, BaseMessage):
        return message
    additional_kwargs = dict(message.additional_kwargs)
    for key in _SERVER_OWNED_MESSAGE_METADATA_KEYS:
        additional_kwargs.pop(key, None)
    if additional_kwargs == message.additional_kwargs:
        return message
    return message.model_copy(update={"additional_kwargs": additional_kwargs})


def normalize_input(raw_input: dict[str, Any] | None) -> dict[str, Any]:
    """Convert LangGraph Platform input format to LangChain state dict.

    Delegates dict→message coercion to ``langchain_core.messages.utils.convert_to_messages``
    so that ``additional_kwargs`` (e.g. uploaded-file metadata — gh #3132), ``id``,
    ``name``, and non-human roles (ai/system/tool) survive unchanged.  An earlier
    hand-rolled version only forwarded ``content`` and collapsed every role to
    ``HumanMessage``, which silently stripped frontend-supplied attachments.

    Malformed message dicts (missing ``role``/``type``/``content``, unsupported
    role, etc.) raise ``HTTPException(400)`` with the offending index, instead
    of bubbling up as a 500.  The gateway is a system boundary, so per-entry
    validation errors are the right shape for clients to retry against.

    Dynamic-context reminder markers and the transient view-image context marker
    are server-owned: external callers cannot supply them (D-3).
    """
    if raw_input is None:
        return {}
    messages = raw_input.get("messages")
    if messages and isinstance(messages, list):
        converted: list[Any] = []
        for index, msg in enumerate(messages):
            if isinstance(msg, BaseMessage):
                converted.append(_strip_external_message_metadata(msg))
            elif isinstance(msg, dict):
                try:
                    converted.extend(_strip_external_message_metadata(m) for m in convert_to_messages([msg]))
                except (ValueError, TypeError, NotImplementedError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid message at input.messages[{index}]: {exc}",
                    ) from exc
            else:
                converted.append(msg)
        return {**raw_input, "messages": converted}
    return raw_input


_DEFAULT_ASSISTANT_ID = "lead_agent"


def resolve_agent_factory(assistant_id: str | None):
    """Resolve the agent factory callable from config.

    Custom agents are implemented as ``lead_agent`` + an ``agent_name``
    injected into ``configurable`` or ``context`` — see
    :func:`build_run_config`.  All ``assistant_id`` values therefore map to the
    same factory; the routing happens inside ``make_lead_agent`` when it reads
    ``cfg["agent_name"]``.
    """
    from deerflow.agents.lead_agent.agent import make_lead_agent

    return make_lead_agent


# Lead-agent recursion budget bounds. The Gateway must NOT trust a
# client-supplied ``recursion_limit`` verbatim: an arbitrarily large value lets
# a single run execute unbounded LangGraph super-steps (each at least one LLM
# call), enabling runaway API cost / DoS. ``_DEFAULT_RECURSION_LIMIT`` is the
# server default when the client sends nothing; the hard ceiling any client
# value is clamped to is configurable via ``AppConfig.max_recursion_limit``.
_DEFAULT_RECURSION_LIMIT = 100
_DEFAULT_MAX_RECURSION_LIMIT = 2500


def _resolve_max_recursion_limit() -> int:
    """Resolve the clamp ceiling from ``AppConfig.max_recursion_limit``.

    Falls back to ``_DEFAULT_MAX_RECURSION_LIMIT`` when the app config cannot be
    loaded (e.g. no ``config.yaml`` in a bare unit-test environment) so that the
    clamp still applies rather than crashing the run-config assembly.
    """
    try:
        from deerflow.config import get_app_config

        return get_app_config().max_recursion_limit
    except Exception:
        return _DEFAULT_MAX_RECURSION_LIMIT


def _clamp_recursion_limit(value: Any, max_limit: int) -> int:
    """Clamp a client-supplied ``recursion_limit`` into a safe server range.

    Non-integer values (including ``bool``, an ``int`` subclass) and non-positive
    values fall back to ``_DEFAULT_RECURSION_LIMIT``; valid positive integers are
    capped at ``max_limit`` (from ``AppConfig.max_recursion_limit``).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return _DEFAULT_RECURSION_LIMIT
    return min(value, max_limit)


def build_run_config(
    thread_id: str,
    request_config: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    *,
    assistant_id: str | None = None,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """Build a RunnableConfig dict for the agent.

    When *assistant_id* refers to a custom agent (anything other than
    ``"lead_agent"`` / ``None``), the name is forwarded as ``agent_name`` in
    whichever runtime options container is active: ``context`` for
    LangGraph >= 0.6.0 requests, otherwise ``configurable``.
    ``make_lead_agent`` reads this key to load the matching
    ``agents/<name>/SOUL.md`` and per-agent config — without it the agent
    silently runs as the default lead agent.

    This mirrors the channel manager's ``_resolve_run_params`` logic so that
    the LangGraph Platform-compatible HTTP API and the IM channel path behave
    identically.
    """
    config: dict[str, Any] = {"recursion_limit": recursion_limit if recursion_limit is not None else _DEFAULT_RECURSION_LIMIT}
    if request_config:
        # LangGraph >= 0.6.0 introduced ``context`` as the preferred way to
        # pass thread-level data and rejects requests that include both
        # ``configurable`` and ``context``.  If the caller already sends
        # ``context``, honour it and skip our own ``configurable`` dict.
        if "context" in request_config:
            if "configurable" in request_config:
                logger.warning(
                    "build_run_config: client sent both 'context' and 'configurable'; preferring 'context' (LangGraph >= 0.6.0). thread_id=%s, caller_configurable keys=%s",
                    thread_id,
                    list(request_config.get("configurable", {}).keys()),
                )
            context_value = request_config["context"]
            if context_value is None:
                context = {}
            elif isinstance(context_value, Mapping):
                context = dict(context_value)
            else:
                raise ValueError("request config 'context' must be a mapping or null.")
            config["context"] = context
        else:
            configurable = {"thread_id": thread_id}
            configurable.update(request_config.get("configurable", {}))
            config["configurable"] = configurable
        for k, v in request_config.items():
            if k not in ("configurable", "context"):
                config[k] = v
    else:
        config["configurable"] = {"thread_id": thread_id}

    # Never trust a client-supplied recursion_limit verbatim: clamp it to a
    # safe server range so a single run cannot execute unbounded LangGraph
    # super-steps (runaway LLM cost / DoS). Applied after the passthrough so it
    # overrides whatever the client sent; the internal ``recursion_limit`` kwarg
    # is clamped identically.
    if (request_config and "recursion_limit" in request_config) or recursion_limit is not None:
        raw = config.get("recursion_limit")
        max_limit = _resolve_max_recursion_limit()
        clamped = _clamp_recursion_limit(raw, max_limit)
        if clamped != raw:
            logger.warning(
                "build_run_config: clamped recursion_limit %r -> %d (max %d). thread_id=%s",
                raw,
                clamped,
                max_limit,
                thread_id,
            )
        config["recursion_limit"] = clamped

    # Inject custom agent name when the caller specified a non-default assistant.
    # Honour an explicit agent_name in the active runtime options container.
    if assistant_id and assistant_id != _DEFAULT_ASSISTANT_ID:
        normalized = assistant_id.strip().lower().replace("_", "-")
        if not normalized or not re.fullmatch(r"[a-z0-9-]+", normalized):
            raise ValueError(f"Invalid assistant_id {assistant_id!r}: must contain only letters, digits, and hyphens after normalization.")
        if "configurable" in config:
            target = config["configurable"]
        elif "context" in config:
            target = config["context"]
        else:
            target = config.setdefault("configurable", {})
        if target is not None and "agent_name" not in target:
            target["agent_name"] = normalized
        config.setdefault("run_name", resolve_root_run_name(config, normalized))
    if metadata:
        config.setdefault("metadata", {}).update(metadata)
    return config


# ---------------------------------------------------------------------------
# Checkpoint state reading (raw checkpointer accessor)
# ---------------------------------------------------------------------------
#
# Upstream serves checkpoint state through a graph-compiled
# ``CheckpointStateAccessor`` with delta materialization; this gateway has
# none of that infrastructure. Checkpoints here always carry complete
# ``channel_values``, so reads degrade to raw checkpointer access — the
# upstream ``_RawCheckpointReadAccessor`` semantics — exposing only the
# surface the regenerate/edit-regenerate prepare payloads need (``aget`` /
# ``ahistory``). ``next``/``tasks`` are not derivable without the compiled
# graph and stay empty.


class _RawCheckpointSnapshot:
    """StateSnapshot-shaped view over a raw checkpoint tuple."""

    __slots__ = ("checkpoint_exists", "config", "values", "metadata", "parent_config", "created_at", "tasks", "tasks_known", "next")

    def __init__(self, config: dict[str, Any], tup: Any | None) -> None:
        self.checkpoint_exists = tup is not None
        self.config = getattr(tup, "config", None) or config
        checkpoint = getattr(tup, "checkpoint", None) or {}
        self.values = dict(checkpoint.get("channel_values") or {})
        self.metadata = dict(getattr(tup, "metadata", None) or {})
        self.parent_config = getattr(tup, "parent_config", None)
        self.created_at = checkpoint.get("ts") or self.metadata.get("created_at", "")
        self.tasks: tuple = ()
        self.tasks_known = False
        self.next: tuple = ()


class _RawCheckpointReadAccessor:
    """Raw checkpointer read accessor.

    Reads do not need the compiled graph because persisted checkpoints carry
    complete ``channel_values``. Writes are unsupported — mutation paths keep
    going through the run lifecycle.
    """

    def __init__(self, checkpointer: Any) -> None:
        self.checkpointer = checkpointer

    async def aget(self, config: dict[str, Any]) -> _RawCheckpointSnapshot:
        tup = await self.checkpointer.aget_tuple(config)
        return _RawCheckpointSnapshot(config, tup)

    async def ahistory(self, config: dict[str, Any], *, limit: int | None = None) -> list[_RawCheckpointSnapshot]:
        if limit is not None and limit <= 0:
            return []
        result: list[_RawCheckpointSnapshot] = []
        before = None
        walk_config = config
        if config.get("configurable", {}).get("checkpoint_id"):
            # Pregel's get_state_history treats config.checkpoint_id as the
            # inclusive start of the walk, while alist(before=...) is
            # exclusive — fetch the anchor explicitly so the walk matches.
            before = config
            walk_config = {
                **config,
                "configurable": {k: v for k, v in config.get("configurable", {}).items() if k != "checkpoint_id"},
            }
            anchor = await self.checkpointer.aget_tuple(before)
            if anchor is not None:
                result.append(_RawCheckpointSnapshot(config, anchor))
        if limit is None or len(result) < limit:
            remaining = None if limit is None else limit - len(result)
            async for tup in self.checkpointer.alist(walk_config, before=before, limit=remaining):
                result.append(_RawCheckpointSnapshot(config, tup))
                if limit is not None and len(result) >= limit:
                    break
        return result


def build_checkpoint_state_accessor(
    request: Request,
    *,
    thread_id: str,
    assistant_id: str | None = None,
    checkpoint_id: str | None = None,
) -> tuple[_RawCheckpointReadAccessor, dict[str, Any]]:
    """Build the raw read accessor plus the config addressing the thread head."""
    config = build_run_config(thread_id, None, None, assistant_id=assistant_id)
    configurable = config.setdefault("configurable", {})
    configurable["checkpoint_ns"] = ""
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return _RawCheckpointReadAccessor(get_checkpointer(request)), config


async def resolve_thread_assistant_id(request: Request, thread_id: str) -> str | None:
    """Return the assistant_id recorded in thread metadata, or ``None``.

    Missing records and store failures degrade to ``None`` (the default lead
    agent): all local assistants share one graph schema, so a read with the
    wrong assistant_id is still correct.
    """
    try:
        thread_store = get_thread_store(request)
        record = await thread_store.get(thread_id)
    except Exception:
        logger.warning("Failed to resolve assistant_id for thread %s", sanitize_log_param(thread_id), exc_info=True)
        return None
    return record.get("assistant_id") if isinstance(record, dict) else None


async def build_thread_checkpoint_state_accessor(
    request: Request,
    *,
    thread_id: str,
    checkpoint_id: str | None = None,
) -> tuple[_RawCheckpointReadAccessor, dict[str, Any]]:
    """Single resolution boundary for checkpoint state reads on a thread."""
    assistant_id = await resolve_thread_assistant_id(request, thread_id)
    return build_checkpoint_state_accessor(
        request,
        thread_id=thread_id,
        assistant_id=assistant_id,
        checkpoint_id=checkpoint_id,
    )


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


async def _gate_agent_runnable(agent_name: str, request: Request) -> None:
    """SEC-H8 core: visibility-gate ONE concrete agent name (404 if invisible).

    Shared by the assistant_id gate (``_assert_assistant_runnable``) and the
    final-effective-agent_name gate at the end of ``start_run`` — agent routing
    is NOT assistant_id-only: the frontend agent chat page routes via
    ``context.agent_name`` (with assistantId pinned to ``lead_agent``), and
    ``body.config.configurable`` passes through verbatim. Any name that
    actually reaches ``make_lead_agent`` must pass ``can_see_agent``.
    """
    normalized = (agent_name or "").strip().lower().replace("_", "-")
    # lead_agent 的两种拼写（下划线原文 / 归一化连字符）都视为默认 assistant。
    if not normalized or normalized in (_DEFAULT_ASSISTANT_ID, _DEFAULT_ASSISTANT_ID.replace("_", "-")):
        return

    from deerflow.config.agents_config import can_see_agent, load_agent_config

    try:
        agent_cfg = load_agent_config(normalized)
    except FileNotFoundError:
        # 条目 6 配套（2026-09-12 缺陷批）：未知/已退役 agent 名——修前放行走旧
        # 失败路径，run 被创建（HTTP 200）后后台裸 FileNotFoundError（带服务器
        # 绝对路径）经 SSE 暴露。现同步 404：不建 run 记录、不泄路径。
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' 不存在或已退役") from None
    except ValueError as e:
        logger.warning("SEC-H8: rejecting agent name %r: %s", normalized, e)
        raise HTTPException(status_code=400, detail="Invalid assistant_id") from e
    if agent_cfg is None:
        return

    from app.gateway.authz import _authenticate

    auth = await _authenticate(request)
    user = auth.user if getattr(auth, "is_authenticated", False) else None
    user_id = str(user.id) if user is not None else None
    is_admin = getattr(user, "system_role", None) == "admin"
    if not can_see_agent(agent_cfg, user_id, is_admin):
        logger.warning(
            "SEC-H8: denying run of agent %r for user %r (admin=%s) — 404",
            normalized,
            user_id,
            is_admin,
        )
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found")


def _apply_retired_agent_aliases(body: Any) -> None:
    """条目 6（2026-09-12 缺陷批，方案 a）：退役 agent 别名改写——API 直调旧名
    （course-ppt/五壳）的 run 改写到承接 agent 启动（前端 retired-mapping 同款映射，
    后端事实源 ``_shared/retired_agents.py``）。

    四个 agent_name 来源**全部**改写（routing 优先级 context > configurable >
    assistant_id，只改一处会被旧名盖回）。必须在 ``_assert_assistant_runnable``
    判定前——对旧名的可见性判定无意义，改写后按承接 agent 判定；gallery run
    路径不带 agent 名（恒默认 lead_agent）不受影响。
    """
    from app.gateway._shared.retired_agents import resolve_retired_agent

    assistant_id = getattr(body, "assistant_id", None)
    if isinstance(assistant_id, str) and assistant_id.strip():
        mapped = resolve_retired_agent(assistant_id)
        if mapped != assistant_id:
            logger.info("retired agent alias: assistant_id %r -> %r", assistant_id, mapped)
            try:
                body.assistant_id = mapped
            except (AttributeError, TypeError, ValueError):
                # frozen/校验模型等无法就地改写——仅记日志（改写尽力而为）
                logger.warning("could not rewrite assistant_id alias in place", exc_info=True)

    def _rewrite_container(container: Any) -> None:
        if not isinstance(container, Mapping):
            return
        name = container.get("agent_name")
        if isinstance(name, str) and name.strip():
            mapped = resolve_retired_agent(name)
            if mapped != name:
                logger.info("retired agent alias: agent_name %r -> %r", name, mapped)
                container["agent_name"] = mapped

    request_config = getattr(body, "config", None)
    if isinstance(request_config, Mapping):
        for container_key in ("configurable", "context"):
            _rewrite_container(request_config.get(container_key))
    _rewrite_container(getattr(body, "context", None))


async def _assert_assistant_runnable(body: Any, request: Request) -> None:
    """SEC-H8 (批次 2 项 2.3 + 复核修复 2026-09-04 二轮): gate EVERY
    client-reachable agent_name source by agent visibility.

    ``start_run`` is the single chokepoint for all 6 run-creation call sites
    (runs.py ×2, thread_runs.py ×3, gallery.py). Agent routing is NOT
    assistant_id-only — the effective agent name can come from any of:

    - ``body.assistant_id`` (validated by build_run_config unless overridden);
    - ``body.config.configurable.agent_name`` (merged verbatim; wins over
      assistant_id at build_run_config:284);
    - ``body.config.context.agent_name`` (LangGraph >= 0.6 container; at
      runtime ``_get_runtime_config`` does ``cfg.update(context)`` — context
      OVERRIDES configurable);
    - top-level ``body.context.agent_name`` (DeerFlow extension; the frontend
      agent chat page routes through it).

    Runtime precedence is context > configurable, but precedence is exactly
    what an attacker games (the first review-fix gate read configurable first
    and let a "visible" name in configurable mask a victim name in context).
    We therefore gate EVERY distinct name from EVERY source — no precedence
    reasoning to get wrong — and do it BEFORE ``create_or_reject`` so a denied
    run leaves no orphan record (which would 409 the thread afterwards).

    Semantics = the shared ``can_see_agent`` predicate: public → everyone
    incl. anonymous; custom with created_by → creator/admin; legacy custom
    without created_by → logged-in users only (免登录一并拒绝, 2026-09-03
    decision). lead_agent / None / unknown names short-circuit.
    """
    candidates: list[str] = []

    def _add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            candidates.append(value)

    _add(getattr(body, "assistant_id", None))
    request_config = getattr(body, "config", None)
    if isinstance(request_config, Mapping):
        for container_key in ("configurable", "context"):
            container = request_config.get(container_key)
            if isinstance(container, Mapping):
                _add(container.get("agent_name"))
    context = getattr(body, "context", None)
    if isinstance(context, Mapping):
        _add(context.get("agent_name"))

    for name in dict.fromkeys(candidates):  # dedupe, preserve order
        await _gate_agent_runnable(name, request)


async def start_run(
    body: Any,
    thread_id: str,
    request: Request,
) -> RunRecord:
    """Create a RunRecord and launch the background agent task.

    Parameters
    ----------
    body : RunCreateRequest
        The validated request body (typed as Any to avoid circular import
        with the router module that defines the Pydantic model).
    thread_id : str
        Target thread.
    request : Request
        FastAPI request — used to retrieve singletons from ``app.state``.
    """
    # 条目 6：退役别名改写——先于 SEC-H8 可见性判定（改写后按承接 agent 判定；
    # 未知 agent 名在 _gate_agent_runnable 同步 404，不再后台裸炸）。
    _apply_retired_agent_aliases(body)
    # SEC-H8: reject invisible custom agents before creating any run record.
    await _assert_assistant_runnable(body, request)

    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    run_ctx = get_run_context(request)

    disconnect = DisconnectMode.cancel if body.on_disconnect == "cancel" else DisconnectMode.continue_

    try:
        record = await run_mgr.create_or_reject(
            thread_id,
            body.assistant_id,
            on_disconnect=disconnect,
            metadata=body.metadata or {},
            kwargs={"input": body.input, "config": body.config},
            multitask_strategy=body.multitask_strategy,
        )
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except UnsupportedStrategyError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc

    # Upsert thread metadata so the thread appears in /threads/search,
    # even for threads that were never explicitly created via POST /threads
    # (e.g. stateless runs).
    #
    # SEC-C1 fail-closed (security plan 2026-09-02 B1, F-B08-001; 复核三轮修订):
    # the get() above is ownership-filtered and returns None BOTH for
    # other-owner rows AND for NULL-owner (shared/legacy) rows — so a bare
    # "re-read still None → abort" conflated the cases below. Triage
    # with one owner-unfiltered read (user_id=None bypasses the filter):
    #   other owner  → fail closed (404, run marked error)
    #   NULL owner   → allowed — matches check_access(require_existing=False)
    #                  semantics used by the router-side ownership gate
    #   row missing  → M15 (requirements-v2 review plan 2026-09-09 窄修): one
    #                  create retry rules out transient jitter; still failing +
    #                  still missing = persistent infra failure → 503 fail-closed
    #                  (run marked error) instead of the legacy warn-and-proceed,
    #                  which grafted the run onto an unowned thread that any
    #                  logged-in user could then read (M1 permissive-read
    #                  amplification). The outer catch-all gets the same 503
    #                  fail-closed semantics (read/update path infra failures).

    async def _triage_row() -> tuple[str, object]:
        """Owner-unfiltered read classified as 'missing' / 'tolerate' / 'foreign'.

        P1-S1（2026-09-30 上游移植，修 B1 联动面）：NULL-owner 行不再停留在
        "tolerate"——首次访问即经 ``claim_unowned`` 原子过户给当前用户
        （SQL 单条 UPDATE ... WHERE user_id IS NULL，并发下唯一赢家）。
        过户输给其他用户 → 改判 "foreign"（洞收敛：此后任何用户读这行
        都走 ownership 过滤）。显式空串 owner 与历史上一样按 tolerate
        处理（claim 语义只认 NULL）。
        """
        row = await run_ctx.thread_store.get(thread_id, user_id=None)
        if not isinstance(row, dict):
            return ("missing", None)
        owner = row.get("user_id")
        effective_user = get_effective_user_id()
        if owner == effective_user or owner == "":
            return ("tolerate", owner)
        if owner is None:
            try:
                claimed = await run_ctx.thread_store.claim_unowned(thread_id, effective_user)
            except Exception:
                # 原子过户失败不阻断本 run（沿用 tolerate 语义），但留下证据
                logger.warning(
                    "claim_unowned failed for thread %s — leaving row NULL-owned",
                    sanitize_log_param(thread_id),
                    exc_info=True,
                )
                return ("tolerate", owner)
            if claimed:
                logger.info(
                    "Thread %s atomically claimed by %s (was NULL-owned)",
                    sanitize_log_param(thread_id),
                    sanitize_log_param(effective_user),
                )
                return ("tolerate", effective_user)
            # Claim lost the race: row is now owned. Classify by fresh truth.
            fresh = await run_ctx.thread_store.get(thread_id, user_id=None)
            fresh_owner = fresh.get("user_id") if isinstance(fresh, dict) else None
            if fresh_owner in (None, "", effective_user):
                return ("tolerate", fresh_owner)
            return ("foreign", fresh_owner)
        return ("foreign", owner)

    async def _abort(status_code: int, run_error: str, detail: str):
        try:
            await run_mgr.set_status(record.run_id, RunStatus.error, error=run_error)
        except Exception:
            logger.warning("Failed to mark run %s as error after thread_meta failure", record.run_id, exc_info=True)
        raise HTTPException(status_code=status_code, detail=detail)

    async def _create_thread_meta() -> None:
        await run_ctx.thread_store.create(
            thread_id,
            assistant_id=body.assistant_id,
            metadata=body.metadata,
        )

    try:
        existing = await run_ctx.thread_store.get(thread_id)
        if existing is None:
            try:
                await _create_thread_meta()
            except Exception:
                verdict, owner = await _triage_row()
                if verdict == "tolerate":
                    logger.info(
                        "Thread %s concurrently created (owner=%r); continuing",
                        sanitize_log_param(thread_id),
                        owner,
                    )
                elif verdict == "foreign":
                    logger.error(
                        "Thread %s exists but is not owned by this user — aborting run start",
                        sanitize_log_param(thread_id),
                    )
                    await _abort(status_code=404, run_error="thread ownership check failed", detail=f"Thread {thread_id} not found")
                else:
                    # M15: not a concurrent create — one retry distinguishes a
                    # transient blip from a persistent infra failure.
                    try:
                        await _create_thread_meta()
                    except Exception:
                        verdict2, owner2 = await _triage_row()
                        if verdict2 == "tolerate":
                            logger.info(
                                "Thread %s concurrently created during create retry (owner=%r); continuing",
                                sanitize_log_param(thread_id),
                                owner2,
                            )
                        elif verdict2 == "foreign":
                            logger.error(
                                "Thread %s exists but is not owned by this user — aborting run start",
                                sanitize_log_param(thread_id),
                            )
                            await _abort(status_code=404, run_error="thread ownership check failed", detail=f"Thread {thread_id} not found")
                        else:
                            logger.error(
                                "Persistent thread_meta create failure for %s — failing closed (503)",
                                sanitize_log_param(thread_id),
                                exc_info=True,
                            )
                            await _abort(status_code=503, run_error="thread metadata store unavailable", detail=f"Thread metadata store unavailable for {thread_id}; run aborted")
        else:
            # 条目 33（2026-09-12 缺陷批）：update_status 单次瞬时重试——create 分支
            # 的一次重试哲学（「transient blip vs persistent infra failure」）对 update
            # 同样成立。存储层 UPDATE 谓词仅 thread_id、幂等（前有 _check_ownership
            # 前置闸），首败未写入时重试一次与首试成功语义等价；再败落外层 catch-all
            # 503 fail-closed（重试放内层 try，不吃掉外层语义）。
            try:
                await run_ctx.thread_store.update_status(thread_id, "running")
            except Exception:
                await run_ctx.thread_store.update_status(thread_id, "running")
    except HTTPException:
        raise
    except Exception:
        # M15: read/update path infra failure — no longer warn-and-proceed.
        logger.error(
            "Failed to upsert thread_meta for %s — failing closed (503)",
            sanitize_log_param(thread_id),
            exc_info=True,
        )
        try:
            await run_mgr.set_status(record.run_id, RunStatus.error, error="thread metadata store unavailable")
        except Exception:
            logger.warning("Failed to mark run %s as error after upsert failure", record.run_id, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Thread metadata store unavailable for {thread_id}; run aborted")

    agent_factory = resolve_agent_factory(body.assistant_id)
    graph_input = normalize_input(body.input)
    config = build_run_config(thread_id, body.config, body.metadata, assistant_id=body.assistant_id)

    # Merge DeerFlow-specific context overrides into configurable.
    # The ``context`` field is a custom extension for the langgraph-compat layer
    # that carries agent configuration (model_name, thinking_enabled, etc.).
    # Only agent-relevant keys are forwarded; unknown keys (e.g. thread_id) are ignored.
    context = getattr(body, "context", None)
    if context:
        _CONTEXT_CONFIGURABLE_KEYS = {
            "model_name",
            "mode",
            "thinking_enabled",
            "reasoning_effort",
            "is_plan_mode",
            "subagent_enabled",
            "max_concurrent_subagents",
            "agent_name",
            "is_bootstrap",
        }
        configurable = config.setdefault("configurable", {})
        for key in _CONTEXT_CONFIGURABLE_KEYS:
            if key in context:
                configurable.setdefault(key, context[key])

    # SEC-H8 二轮复核修复（2026-09-04）：原先在此处对合并后 agent_name 过门禁
    # ——但 (a) 读取顺序 configurable→context 与运行时 _get_runtime_config 的
    # context 覆盖 configurable 镜像相反（可见名掩护 context 里的受害名可绕过），
    # (b) 门禁在 create_or_reject 之后，拒绝会留孤儿 pending run 让线程后续 409。
    # 现改为 start_run 入口处的 _assert_assistant_runnable 对 body 四个来源
    # （assistant_id / config.configurable / config.context / 顶层 context）的
    # 每一个 agent_name 逐一过门禁——建记录前拒绝，且不做任何优先级推理。
    # build_run_config / 上面的 context 合并不会再引入未过门禁的名字。

    stream_modes = normalize_stream_modes(body.stream_mode)

    task = asyncio.create_task(
        run_agent(
            bridge,
            run_mgr,
            record,
            ctx=run_ctx,
            agent_factory=agent_factory,
            graph_input=graph_input,
            config=config,
            stream_modes=stream_modes,
            stream_subgraphs=body.stream_subgraphs,
            interrupt_before=body.interrupt_before,
            interrupt_after=body.interrupt_after,
        )
    )
    record.task = task

    # Title sync is handled by worker.py's finally block which reads the
    # title from the checkpoint and calls thread_store.update_display_name
    # after the run completes.

    return record


async def sse_consumer(
    bridge: StreamBridge,
    record: RunRecord,
    request: Request,
    run_mgr: RunManager,
):
    """Async generator that yields SSE frames from the bridge.

    The ``finally`` block implements ``on_disconnect`` semantics:
    - ``cancel``: abort the background task on client disconnect.
    - ``continue``: let the task run; events are discarded.
    """
    last_event_id = request.headers.get("Last-Event-ID")
    # If the run already reached a terminal status (success/error/timeout/
    # interrupted) — e.g. a reconnect after the in-memory stream was cleaned
    # up, or joining from a different worker — make sure the subscriber
    # receives END instead of hanging on heartbeats (which blocks the SDK
    # submit queue and silently drops every later message).
    if record.status not in (RunStatus.pending, RunStatus.running):
        await bridge.ensure_terminated(record.run_id)
    try:
        async for entry in bridge.subscribe(record.run_id, last_event_id=last_event_id):
            if await request.is_disconnected():
                break

            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
                continue

            if entry is END_SENTINEL:
                yield format_sse("end", None, event_id=entry.id or None)
                return

            yield format_sse(entry.event, entry.data, event_id=entry.id or None)

    finally:
        if record.status in (RunStatus.pending, RunStatus.running):
            if record.on_disconnect == DisconnectMode.cancel:
                await run_mgr.cancel(record.run_id)

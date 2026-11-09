"""Middleware to detect and break repetitive tool call loops.

P0 safety: prevents the agent from calling the same tool with the same
arguments indefinitely until the recursion limit kills the run.

Detection strategy:
  1. After each model response, hash the tool calls (name + args).
  2. Track recent hashes in a sliding window.
  3. If the same hash appears >= warn_threshold times, queue a
     "you are repeating yourself — wrap up" warning for the current
     thread/run. The warning is **injected at the next model call** (in
     ``wrap_model_call``) as a ``HumanMessage`` appended to the message
     list, *after* all ToolMessage responses to the previous
     AIMessage(tool_calls).
  4. If it appears >= hard_limit times, strip all tool_calls from the
     response so the agent is forced to produce a final text answer.

Why the warning is injected at ``wrap_model_call`` instead of
``after_model`` (2026-10-14 加固批，函数级移植上游 #2752):

  ``after_model`` fires immediately after the model emits an
  ``AIMessage`` that may carry ``tool_calls``. The tools node has not
  run yet, so no matching ``ToolMessage`` exists in the history. Any
  message we add here lands *between* the assistant's tool_calls and
  their responses. OpenAI/Moonshot reject the next request with
  ``"tool_call_ids did not have response messages"`` because their
  validators require the assistant's tool_calls to be followed
  immediately by tool messages. Anthropic also disallows mid-stream
  ``SystemMessage``. By deferring the warning to ``wrap_model_call``,
  every prior ToolMessage is already present in the request's message
  list and the warning is appended at the end — pairing intact, no
  ``AIMessage`` semantics are mutated, and nothing enters the checkpoint.

Queued warnings are intentionally transient. If a run ends before the
next model request drains a queued warning, ``after_agent`` drops it
instead of carrying it into a later invocation for the same thread. The
hard-stop path still forces termination when the configured safety limit
is reached.

Run-scoped state (2026-10-14 加固批，函数级移植上游 #5344):

  Hash histories, tool-frequency windows/counters, warning-suppression
  sets and the F4 exemption ledger share a ``(thread_id, run_id)`` key.
  One compiled graph can serve many runs for the same conversation: a
  later user run gets a fresh budget, while repeated graph entries of
  the same Gateway run (hidden goal continuations) share one budget.
  ``after_agent`` clears only transient pending warnings for its own
  scope, not those histories; the sync/async ``before_agent`` hooks stay
  topology-preserving no-ops and never delete a sibling run's pending
  warning. Embedders that omit ``context.run_id`` get an opaque
  per-invocation fallback token anchored on the invocation's
  ``stream_writer`` object — 本仓 langgraph 版本无上游的 ``Runtime.control``，
  且 ``execution_info.run_id`` 恒为 None（实证 2026-10-15）；
  ``Runtime`` 对象逐节点更换、``runtime.context`` dict 可被调用方跨
  invoke 复用，唯 ``stream_writer`` 同 invoke 内稳定、跨 invoke 唯一。
  The bounded map holds strong references while active and is released
  by ``after_agent`` on the normal completion path.

F4（2026-09-29 长任务中断修复计划）— 失败后重试定向豁免：hash 硬停前回溯消息
历史，找到上一次**同调用集**对应的 ToolMessage，若该集合全部 status=="error"
（如沙箱渲染 SIGKILL），本次不硬停、记一次豁免——失败重试不等于死循环。豁免
帽 error_retry_exemption_cap（默认 2）次/哈希/作用域：默认 hard_limit=5 下第 5、6
次消耗两顶豁免放行，第 7 次豁免耗尽仍硬停（真死循环必被掐断，边界由测试钉死）。
豁免计数随 LRU 驱逐与 reset() 两分支清理，防长寿命 gateway 下只增不减。

#6804（2026-10-14 加固批）— warn 级文案允许"先保存再收尾"：被要求把结果写入
文件的 agent 不再被"立即停止所有工具"逼成"口头完成"；hard stop 文案不变。
"""

import hashlib
import json
import logging
import threading
import uuid
from collections import Counter, OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

# Defaults — can be overridden via constructor
_DEFAULT_WARN_THRESHOLD = 3  # inject warning after 3 identical calls
_DEFAULT_HARD_LIMIT = 5  # force-stop after 5 identical calls
_DEFAULT_WINDOW_SIZE = 20  # track last N tool calls
_DEFAULT_MAX_TRACKED_THREADS = 100  # LRU limit for tracked thread/run scopes
_DEFAULT_TOOL_FREQ_WARN = 60  # warn after 60 calls to the same tool type
_DEFAULT_TOOL_FREQ_HARD_LIMIT = 100  # force-stop after 100 calls to the same tool type
_DEFAULT_ERROR_RETRY_EXEMPTIONS = 2  # F4/A3: per-hash per-scope exemption cap

# #2752: one run cannot queue an unbounded warning list.
_MAX_PENDING_WARNINGS_PER_RUN = 4

type _RunScopeKey = tuple[str, str | None]


def _normalize_tool_call_args(raw_args: object) -> tuple[dict, str | None]:
    """Normalize tool call args to a dict plus an optional fallback key.

    Some providers serialize ``args`` as a JSON string instead of a dict.
    We defensively parse those cases so loop detection does not crash while
    still preserving a stable fallback key for non-dict payloads.
    """
    if isinstance(raw_args, dict):
        return raw_args, None

    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}, raw_args

        if isinstance(parsed, dict):
            return parsed, None
        return {}, json.dumps(parsed, sort_keys=True, default=str)

    if raw_args is None:
        return {}, None

    return {}, json.dumps(raw_args, sort_keys=True, default=str)


def _stable_tool_key(name: str, args: dict, fallback_key: str | None) -> str:
    """Derive a stable key from salient args without overfitting to noise."""
    if name == "read_file" and fallback_key is None:
        path = args.get("path") or ""
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        bucket_size = 200
        try:
            start_line = int(start_line) if start_line is not None else 1
        except (TypeError, ValueError):
            start_line = 1
        try:
            end_line = int(end_line) if end_line is not None else start_line
        except (TypeError, ValueError):
            end_line = start_line

        start_line, end_line = sorted((start_line, end_line))
        bucket_start = max(start_line, 1)
        bucket_end = max(end_line, 1)
        bucket_start = (bucket_start - 1) // bucket_size
        bucket_end = (bucket_end - 1) // bucket_size
        return f"{path}:{bucket_start}-{bucket_end}"

    # write_file / str_replace are content-sensitive: same path may be updated
    # with different payloads during iteration. Using only salient fields (path)
    # can collapse distinct calls, so we hash full args to reduce false positives.
    if name in {"write_file", "str_replace"}:
        if fallback_key is not None:
            return fallback_key
        return json.dumps(args, sort_keys=True, default=str)

    salient_fields = ("path", "url", "query", "command", "pattern", "glob", "cmd")
    stable_args = {field: args[field] for field in salient_fields if args.get(field) is not None}
    if stable_args:
        return json.dumps(stable_args, sort_keys=True, default=str)

    if fallback_key is not None:
        return fallback_key

    return json.dumps(args, sort_keys=True, default=str)


def _hash_tool_calls(tool_calls: list[dict]) -> str:
    """Deterministic hash of a set of tool calls (name + stable key).

    This is intended to be order-independent: the same multiset of tool calls
    should always produce the same hash, regardless of their input order.
    """
    # Normalize each tool call to a stable (name, key) structure.
    normalized: list[str] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args, fallback_key = _normalize_tool_call_args(tc.get("args", {}))
        key = _stable_tool_key(name, args, fallback_key)

        normalized.append(f"{name}:{key}")

    # Sort so permutations of the same multiset of calls yield the same ordering.
    normalized.sort()
    blob = json.dumps(normalized, sort_keys=True, default=str)
    return hashlib.md5(blob.encode()).hexdigest()[:12]


# Warn-level messages ask the agent to wrap up, but must not make it drop work
# it was asked to persist: a subagent whose task names an output file would
# otherwise stop *all* tool calls, including the write that delivers its
# result, and finish as "completed" with nothing on disk (#6804, 2026-10-14).
# So the wrap-up allows a bounded save/read/retry before the final answer,
# without reusing a looping writer. The hard-stop path below is unchanged
# (it strips tool calls).
_WRAP_UP_INSTRUCTIONS = (
    "If your task asks you to save results to a file, first write what you have collected so far to that file "
    "with a file-writing tool (for example write_file, unless it is the looping tool). "
    "If the writer is itself looping, use a different available file-writing tool. "
    "If a save is blocked by a read-before-write check, read the target file and retry the save once. "
    "Do not use a tool that this warning tells you to stop, or repeat a failed save loop. "
    "If no permitted save path is available or the retry fails, include the collected results in your final answer "
    "and state that the file was not saved. Then stop calling tools and produce your final answer. "
    "If you cannot complete the task, summarize what you accomplished so far."
)

_WARNING_MSG = "[LOOP DETECTED] You are repeating the same tool calls. Stop repeating them and wrap up now. " + _WRAP_UP_INSTRUCTIONS

_TOOL_FREQ_WARNING_MSG = "[LOOP DETECTED] You have called {tool_name} {count} times without producing a final answer. Stop calling {tool_name} and wrap up now. " + _WRAP_UP_INSTRUCTIONS

_HARD_STOP_MSG = "[FORCED STOP] Repeated tool calls exceeded the safety limit. Producing final answer with results collected so far."

_TOOL_FREQ_HARD_STOP_MSG = "[FORCED STOP] Tool {tool_name} called {count} times — exceeded the per-tool safety limit. Producing final answer with results collected so far."


class LoopDetectionMiddleware(AgentMiddleware[AgentState]):
    """Detects and breaks repetitive tool call loops.

    Args:
        warn_threshold: Number of identical tool call sets before queueing
            a warning for the next model call. Default: 3.
        hard_limit: Number of identical tool call sets before stripping
            tool_calls entirely. Default: 5.
        window_size: Size of the sliding window for tracking calls.
            Default: 20.
        max_tracked_threads: Maximum number of thread/run scopes to track
            before evicting the least recently used. The configuration name
            is retained for compatibility. Default: 100.
        tool_freq_warn: Number of calls to the same tool *type* (regardless
            of arguments) **within the Layer 2 sliding window** before
            queueing a frequency warning. Catches cross-file read loops that
            hash-based detection misses. Default: 60.
        tool_freq_hard_limit: Number of calls to the same tool type within
            the Layer 2 sliding window before forcing a stop. The window is
            sized to at least this effective limit so the stop stays
            reachable. Default: 100.
        error_retry_exemption_cap: F4 — per-hash per-scope cap on "previous
            identical call set failed, let this retry through" exemptions at
            the hash hard limit. Default: 2 (A3, 2026-09-29). 0 disables.
    """

    def __init__(
        self,
        warn_threshold: int = _DEFAULT_WARN_THRESHOLD,
        hard_limit: int = _DEFAULT_HARD_LIMIT,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        max_tracked_threads: int = _DEFAULT_MAX_TRACKED_THREADS,
        tool_freq_warn: int = _DEFAULT_TOOL_FREQ_WARN,
        tool_freq_hard_limit: int = _DEFAULT_TOOL_FREQ_HARD_LIMIT,
        polling_exempt: set[str] | None = None,
        error_retry_exemption_cap: int = _DEFAULT_ERROR_RETRY_EXEMPTIONS,
    ):
        super().__init__()
        self.warn_threshold = warn_threshold
        self.hard_limit = hard_limit
        self.window_size = window_size
        self.max_tracked_threads = max_tracked_threads
        self.tool_freq_warn = tool_freq_warn
        self.tool_freq_hard_limit = tool_freq_hard_limit
        self.error_retry_exemption_cap = max(0, int(error_retry_exemption_cap))
        # Tools exempt from Layer 1 hash detection (status pollers that
        # legitimately call with identical args every cycle).
        self.polling_exempt: set[str] = set(polling_exempt or ())
        self._lock = threading.Lock()
        # Per-scope tracking using OrderedDict for LRU eviction (2026-10-14:
        # re-keyed from thread_id to (thread_id, run_id), upstream #5344).
        self._history: OrderedDict[_RunScopeKey, list[str]] = OrderedDict()
        self._warned: dict[_RunScopeKey, set[str]] = defaultdict(set)
        # A4（2026-10-07 反馈核实批）：Layer 2 频率计数窗口化——最近若干次调用的
        # 工具名序列（per scope），随入窗/出窗维护 ``_tool_freq`` 的窗口内计数。
        # 取代旧的跨 run 终身累计 dict（"线程毒化"问题）。窗口尺寸在计数点按
        # max(window_size, effective hard limit) 动态取值，见 ``_track_and_check``。
        self._tool_freq_window: defaultdict[_RunScopeKey, deque[str]] = defaultdict(deque)
        # Per-scope windowed call counts（Counter 镜像窗口 deque，O(1) 读计数，
        # 入窗 +1 / 出窗 -1）
        self._tool_freq: defaultdict[_RunScopeKey, Counter[str]] = defaultdict(Counter)
        self._tool_freq_warned: dict[_RunScopeKey, set[str]] = defaultdict(set)
        # F4: scope -> call_hash -> exemptions consumed. Lifetime cap per
        # (scope, hash) — reset() has no production caller — so it MUST ride
        # the same LRU eviction / reset() cleanup as the dicts above (P-2).
        self._error_retry_exemptions: dict[_RunScopeKey, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # #2752: per-scope queue of warnings to inject at the next model call.
        # Populated by ``after_model`` (detection) and drained by
        # ``wrap_model_call`` (injection); see module docstring.
        self._pending_warnings: dict[_RunScopeKey, list[str]] = defaultdict(list)
        self._pending_warning_touch_order: OrderedDict[_RunScopeKey, None] = OrderedDict()
        self._max_pending_warning_keys = max(1, self.max_tracked_threads * 2)
        # Invocation-fallback anchors for embedders that omit run_id (see
        # module docstring). Strong reference prevents CPython address reuse
        # while the mapping is live; ``after_agent`` releases normal
        # invocations and the bounded map covers abnormal exits (#5344).
        self._fallback_run_ids: OrderedDict[int, tuple[object, str]] = OrderedDict()
        self._max_fallback_run_ids = max(1, self.max_tracked_threads * 2)

    def _get_thread_id(self, runtime: Runtime) -> str:
        """Extract thread_id from runtime context for per-scope tracking."""
        thread_id = runtime.context.get("thread_id") if runtime.context else None
        if thread_id:
            return str(thread_id)
        return "default"

    def _get_run_id(self, runtime: Runtime) -> str | None:
        """Extract run_id from runtime context for per-scope tracking.

        Context presence is authoritative, including an explicit ``None``:
        callers that legitimately carry ``run_id=None`` keep that value
        instead of being conflated with "absent" under a shared literal.

        When the context has no ``run_id`` key at all, derive an opaque
        per-invocation fallback token. 本仓 langgraph 版本的锚点选择见模块
        docstring：``stream_writer`` 同 invoke 稳定、跨 invoke 唯一；最后
        回落 ``runtime.context`` dict 身份。The anchor is mapped to a
        generated token rather than embedding ``id(anchor)`` in the key,
        because CPython may reuse an address after garbage collection.
        """
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict) and "run_id" in ctx:
            return ctx["run_id"]

        writer = getattr(runtime, "stream_writer", None)
        if writer is not None:
            anchor: object = writer
        elif isinstance(ctx, dict):
            anchor = ctx
        else:
            anchor = runtime
        anchor_id = id(anchor)
        with self._lock:
            existing = self._fallback_run_ids.get(anchor_id)
            if existing is not None and existing[0] is anchor:
                self._fallback_run_ids.move_to_end(anchor_id)
                return existing[1]

            fallback_run_id = f"__invocation__:{uuid.uuid4().hex}"
            self._fallback_run_ids[anchor_id] = (anchor, fallback_run_id)
            self._fallback_run_ids.move_to_end(anchor_id)
            while len(self._fallback_run_ids) > self._max_fallback_run_ids:
                self._fallback_run_ids.popitem(last=False)
            return fallback_run_id

    def _release_fallback_run_id(self, runtime: Runtime) -> None:
        """Release a completed invocation's fallback anchor, if it used one."""
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict) and "run_id" in ctx:
            return

        writer = getattr(runtime, "stream_writer", None)
        if writer is not None:
            anchor: object = writer
        elif isinstance(ctx, dict):
            anchor = ctx
        else:
            anchor = runtime
        anchor_id = id(anchor)
        with self._lock:
            existing = self._fallback_run_ids.get(anchor_id)
            if existing is not None and existing[0] is anchor:
                self._fallback_run_ids.pop(anchor_id, None)

    def _run_scope_key(self, runtime: Runtime) -> _RunScopeKey:
        """Return the shared tracking key for the current thread/run."""
        return self._get_thread_id(runtime), self._get_run_id(runtime)

    def _pending_key(self, runtime: Runtime) -> _RunScopeKey:
        """Return the pending-warning key for the current thread/run."""
        return self._run_scope_key(runtime)

    @staticmethod
    def _stamp_stop_reason(runtime: Runtime, value: str) -> None:
        """F6: stamp *value* into runtime.context["stop_reason"]; never raises."""
        try:
            ctx = getattr(runtime, "context", None)
            if isinstance(ctx, dict):
                ctx.setdefault("stop_reason", value)
        except Exception:
            logger.debug("Failed to stamp stop_reason", exc_info=True)

    def _effective_freq_hard_limit(self) -> int:
        """Effective per-tool-type freq hard limit, linked to the run's
        ``recursion_limit`` via ``min(max(configured, rec), max(rec // 2, configured))``.

        Rationale (2026-08-13 regression fix, v2 formula):
          * A sequential single-tool varying-args loop costs ~2 supersteps per
            round (model + tool), so ``rec // 2`` puts the hard stop ~1
            superstep ahead of the GraphRecursionError wall — it converts the
            crash into a clean forced stop with results collected, and does
            NOT meaningfully save tokens on that shape. Its real early-catch
            value is **parallel multi-call loops**: with k calls per round the
            stop fires in ~rec / (2k) rounds instead of rec / 2.
          * The floor ``max(rec // 2, configured)`` guarantees the effective
            limit never drops below the configured cap — an IM run (rec=100)
            keeps 100 instead of being tightened to 50 (which would sit below
            tool_freq_warn=60 and dead-code the warning).
          * Sequential varying-args loops that this can only catch at the wall
            are the job of the freq warning (@ tool_freq_warn) + TokenBudget.

        ``recursion_limit`` lives on the RunnableConfig. We read it via
        ``get_config()`` — the same pattern uploads_middleware / title_middleware
        use. ``get_config()`` raises outside a runnable context (unit tests), so
        swallow broadly and fall back to the configured default.

        NOTE: ``recursion_limit`` is a TOP-LEVEL RunnableConfig key
        (services.py sets ``config = {"recursion_limit": ...}``), NOT under
        ``configurable``. Do NOT copy the ``.get("configurable", {})`` pattern
        other middlewares use for thread_id/agent_name — that would read None
        here and silently disable the linkage."""
        configured = self.tool_freq_hard_limit
        try:
            # Top-level key, not configurable — see docstring + services.py.
            rec = int(get_config().get("recursion_limit", 0))
        except Exception:
            rec = 0
        if rec <= 0:
            return configured
        return min(max(configured, rec), max(rec // 2, configured))

    def _evict_if_needed(self) -> None:
        """Evict least recently used thread/run scopes if over the limit.

        Must be called while holding self._lock.
        """
        while len(self._history) > self.max_tracked_threads:
            evicted_key, _ = self._history.popitem(last=False)
            self._warned.pop(evicted_key, None)
            self._tool_freq.pop(evicted_key, None)
            self._tool_freq_window.pop(evicted_key, None)
            self._tool_freq_warned.pop(evicted_key, None)
            # P-2（2026-09-29）：豁免字典随 LRU 驱逐清理。
            self._error_retry_exemptions.pop(evicted_key, None)
            self._drop_pending_warning_key_locked(evicted_key)
            logger.debug(
                "Evicted loop tracking for thread/run scope (LRU)",
                extra={"thread_id": evicted_key[0], "run_id": evicted_key[1]},
            )

    # ── #2752: pending-warning bookkeeping ────────────────────────────────

    def _drop_pending_warning_key_locked(self, key: _RunScopeKey) -> None:
        """Drop all pending-warning bookkeeping for one thread/run key.

        Must be called while holding self._lock.
        """
        self._pending_warnings.pop(key, None)
        self._pending_warning_touch_order.pop(key, None)

    def _touch_pending_warning_key_locked(self, key: _RunScopeKey) -> None:
        """Mark a pending-warning key as recently used.

        Must be called while holding self._lock.
        """
        self._pending_warning_touch_order[key] = None
        self._pending_warning_touch_order.move_to_end(key)

    def _prune_pending_warning_state_locked(self, protected_key: _RunScopeKey) -> None:
        """Cap pending-warning state across abnormal or concurrent runs.

        Must be called while holding self._lock.
        """
        overflow = len(self._pending_warning_touch_order) - self._max_pending_warning_keys
        if overflow <= 0:
            return

        candidates = [key for key in self._pending_warning_touch_order if key != protected_key]
        for key in candidates[:overflow]:
            self._drop_pending_warning_key_locked(key)

    def _queue_pending_warning(self, runtime: Runtime, warning: str) -> None:
        """Queue one transient warning for the current thread/run with caps."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            warnings = self._pending_warnings[pending_key]
            if warning not in warnings:
                warnings.append(warning)
            if len(warnings) > _MAX_PENDING_WARNINGS_PER_RUN:
                del warnings[: len(warnings) - _MAX_PENDING_WARNINGS_PER_RUN]
            self._touch_pending_warning_key_locked(pending_key)
            self._prune_pending_warning_state_locked(protected_key=pending_key)

    # ── F4: error-retry exemption (2026-09-29) ───────────────────────────

    def _previous_call_set_failed(self, state: AgentState, call_hash: str) -> bool:
        """Whether the most recent prior occurrence of *call_hash* failed entirely.

        Walks backwards over ``state["messages"]`` (the last message is the
        AIMessage currently under inspection) to the previous AIMessage whose
        hashable tool_calls hash to *call_hash*, then collects the
        ToolMessages that immediately follow it. P-5（2026-09-29）: the match
        is per call SET — every tool_call_id of that set must have a
        ToolMessage and ALL of them must carry ``status == "error"``. A set
        that partially succeeded (or has dangling ids) is not a "failed
        retry" and gets no exemption.
        """
        messages = state.get("messages", [])
        for i in range(len(messages) - 2, -1, -1):
            msg = messages[i]
            if getattr(msg, "type", None) != "ai":
                continue
            prev_calls = getattr(msg, "tool_calls", None) or []
            hashable = [tc for tc in prev_calls if tc.get("name", "") not in self.polling_exempt]
            if not hashable:
                continue
            if _hash_tool_calls(hashable) != call_hash:
                continue
            ids = {tc.get("id") for tc in prev_calls if tc.get("id")}
            if not ids:
                return False
            results: dict[str, str | None] = {}
            for follow in messages[i + 1 :]:
                if isinstance(follow, AIMessage):
                    break
                if isinstance(follow, ToolMessage):
                    cid = getattr(follow, "tool_call_id", None)
                    if cid in ids and cid not in results:
                        results[cid] = getattr(follow, "status", None)
            return all(cid in results for cid in ids) and all(status == "error" for status in results.values())
        return False

    def _maybe_exempt_error_retry(self, state: AgentState, scope_key: _RunScopeKey, call_hash: str, count: int, tool_names: list[str]) -> bool:
        """Consume one F4 exemption for (scope_key, call_hash) if justified.

        Must be called while holding ``self._lock``. Returns True when the
        hard stop is waived: the previous identical call set failed entirely
        AND the per-hash cap is not exhausted. Boundary with defaults
        (hard_limit=5, cap=2): 5th and 6th calls consume the exemptions and
        pass; the 7th finds the cap exhausted and hard-stops — a true
        fail-forever retry loop is still cut off.
        """
        if self.error_retry_exemption_cap <= 0:
            return False
        exemptions = self._error_retry_exemptions[scope_key]
        used = exemptions.get(call_hash, 0)
        if used >= self.error_retry_exemption_cap:
            return False
        if not self._previous_call_set_failed(state, call_hash):
            return False
        exemptions[call_hash] = used + 1
        logger.warning(
            "Loop hard limit reached but previous identical call set failed — exempting error retry (%d/%d, count=%d)",
            used + 1,
            self.error_retry_exemption_cap,
            count,
            extra={"thread_id": scope_key[0], "run_id": scope_key[1], "call_hash": call_hash, "count": count, "tools": tool_names},
        )
        return True

    def _track_and_check(self, state: AgentState, runtime: Runtime) -> tuple[str | None, bool]:
        """Track tool calls and check for loops.

        Two detection layers:
          1. **Hash-based** (existing): catches identical tool call sets.
          2. **Frequency-based** (new): catches the same *tool type* being
             called many times with varying arguments (e.g. ``read_file``
             on 40 different files).

        Returns:
            (warning_message_or_none, should_hard_stop)
        """
        messages = state.get("messages", [])
        if not messages:
            return None, False

        last_msg = messages[-1]
        if getattr(last_msg, "type", None) != "ai":
            return None, False

        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return None, False

        # #5344: derive the scope BEFORE taking the lock — ``_get_run_id``
        # acquires ``self._lock`` for the fallback-anchor map.
        scope_key = self._run_scope_key(runtime)
        thread_id, run_id = scope_key

        # Split out status-polling tools: identical args are expected for them,
        # so they must not participate in hash-based (Layer 1) detection.
        hashable_calls = [tc for tc in tool_calls if tc.get("name", "") not in self.polling_exempt]

        with self._lock:
            # Touch / create entry (move to end for LRU)
            if scope_key in self._history:
                self._history.move_to_end(scope_key)
            else:
                self._history[scope_key] = []
                self._evict_if_needed()

            history = self._history[scope_key]

            # --- Layer 1: hash-based (identical call sets) — polling tools exempt ---
            call_hash: str | None = None
            count = 0
            if hashable_calls:
                call_hash = _hash_tool_calls(hashable_calls)
                history.append(call_hash)
                if len(history) > self.window_size:
                    history[:] = history[-self.window_size :]

                # #2752: prune the warned set to the sliding window so a
                # long-lived scope does not keep every historical warned hash.
                warned_hashes = self._warned.get(scope_key)
                if warned_hashes is not None:
                    warned_hashes.intersection_update(history)
                    if not warned_hashes:
                        self._warned.pop(scope_key, None)

                count = history.count(call_hash)

            tool_names = [tc.get("name", "?") for tc in tool_calls]

            if call_hash is not None and count >= self.hard_limit:
                # F4（2026-09-29）：失败后重试定向豁免——上一次同调用集全部失败时
                # 放行（帽 error_retry_exemption_cap 次/哈希/作用域）。豁免后落到
                # Layer 2 频率计数继续走查，不做提前 return。
                if not self._maybe_exempt_error_retry(state, scope_key, call_hash, count, tool_names):
                    logger.error(
                        "Loop hard limit reached — forcing stop",
                        extra={
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "call_hash": call_hash,
                            "count": count,
                            "tools": tool_names,
                        },
                    )
                    return _HARD_STOP_MSG, True

            if call_hash is not None and count >= self.warn_threshold:
                warned = self._warned[scope_key]
                if call_hash not in warned:
                    warned.add(call_hash)
                    logger.warning(
                        "Repetitive tool calls detected — injecting warning",
                        extra={
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "call_hash": call_hash,
                            "count": count,
                            "tools": tool_names,
                        },
                    )
                    return _WARNING_MSG, False

            # --- Layer 2: per-tool-type frequency (all tools, raised limit) ---
            freq_hard_limit = self._effective_freq_hard_limit()
            # 窗口尺寸红线：窗内计数永不超过 deque 长度，故窗口必须 ≥ 与之比较的
            # hard limit，否则 hard 分支是死代码。Layer 1 的 window_size（默认 20
            # < hard 100）只作下限，不单独决定 Layer 2 窗口；effective hard limit
            # 随 runtime recursion_limit 浮动，故逐次在此取 max 而非构造期静态定长。
            freq_window = max(self.window_size, freq_hard_limit)
            freq = self._tool_freq[scope_key]
            window = self._tool_freq_window[scope_key]
            for tc in tool_calls:
                name = tc.get("name", "")
                if not name:
                    continue
                window.append(name)
                freq[name] += 1
                while len(window) > freq_window:
                    oldest = window.popleft()
                    remaining = freq[oldest] - 1
                    if remaining > 0:
                        freq[oldest] = remaining
                    else:
                        freq.pop(oldest, None)
                tc_count = freq[name]

                if tc_count >= freq_hard_limit:
                    logger.error(
                        "Tool frequency hard limit reached — forcing stop",
                        extra={
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "tool_name": name,
                            "count": tc_count,
                            "limit": freq_hard_limit,
                        },
                    )
                    return _TOOL_FREQ_HARD_STOP_MSG.format(tool_name=name, count=tc_count), True

                if tc_count >= self.tool_freq_warn:
                    warned = self._tool_freq_warned[scope_key]
                    if name not in warned:
                        warned.add(name)
                        logger.warning(
                            "Tool frequency warning — too many calls to same tool type",
                            extra={
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "tool_name": name,
                                "count": tc_count,
                            },
                        )
                        return _TOOL_FREQ_WARNING_MSG.format(tool_name=name, count=tc_count), False
                elif name in self._tool_freq_warned[scope_key]:
                    # 窗口内计数已衰减回 warn 阈值之下——重置 warn 旗标，下一段
                    # 爆发可再次告警（对齐上游 "cleared per name when the
                    # windowed count decays back below the warn threshold"）。
                    self._tool_freq_warned[scope_key].discard(name)

        return None, False

    @staticmethod
    def _append_text(content: str | list | None, text: str) -> str | list:
        """Append *text* to AIMessage content, handling str, list, and None.

        When content is a list of content blocks (e.g. Anthropic thinking mode),
        we append a new ``{"type": "text", ...}`` block instead of concatenating
        a string to a list, which would raise ``TypeError``.
        """
        if content is None:
            return text
        if isinstance(content, list):
            return [*content, {"type": "text", "text": f"\n\n{text}"}]
        if isinstance(content, str):
            return content + f"\n\n{text}"
        # Fallback: coerce unexpected types to str to avoid TypeError
        return str(content) + f"\n\n{text}"

    @staticmethod
    def _build_hard_stop_update(last_msg, content: str | list) -> dict:
        """Clear tool-call metadata so forced-stop messages serialize as plain assistant text."""
        update = {
            "tool_calls": [],
            "content": content,
        }

        additional_kwargs = dict(getattr(last_msg, "additional_kwargs", {}) or {})
        for key in ("tool_calls", "function_call"):
            additional_kwargs.pop(key, None)
        update["additional_kwargs"] = additional_kwargs

        response_metadata = deepcopy(getattr(last_msg, "response_metadata", {}) or {})
        if response_metadata.get("finish_reason") == "tool_calls":
            response_metadata["finish_reason"] = "stop"
        update["response_metadata"] = response_metadata

        return update

    def _apply(self, state: AgentState, runtime: Runtime) -> dict | None:
        warning, hard_stop = self._track_and_check(state, runtime)

        if hard_stop:
            # F6（2026-09-29）：循环硬停打 stop_reason（run 行可区分"被迫收尾"与
            # 正常收尾）；setdefault 语义——更早的终止原因不覆盖。与 slow/empty
            # 计数同模式（runtime.context → worker finally 落库）。
            self._stamp_stop_reason(runtime, "loop_hard_limit")
            # Strip tool_calls from the last AIMessage to force text output.
            # Once tool_calls are stripped, the AIMessage no longer requires
            # matching ToolMessage responses, so mutating it in place here is
            # safe for OpenAI/Moonshot pairing validators (#2752).
            messages = state.get("messages", [])
            last_msg = messages[-1]
            content = self._append_text(last_msg.content, warning or _HARD_STOP_MSG)
            stripped_msg = last_msg.model_copy(update=self._build_hard_stop_update(last_msg, content))
            return {"messages": [stripped_msg]}

        if warning:
            # #2752（2026-10-14 加固批）：warn 级警告改为排队、延迟到下一次模型
            # 请求注入。不得在此改写 AIMessage（会把框架文案塞进模型嘴里、污染
            # Memory/Title 等下游消费者），也不得插入独立非工具消息（tools node
            # 尚未产出 ToolMessage，会破坏 OpenAI/Moonshot 配对）。注入经
            # ``wrap_model_call``，只进请求、不进 checkpoint。
            self._queue_pending_warning(runtime, warning)
            return None

        return None

    # ── #2752/#5344: transient lifecycle hooks ────────────────────────────

    def _clear_current_run_pending_warnings(self, runtime: Runtime) -> None:
        """Drop pending warnings owned by the current thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            self._drop_pending_warning_key_locked(pending_key)

    @staticmethod
    def _format_warning_message(warnings: list[str]) -> str:
        """Merge pending warnings into one prompt message."""
        deduped = list(dict.fromkeys(warnings))
        return "\n\n".join(deduped)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        # #5344: keep this hook in the compiled graph topology. Pending
        # warnings are already run-scoped; touching sibling runs here would
        # break overlapping invocations — after_agent and the bounded queue
        # own cleanup.
        return None

    @override
    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        # Async topology must mirror the sync hook above.
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._apply(state, runtime)

    @override
    def after_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        self._release_fallback_run_id(runtime)
        return None

    @override
    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        self._clear_current_run_pending_warnings(runtime)
        self._release_fallback_run_id(runtime)
        return None

    def _drain_pending_warnings(self, runtime: Runtime) -> list[str]:
        """Pop and return all queued warnings for *runtime*'s thread/run."""
        pending_key = self._pending_key(runtime)
        with self._lock:
            warnings = self._pending_warnings.pop(pending_key, [])
            self._pending_warning_touch_order.pop(pending_key, None)
        return warnings

    def _inject_warnings(self, request: ModelRequest, warnings: list[str]) -> ModelRequest:
        """Append *warnings* to the outgoing message list.

        The warning is placed *after* every existing message, including the
        ToolMessage responses to the previous AIMessage(tool_calls). This
        keeps ``assistant tool_calls -> tool_messages`` pairing intact for
        OpenAI/Moonshot, avoids the Anthropic mid-stream SystemMessage
        restriction (we use HumanMessage), and never mutates an existing
        AIMessage — the injection is request-only and never reaches the
        checkpoint.
        """
        if not warnings:
            return request
        new_messages = [
            *request.messages,
            HumanMessage(content=self._format_warning_message(warnings), name="loop_warning"),
        ]
        return request.override(messages=new_messages)

    def _restore_pending_warnings(self, runtime: Runtime, warnings: list[str]) -> None:
        """Requeue warnings taken for a model call that raised（#5433 移植，2026-10-15 评审二轮）.

        LLMErrorHandlingMiddleware sits outside this middleware and retries a
        failed call by running this wrap again, so the retry must still find
        the warning. It is not queued twice: the ``_warned`` suppression set
        is already set, and the restore dedupes against the current queue.
        """
        if not warnings:
            return
        # _pending_key 可触碰 fallback-anchor 锁——须在 self._lock 之外取键。
        pending_key = self._pending_key(runtime)
        with self._lock:
            queued = self._pending_warnings.setdefault(pending_key, [])
            queued[:0] = [warning for warning in warnings if warning not in queued]
            self._touch_pending_warning_key_locked(pending_key)
            # Restore happens after the failed handler may have admitted newer
            # scopes; reapply the same cap as the normal queue path (#5433).
            self._prune_pending_warning_state_locked(protected_key=pending_key)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        warnings = self._drain_pending_warnings(request.runtime)
        try:
            return handler(self._inject_warnings(request, warnings))
        # CancelledError（BaseException）不入此分支：取消即丢、与上游 #5433 对齐——
        # run 已死，后续新 run 由 before_agent 重置该 run 的全部一次性状态。
        except Exception:
            self._restore_pending_warnings(request.runtime, warnings)
            raise

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        warnings = self._drain_pending_warnings(request.runtime)
        try:
            return await handler(self._inject_warnings(request, warnings))
        # CancelledError（BaseException）不入此分支：取消即丢、与上游 #5433 对齐——
        # run 已死，后续新 run 由 before_agent 重置该 run 的全部一次性状态。
        except Exception:
            self._restore_pending_warnings(request.runtime, warnings)
            raise

    def reset(self, thread_id: str | None = None) -> None:
        """Clear tracking state. If thread_id given, clear only that thread."""
        with self._lock:
            if thread_id:
                for mapping in (
                    self._history,
                    self._warned,
                    self._tool_freq_window,
                    self._tool_freq,
                    self._tool_freq_warned,
                    self._error_retry_exemptions,
                ):
                    for key in list(mapping):
                        if key[0] == thread_id:
                            mapping.pop(key, None)
                # #5344: reset(thread_id) clears every retained run scope of
                # that thread, pending-warning bookkeeping included.
                pending_keys = set(self._pending_warnings) | set(self._pending_warning_touch_order)
                for key in pending_keys:
                    if key[0] == thread_id:
                        self._drop_pending_warning_key_locked(key)
            else:
                self._history.clear()
                self._warned.clear()
                self._tool_freq.clear()
                self._tool_freq_window.clear()
                self._tool_freq_warned.clear()
                self._error_retry_exemptions.clear()
                self._pending_warnings.clear()
                self._pending_warning_touch_order.clear()
                self._fallback_run_ids.clear()


def build_loop_detection_from_config() -> LoopDetectionMiddleware | None:
    """Construct the middleware from config.yaml, or None if disabled.

    Reads the ``loop_detection`` section (Pydantic ``LoopDetectionConfig`` module
    singleton populated by ``load_loop_detection_config_from_dict`` at startup).
    Returns ``None`` when ``enabled`` is False so the caller simply omits it.
    """
    from deerflow.config.loop_detection_config import get_loop_detection_config

    cfg = get_loop_detection_config()
    if not cfg.enabled:
        return None
    return LoopDetectionMiddleware(
        warn_threshold=cfg.hash_warn_threshold,
        hard_limit=cfg.hash_hard_limit,
        window_size=cfg.window_size,
        tool_freq_warn=cfg.tool_freq_warn,
        tool_freq_hard_limit=cfg.tool_freq_hard_limit,
        polling_exempt=set(cfg.polling_exempt),
    )

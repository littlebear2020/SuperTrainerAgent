"""Observe per-run token usage and record threshold crossings.

This is the deliberately-halved counterpart of upstream's
``token_budget_middleware``. Upstream does two things when usage climbs:
it **records** the fact, and it **acts on it** — injecting a
``[TOKEN BUDGET WARNING] ... Wrap up your current work ... Avoid starting new
tool calls`` HumanMessage at the next model call, and stripping ``tool_calls``
off the AIMessage at the hard limit.

We keep only the recording half, on purpose:

- Video-generation runs legitimately consume millions of tokens. Telling the
  agent to wrap up would make it cut storyboards short and deliver a thinner
  video — a silent quality regression that looks like success.
- A budget that changes agent behavior is an enforcement mechanism, and
  enforcement thresholds picked before we have real spend data are guesses.
  Reporting first, deciding later.

So this middleware **never returns a state update and never mutates the
request**. Its hooks are pure observers: they read ``state["messages"]`` and
emit a log line. ``test_token_attention_middleware.py`` asserts message-list
identity across the hooks to keep that property from eroding.

The crossing also has to be visible in the operations console. Rather than
writing a second column, the console derives the flag from the tokens already
persisted on ``RunRow.total_tokens`` (``over_threshold`` in
``routers/console.py``), so a run flagged here and a run flagged in the UI can
never disagree.
"""

from __future__ import annotations

import logging
import threading
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from deerflow.config.token_usage_config import TokenUsageConfig
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

# Cap on remembered run ids, so a long-lived process cannot accumulate state
# for abandoned runs. Crossings are one-shot per run, so the only cost of
# eviction is a duplicate log line for a very old run.
_MAX_TRACKED_RUNS = 1000


class TokenAttentionMiddleware(AgentMiddleware[AgentState]):
    """Log once per run when cumulative token usage crosses the threshold.

    Recording only: contributes no state update, no message injection, and no
    change to ``tool_calls``. See the module docstring for why.
    """

    def __init__(self, config: TokenUsageConfig) -> None:
        super().__init__()
        self._threshold = max(int(getattr(config, "attention_threshold_tokens", 0) or 0), 0)
        self._lock = threading.Lock()
        # run_id -> True once the crossing has been logged.
        self._reported: dict[str, bool] = {}

    @classmethod
    def from_config(cls, config: TokenUsageConfig) -> TokenAttentionMiddleware:
        return cls(config=config)

    @property
    def threshold(self) -> int:
        return self._threshold

    # -- Hooks (observation only) -------------------------------------------

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> None:
        self._observe(state, runtime)
        return None

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> None:
        self._observe(state, runtime)
        return None

    # -- Internals ----------------------------------------------------------

    @staticmethod
    def _run_id(runtime: Runtime) -> str:
        ctx = getattr(runtime, "context", None)
        if isinstance(ctx, dict) and ctx.get("run_id"):
            return str(ctx["run_id"])
        # Fall back to the runtime identity so two concurrent runs without a
        # run_id in context cannot share a bucket.
        return f"runtime:{id(runtime)}"

    @staticmethod
    def _model_name(message: AIMessage) -> str | None:
        metadata = getattr(message, "response_metadata", None) or {}
        if not isinstance(metadata, dict):
            return None
        name = metadata.get("model_name") or metadata.get("model")
        return str(name) if name else None

    @staticmethod
    def _sum_tokens(messages: list) -> int:
        """Cumulative tokens across the AIMessages in the thread history.

        Summing history (rather than tracking deltas) also captures subagent
        tokens, which ``TokenUsageMiddleware`` folds back into the history
        retroactively.

        Counts each ``msg.id`` once. ``RunJournal`` dedups by LangChain run id
        before writing ``RunRow.total_tokens``, which is the value the console
        thresholds on — so counting a repeated message twice here would let this
        middleware flag a run the console does not, breaking the agreement the
        class docstring promises. Messages without an ``id`` cannot be
        identified and are always counted: under-reporting a real crossing is
        worse than double-counting an anonymous message.
        """
        total = 0
        seen_ids: set[str] = set()
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            usage = getattr(message, "usage_metadata", None) or {}
            if not isinstance(usage, dict):
                continue
            message_id = getattr(message, "id", None)
            if message_id:
                if str(message_id) in seen_ids:
                    continue
                seen_ids.add(str(message_id))
            try:
                subtotal = int(usage.get("total_tokens") or 0)
                if subtotal <= 0:
                    subtotal = int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)
            except (TypeError, ValueError):
                continue
            if subtotal > 0:
                total += subtotal
        return total

    def _observe(self, state: AgentState, runtime: Runtime) -> None:
        if self._threshold <= 0:
            return
        messages = state.get("messages") or []
        if not messages:
            return

        used = self._sum_tokens(messages)
        if used < self._threshold:
            return

        run_id = self._run_id(runtime)
        with self._lock:
            if self._reported.get(run_id):
                return
            if len(self._reported) >= _MAX_TRACKED_RUNS:
                # Drop the oldest tracked run (dicts preserve insertion order).
                self._reported.pop(next(iter(self._reported)), None)
            self._reported[run_id] = True

        last = messages[-1]
        model_name = self._model_name(last) if isinstance(last, AIMessage) else None
        try:
            user_id = get_effective_user_id()
        except Exception:  # pragma: no cover - defensive: observation must never break a run
            user_id = None

        logger.warning(
            "token attention threshold crossed: run_id=%s user_id=%s model=%s used=%d threshold=%d",
            run_id,
            user_id,
            model_name,
            used,
            self._threshold,
        )

"""Summarization middleware extensions for DeerFlow.

This middleware uses ``wrap_model_call`` instead of ``before_model`` to perform
context summarization. This ensures ``state["messages"]`` and the checkpoint are
never modified — the full conversation history is always preserved. Only the
**inference window** (what the LLM actually sees) is replaced with
``[summary, preserved_messages]`` when token limits are approached.

A reuse guard prevents the "compression loop" where every model call past the
trigger re-ran the summary model: fresh summaries are persisted to state as
``summarization_checkpoint`` (via ``after_model``), and subsequent calls reuse
the stored summary until message/token growth crosses the resummarize
thresholds (``resummarize_min_new_messages`` / ``resummarize_min_new_tokens``;
both 0 restores legacy always-resummarize behavior). Reuse freezes the cutoff
C0 recorded at summarization time: the window is [summary covering
messages[:C0]] + messages[C0:], so the preserved tail grows — bounded by the
reuse thresholds — until the next fresh summary, and no message silently
leaves the model's view during reuse.
"""

from __future__ import annotations

import html
import logging
import posixpath
import threading
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, override, runtime_checkable

from langchain.agents import AgentState
from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage, get_buffer_string
from langgraph.config import get_config
from langgraph.constants import TAG_NOSTREAM
from langgraph.runtime import Runtime

from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolProtocolError

logger = logging.getLogger(__name__)

# DeerFlow default summary prompt: langchain's DEFAULT_SUMMARY_PROMPT (kept
# section-for-section compatible: SESSION INTENT / SUMMARY / ARTIFACTS /
# NEXT STEPS) plus a mandatory working-state section so interrupted runs
# resume from their artifacts instead of redoing completed steps.
# The {messages} placeholder contract is required by the parent's
# ``summary_prompt.format(messages=...)`` call — it is the only placeholder.
DEERFLOW_DEFAULT_SUMMARY_PROMPT = """<role>
Context Extraction Assistant
</role>

<primary_objective>
Your sole objective in this task is to extract the highest quality/most relevant context from the conversation history below.
</primary_objective>

<objective_information>
You're nearing the total number of input tokens you can accept, so you must extract the highest quality/most relevant pieces of information from your conversation history.
This context will then overwrite the conversation history presented below. Because of this, ensure the context you extract is only the most important information to continue working toward your overall goal.
</objective_information>

<instructions>
The conversation history below will be replaced with the context you extract in this step.
You want to ensure that you don't repeat any actions you've already completed, so the context you extract from the conversation history should be focused on the most important information to your overall goal.

You should structure your summary using the following sections. Each section acts as a checklist - you must populate it with relevant information or explicitly state "None" if there is nothing to report for that section:

## SESSION INTENT
What is the user's primary goal or request? What overall task are you trying to accomplish? This should be concise but complete enough to understand the purpose of the entire session.

## SUMMARY
Extract and record all of the most important context from the conversation history. Include important choices, conclusions, or strategies determined during this conversation. Include the reasoning behind key decisions. Document any rejected options and why they were not pursued.

## ARTIFACTS
What artifacts, files, or resources were created, modified, or accessed during this conversation? For file modifications, list specific file paths and briefly describe the changes made to each. This section prevents silent loss of artifact information.

## 工作状态与产物清单 (WORKING STATE)
List every file or artifact already produced during this conversation with its exact absolute path (e.g. /mnt/user-data/workspace/...). If any manifest files exist (files that track progress, deliverables, or task state), name them and describe exactly what they record. State precisely which step was interrupted or is currently in progress, and which steps are already complete. Completed steps MUST NOT be redone — continuing this task means resuming from this working state, not restarting it.

## NEXT STEPS
What specific tasks remain to be completed to achieve the session intent? What should you do next?

</instructions>

The user will message you with the full message history from which you'll extract context to create a replacement. Carefully read through it all and think deeply about what information is most important to your overall goal and should be saved:

With all of this in mind, please carefully read over the entire conversation history, and extract the most important and relevant context to replace it so that you can free up space in the conversation history.
Respond ONLY with the extracted context. Do not include any additional information, or text before or after the extracted context.

<messages>
Messages to summarize:
{messages}
</messages>"""  # noqa: E501

# Upper bound on stashed per-thread checkpoints awaiting after_model persistence
# (LoopDetection-style protection for the long-lived gateway process).
_MAX_PENDING_CHECKPOINTS = 100


@dataclass(frozen=True)
class SummarizationEvent:
    """Context emitted before conversation history is summarized away."""

    messages_to_summarize: tuple[AnyMessage, ...]
    preserved_messages: tuple[AnyMessage, ...]
    thread_id: str | None
    agent_name: str | None
    runtime: Runtime


@runtime_checkable
class BeforeSummarizationHook(Protocol):
    """Hook invoked before summarization removes messages from state."""

    def __call__(self, event: SummarizationEvent) -> None: ...


def _resolve_thread_id(runtime: Runtime) -> str | None:
    """Resolve the current thread ID from runtime context or LangGraph config."""
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    if thread_id is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        thread_id = config_data.get("configurable", {}).get("thread_id")
    return thread_id


def _resolve_agent_name(runtime: Runtime) -> str | None:
    """Resolve the current agent name from runtime context or LangGraph config."""
    agent_name = runtime.context.get("agent_name") if runtime.context else None
    if agent_name is None:
        try:
            config_data = get_config()
        except RuntimeError:
            return None
        agent_name = config_data.get("configurable", {}).get("agent_name")
    return agent_name


def _tool_call_path(tool_call: dict[str, Any]) -> str | None:
    """Best-effort extraction of a file path argument from a read_file-like tool call."""
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return None
    for key in ("path", "file_path", "filepath"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _clone_ai_message(
    message: AIMessage,
    tool_calls: list[dict[str, Any]],
    *,
    content: Any | None = None,
) -> AIMessage:
    """Clone an AIMessage while replacing its tool_calls list and optional content."""
    update: dict[str, Any] = {"tool_calls": tool_calls}
    if content is not None:
        update["content"] = content
    return message.model_copy(update=update)


def _skill_name_from_path(path: str) -> str:
    """Derive a ledger entry name from a skill file path.

    ``SKILL.md`` maps to its parent directory name (the skill name); any other
    file under the skills root maps to its filename without extension.
    """
    normalized = path.rstrip("/")
    base = posixpath.basename(normalized)
    if base == "SKILL.md":
        return posixpath.basename(posixpath.dirname(normalized)) or "SKILL.md"
    return posixpath.splitext(base)[0] or base


@dataclass
class _SkillBundle:
    """Skill-related tool calls and tool results associated with one AIMessage."""

    ai_index: int
    skill_tool_indices: tuple[int, ...]
    skill_tool_call_ids: frozenset[str]
    skill_tool_tokens: int
    skill_key: str
    # Container paths read by this bundle, in tool-result discovery order.
    # Additive defaulted field: existing consumers never read it (only
    # _build_skill_ledger does), so behavior is unchanged for them.
    skill_paths: tuple[str, ...] = ()


class DeerFlowSummarizationMiddleware(SummarizationMiddleware):
    """Summarization middleware with pre-compression hook dispatch and skill rescue."""

    def __init__(
        self,
        *args,
        skills_container_path: str | None = None,
        skill_file_read_tool_names: Collection[str] | None = None,
        before_summarization: list[BeforeSummarizationHook] | None = None,
        preserve_recent_skill_count: int = 5,
        preserve_recent_skill_tokens: int = 25_000,
        preserve_recent_skill_tokens_per_skill: int = 5_000,
        skill_ledger_max_entries: int = 8,
        resummarize_min_new_messages: int = 20,
        resummarize_min_new_tokens: int = 30_000,
        summary_prompt: str | None = None,
        trim_tokens_to_summarize: int | None = 30_000,
        **kwargs,
    ) -> None:
        super().__init__(
            *args,
            summary_prompt=summary_prompt or DEERFLOW_DEFAULT_SUMMARY_PROMPT,
            trim_tokens_to_summarize=trim_tokens_to_summarize,
            **kwargs,
        )
        self._skills_container_path = skills_container_path or "/mnt/skills"
        self._skill_file_read_tool_names = frozenset(skill_file_read_tool_names or {"read_file", "read", "view", "cat"})
        self._before_summarization_hooks = before_summarization or []
        self._preserve_recent_skill_count = max(0, preserve_recent_skill_count)
        self._preserve_recent_skill_tokens = max(0, preserve_recent_skill_tokens)
        self._preserve_recent_skill_tokens_per_skill = max(0, preserve_recent_skill_tokens_per_skill)
        self._skill_ledger_max_entries = max(0, skill_ledger_max_entries)
        self._resummarize_min_new_messages = max(0, resummarize_min_new_messages)
        self._resummarize_min_new_tokens = max(0, resummarize_min_new_tokens)

        # Per-thread stash for summaries freshly produced in wrap_model_call,
        # handed off to after_model/aafter_model for state persistence
        # (LoopDetection-style thread keying; wrap and after_model always
        # alternate within one run, so at most one entry per thread).
        self._checkpoint_lock = threading.Lock()
        self._pending_checkpoints: dict[str, dict[str, Any]] = {}

        # Build a dedicated summary model tagged with TAG_NOSTREAM so LangGraph's
        # messages-tuple stream handler skips summary tokens, preventing phantom AI
        # messages from being broadcast to the frontend. Existing tags (e.g.
        # "middleware:summarize" bound in agent.py) are preserved via shallow merge.
        existing_tags: list[str] = list(getattr(self.model, "config", {}).get("tags") or [])
        merged_tags = list(existing_tags)
        if TAG_NOSTREAM not in merged_tags:
            merged_tags.append(TAG_NOSTREAM)
        self._summary_model = self.model.with_config(tags=merged_tags)

    # ------------------------------------------------------------------
    # Safe summarization via wrap_model_call — does NOT modify state
    # ------------------------------------------------------------------
    #
    # before_model / abefore_model are overridden to return None so the
    # parent class's destructive RemoveMessage(REMOVE_ALL_MESSAGES) logic
    # never runs. All summarization happens inside wrap_model_call, which
    # only modifies the ModelRequest's inference window (request.messages).
    # The checkpoint and state["messages"] always retain the full history.

    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """No-op — summarization is performed in wrap_model_call to preserve state."""
        return None

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """No-op — summarization is performed in awrap_model_call to preserve state."""
        return None

    # ------------------------------------------------------------------
    # Summary checkpoint persistence via after_model — O(1) stash pop
    # ------------------------------------------------------------------
    #
    # wrap_model_call cannot write state, so a freshly produced summary is
    # stashed on the instance (keyed by thread_id) and persisted here as
    # ``summarization_checkpoint``. The payload (message_count, approx_tokens)
    # is fully computed in wrap — this hook never recounts tokens.

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Persist a freshly produced summary checkpoint; None on reuse/no-op rounds."""
        checkpoint = self._pop_pending_checkpoint(runtime)
        if checkpoint is None:
            return None
        return {"summarization_checkpoint": checkpoint}

    @override
    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Async twin of after_model (same stash, same semantics)."""
        return self.after_model(state, runtime)

    @staticmethod
    def _checkpoint_key(runtime: Runtime | None) -> str:
        thread_id = _resolve_thread_id(runtime) if runtime is not None else None
        return thread_id or "default"

    def _stash_checkpoint(self, request: ModelRequest, *, summary: str, message_count: int, approx_tokens: int, cutoff_index: int) -> None:
        checkpoint = {
            "summary": summary,
            "message_count": message_count,
            "approx_tokens": approx_tokens,
            "cutoff_index": cutoff_index,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._checkpoint_lock:
            while len(self._pending_checkpoints) >= _MAX_PENDING_CHECKPOINTS:
                del self._pending_checkpoints[next(iter(self._pending_checkpoints))]
            self._pending_checkpoints[self._checkpoint_key(request.runtime)] = checkpoint

    def _pop_pending_checkpoint(self, runtime: Runtime | None) -> dict[str, Any] | None:
        with self._checkpoint_lock:
            return self._pending_checkpoints.pop(self._checkpoint_key(runtime), None)

    @staticmethod
    def _read_checkpoint(request: ModelRequest) -> dict[str, Any] | None:
        """Read the persisted summarization checkpoint from request state, if any."""
        state = getattr(request, "state", None)
        if not isinstance(state, dict):
            return None
        checkpoint = state.get("summarization_checkpoint")
        return checkpoint if isinstance(checkpoint, dict) else None

    def _can_reuse_checkpoint(self, checkpoint: dict[str, Any] | None, *, message_count: int, approx_tokens: int) -> bool:
        """Reuse the stored summary while BOTH growth deltas stay under their thresholds.

        Both knobs at 0 restores legacy behavior: always re-summarize (a single
        0 knob disables reuse too, since a non-negative delta is never < 0).
        """
        if not checkpoint:
            return False
        if self._resummarize_min_new_messages == 0 and self._resummarize_min_new_tokens == 0:
            return False
        summary = checkpoint.get("summary")
        prev_count = checkpoint.get("message_count")
        prev_tokens = checkpoint.get("approx_tokens")
        if not summary or not isinstance(prev_count, int) or not isinstance(prev_tokens, int):
            return False
        return message_count - prev_count < self._resummarize_min_new_messages and approx_tokens - prev_tokens < self._resummarize_min_new_tokens

    def _frozen_reuse_cutoff(self, checkpoint: dict[str, Any] | None, *, message_count: int, approx_tokens: int) -> int | None:
        """Return the checkpoint's stored cutoff C0 when the summary may be reused, else None.

        Validity guards (fall through to the fresh path):
          - thresholds not satisfied (see ``_can_reuse_checkpoint``);
          - ``cutoff_index`` missing (pre-2026-09-22 checkpoints) or not an int;
          - ``cutoff <= 0`` (degenerate) or ``cutoff > message_count``
            (checkpoint rollback shrank the history).

        When valid, no pair-safety re-adjustment is needed: messages are
        append-only and C0 was AI/Tool-pair-safe at stash time.
        """
        if not self._can_reuse_checkpoint(checkpoint, message_count=message_count, approx_tokens=approx_tokens):
            return None
        cutoff = checkpoint.get("cutoff_index")
        if not isinstance(cutoff, int) or isinstance(cutoff, bool):
            return None
        if cutoff <= 0 or cutoff > message_count:
            return None
        return cutoff

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """Summarize the inference window without modifying state.

        When a persisted ``summarization_checkpoint`` exists and conversation
        growth since that checkpoint is below the resummarize thresholds, the
        stored summary is reused with its FROZEN cutoff C0: the window is
        [summary covering messages[:C0]] + messages[C0:] (plus skill/dynamic
        context rescued from messages[:C0] on the current state), so the tail
        grows — bounded by the reuse thresholds — until the next fresh summary,
        and no message silently leaves the model's view during reuse. On any
        failure, falls through to the original request so the agent continues
        functioning (model receives the full message list).
        """
        messages = request.messages
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return handler(request)

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return handler(request)

        try:
            checkpoint = self._read_checkpoint(request)
            frozen_cutoff = self._frozen_reuse_cutoff(checkpoint, message_count=len(messages), approx_tokens=total_tokens)
            if frozen_cutoff is not None:
                dropped, preserved = self._partition_with_skill_rescue(messages, frozen_cutoff)
                new_messages = self._build_new_messages(checkpoint["summary"])
                modified_request = request.override(messages=self._assemble_window(new_messages, dropped, preserved))
                return handler(modified_request)

            messages_to_summarize, preserved = self._partition_with_skill_rescue(messages, cutoff_index)
            if request.runtime is not None:
                self._fire_hooks(messages_to_summarize, preserved, request.runtime)

            summary = self._create_summary(messages_to_summarize)
            self._stash_checkpoint(request, summary=summary, message_count=len(messages), approx_tokens=total_tokens, cutoff_index=cutoff_index)
            new_messages = self._build_new_messages(summary)

            modified_request = request.override(messages=self._assemble_window(new_messages, messages_to_summarize, preserved))
            return handler(modified_request)
        except ToolProtocolError:
            # 评审三轮（2026-10-15）：协议终断不是"摘要失败"——回落重调 handler 会把
            # 内层一次性弹消费的终止标志吞掉（模型带原始请求再被打一次）。放行，
            # 但须与 except Exception 同样丢弃本次调用已 stash 的摘要 checkpoint
            # （PR#22 OCR：残留会被同 thread 后续成功 run 的 after_model 持久化成
            # "从未被模型消费过的窗口"）。
            self._pop_pending_checkpoint(request.runtime)
            raise
        except Exception:
            logger.exception("Safe summarization failed; falling back to pass-through")
            # Drop any summary stashed by this call so a later after_model
            # cannot persist a checkpoint for a window that never ran.
            self._pop_pending_checkpoint(request.runtime)
            return handler(request)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """Async summarization of the inference window without modifying state.

        Reuse-guard semantics (including the frozen cutoff) are identical to
        the sync path.
        """
        messages = request.messages
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return await handler(request)

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return await handler(request)

        try:
            checkpoint = self._read_checkpoint(request)
            frozen_cutoff = self._frozen_reuse_cutoff(checkpoint, message_count=len(messages), approx_tokens=total_tokens)
            if frozen_cutoff is not None:
                dropped, preserved = self._partition_with_skill_rescue(messages, frozen_cutoff)
                new_messages = self._build_new_messages(checkpoint["summary"])
                modified_request = request.override(messages=self._assemble_window(new_messages, dropped, preserved))
                return await handler(modified_request)

            messages_to_summarize, preserved = self._partition_with_skill_rescue(messages, cutoff_index)
            if request.runtime is not None:
                self._fire_hooks(messages_to_summarize, preserved, request.runtime)

            summary = await self._acreate_summary(messages_to_summarize)
            self._stash_checkpoint(request, summary=summary, message_count=len(messages), approx_tokens=total_tokens, cutoff_index=cutoff_index)
            new_messages = self._build_new_messages(summary)

            modified_request = request.override(messages=self._assemble_window(new_messages, messages_to_summarize, preserved))
            return await handler(modified_request)
        except ToolProtocolError:
            # 评审三轮（2026-10-15）：协议终断不是"摘要失败"——回落重调 handler 会把
            # 内层一次性弹消费的终止标志吞掉（模型带原始请求再被打一次）。放行，
            # 但须与 except Exception 同样丢弃本次调用已 stash 的摘要 checkpoint
            # （PR#22 OCR：残留会被同 thread 后续成功 run 的 after_model 持久化成
            # "从未被模型消费过的窗口"）。
            self._pop_pending_checkpoint(request.runtime)
            raise
        except Exception:
            logger.exception("Safe summarization failed; falling back to pass-through")
            # Drop any summary stashed by this call so a later after_model
            # cannot persist a checkpoint for a window that never ran.
            self._pop_pending_checkpoint(request.runtime)
            return await handler(request)

    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary using the nostream-tagged model.

        Raises on failure so the caller (wrap_model_call) can fall back to
        pass-through instead of injecting an error string into model context.
        """
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)

        response = self._summary_model.invoke(
            self.summary_prompt.format(messages=formatted_messages).rstrip(),
            config={"metadata": {"lc_source": "summarization"}},
        )
        return response.text.strip()

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Generate summary using the nostream-tagged model (async).

        Raises on failure so the caller (awrap_model_call) can fall back to
        pass-through instead of injecting an error string into model context.
        """
        if not messages_to_summarize:
            return "No previous conversation history."

        trimmed_messages = self._trim_messages_for_summary(messages_to_summarize)
        if not trimmed_messages:
            return "Previous conversation was too long to summarize."

        formatted_messages = get_buffer_string(trimmed_messages)

        response = await self._summary_model.ainvoke(
            self.summary_prompt.format(messages=formatted_messages).rstrip(),
            config={"metadata": {"lc_source": "summarization"}},
        )
        return response.text.strip()

    @override
    def _build_new_messages(self, summary: str) -> list[HumanMessage]:
        """Override the base implementation to let the human message with the special name 'summary'.
        And this message will be ignored to display in the frontend, but still can be used as context for the model.
        """
        return [HumanMessage(content=f"Here is a summary of the conversation to date:\n\n{summary}", name="summary")]

    def _partition_with_skill_rescue(
        self,
        messages: list[AnyMessage],
        cutoff_index: int,
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Partition like the parent, then rescue recently-loaded skill bundles."""
        to_summarize, preserved = self._partition_messages(messages, cutoff_index)
        # D-3: rescue dynamic-context reminders (date/memory) and their ID-swap
        # peers before the skill pass — the two rescue sets are disjoint (this one
        # is System/Human messages, the skill one is AI+Tool pairs), so order only
        # matters for making every early return below carry the reminder rescue.
        to_summarize, preserved = self._preserve_dynamic_context_reminders(to_summarize, preserved)

        if self._preserve_recent_skill_count == 0 or self._preserve_recent_skill_tokens == 0 or not to_summarize:
            return to_summarize, preserved

        try:
            bundles = self._find_skill_bundles(to_summarize, self._skills_container_path)
        except Exception:
            logger.exception("Skill-preserving summarization rescue failed; falling back to default partition")
            return to_summarize, preserved

        if not bundles:
            return to_summarize, preserved

        rescue_bundles = self._select_bundles_to_rescue(bundles)
        if not rescue_bundles:
            return to_summarize, preserved

        bundles_by_ai_index = {bundle.ai_index: bundle for bundle in rescue_bundles}
        rescue_tool_indices = {idx for bundle in rescue_bundles for idx in bundle.skill_tool_indices}
        rescued: list[AnyMessage] = []
        remaining: list[AnyMessage] = []
        for i, msg in enumerate(to_summarize):
            bundle = bundles_by_ai_index.get(i)
            if bundle is not None and isinstance(msg, AIMessage):
                rescued_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") in bundle.skill_tool_call_ids]
                remaining_tool_calls = [tc for tc in msg.tool_calls if tc.get("id") not in bundle.skill_tool_call_ids]

                if rescued_tool_calls:
                    rescued.append(_clone_ai_message(msg, rescued_tool_calls, content=""))
                if remaining_tool_calls or msg.content:
                    remaining.append(_clone_ai_message(msg, remaining_tool_calls))
                continue

            if i in rescue_tool_indices:
                rescued.append(msg)
                continue

            remaining.append(msg)

        return remaining, rescued + preserved

    def _assemble_window(
        self,
        new_messages: list[HumanMessage],
        dropped_after_rescue: list[AnyMessage],
        preserved: list[AnyMessage],
    ) -> list[AnyMessage]:
        """Assemble the inference window as [summary block, skill ledger?, preserved].

        The ledger (when any un-rescued skill reads were compressed away) sits
        between the summary block and the preserved tail: it points the model
        at skills it already loaded, without carrying their bodies. The last
        window message is always the original last conversation message — the
        ledger never disturbs the turn structure.
        """
        window: list[AnyMessage] = list(new_messages)
        ledger = self._build_skill_ledger(dropped_after_rescue)
        if ledger is not None:
            window.append(ledger)
        window.extend(preserved)
        return window

    def _build_skill_ledger(self, messages_to_summarize: list[AnyMessage]) -> HumanMessage | None:
        """Build a compact name+path ledger for skill reads about to be compressed away.

        Scans the post-rescue ``messages_to_summarize``: whatever skill bundles
        remain there are exactly the ones this summarization folds into prose —
        the complement of the rescue set. Window-level only: the returned
        message never enters state/checkpoint (same contract as the
        ``name="summary"`` message). Entries dedupe by path with recency
        refresh and the cap keeps the NEWEST reads (upstream ThreadState
        skill_context merge semantics). Returns None when the ledger is
        disabled (``skill_ledger_max_entries=0``) or there is nothing to list.
        """
        if self._skill_ledger_max_entries <= 0 or not messages_to_summarize:
            return None

        try:
            bundles = self._find_skill_bundles(messages_to_summarize, self._skills_container_path)
        except Exception:
            logger.exception("Skill ledger build failed; skipping ledger injection")
            return None
        if not bundles:
            return None

        # Ordered dedup by path: a re-read moves the path to the end so the
        # cap below keeps the most recently read skills.
        path_order: dict[str, None] = {}
        for bundle in bundles:
            for path in bundle.skill_paths:
                path_order.pop(path, None)
                path_order[path] = None

        capped = list(path_order)[-self._skill_ledger_max_entries :]
        lines = ["## Active skills (loaded earlier - re-read the file before applying its instructions)"]
        for path in capped:
            # Paths come from model tool-call args (untrusted) — escape so a
            # value cannot forge tags in the window (R-16 render-site rule).
            name = html.escape(_skill_name_from_path(path), quote=False)
            lines.append(f"- {name} -> {html.escape(path, quote=False)}")
        return HumanMessage(content="\n".join(lines), name="skill_ledger")

    def _preserve_dynamic_context_reminders(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
    ) -> tuple[list[AnyMessage], list[AnyMessage]]:
        """Keep hidden dynamic-context reminders and their ID-swap peers out of summary compression.

        These reminders carry the current date and optional memory. If summarization
        removes them, DynamicContextMiddleware can lose the already-injected reminder
        and inject a replacement into the wrong point of the conversation.

        The ID-swap triplet produced by ``_make_reminder_and_user_messages`` contains
        three messages: ``SystemMessage(id=X)`` and ``HumanMessage(id=X__memory)`` are
        both tagged with ``dynamic_context_reminder=True``, but ``HumanMessage(id=X__user)``
        carries the original user content and is **not** tagged. Without peer rescue,
        ``__user`` would stay in ``to_summarize`` and be compressed into prose — orphaning
        the tagged messages and losing the user question from the model's direct context.

        This method rescues tagged reminders and also rescues any untagged messages whose
        ``id`` shares the same ``stable_id`` prefix (i.e. ``X__user``, ``X__memory``).
        """
        from deerflow.agents.middlewares.dynamic_context_middleware import is_dynamic_context_reminder

        reminders = [msg for msg in messages_to_summarize if is_dynamic_context_reminder(msg)]
        if not reminders:
            return messages_to_summarize, preserved_messages

        # Collect the base IDs (the stable_id prefix) from tagged reminders.
        # For a reminder with id="ctx-001__memory", the base is "ctx-001".
        # For a reminder with id="ctx-001" (SystemMessage), the base is "ctx-001".
        # removesuffix is suffix-only — it won't strip a "__" that sits in the
        # middle of a stable_id (e.g. "ctx__001" stays intact, unlike rsplit
        # which would mis-derive "ctx").  Only known ID-swap suffixes (__memory,
        # __user) are stripped; __user is not tagged so won't appear in reminders,
        # but is included defensively.
        reminder_base_ids: set[str] = set()
        for msg in reminders:
            if msg.id:
                base = msg.id.removesuffix("__memory").removesuffix("__user")
                reminder_base_ids.add(base)

        # Single-pass partition: walk messages_to_summarize in chronological order
        # and rescue both tagged reminders and untagged ID-swap peers (whose id
        # starts with a known base + "__").  This preserves the original message
        # order within rescued — critical when multiple triplets land in one
        # summarization window.
        rescued: list[AnyMessage] = []
        remaining: list[AnyMessage] = []
        for msg in messages_to_summarize:
            if is_dynamic_context_reminder(msg) or (msg.id and any(msg.id.startswith(b + "__") for b in reminder_base_ids)):
                rescued.append(msg)
            else:
                remaining.append(msg)
        return remaining, rescued + preserved_messages

    def _find_skill_bundles(
        self,
        messages: list[AnyMessage],
        skills_root: str,
    ) -> list[_SkillBundle]:
        """Locate AIMessage + paired ToolMessage groups that load skill files."""
        bundles: list[_SkillBundle] = []
        n = len(messages)
        i = 0
        while i < n:
            msg = messages[i]
            if not (isinstance(msg, AIMessage) and msg.tool_calls):
                i += 1
                continue

            tool_calls = list(msg.tool_calls)
            skill_paths_by_id: dict[str, str] = {}
            for tc in tool_calls:
                if self._is_skill_tool_call(tc, skills_root):
                    tc_id = tc.get("id")
                    path = _tool_call_path(tc)
                    if tc_id and path:
                        skill_paths_by_id[tc_id] = path

            if not skill_paths_by_id:
                i += 1
                continue

            skill_tool_tokens = 0
            skill_key_parts: list[str] = []
            skill_tool_indices: list[int] = []
            matched_skill_call_ids: set[str] = set()

            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):
                j += 1

            for k in range(i + 1, j):
                tool_msg = messages[k]
                if isinstance(tool_msg, ToolMessage) and tool_msg.tool_call_id in skill_paths_by_id:
                    skill_tool_tokens += self.token_counter([tool_msg])
                    skill_key_parts.append(skill_paths_by_id[tool_msg.tool_call_id])
                    skill_tool_indices.append(k)
                    matched_skill_call_ids.add(tool_msg.tool_call_id)

            if not skill_tool_indices:
                i = j
                continue

            bundles.append(
                _SkillBundle(
                    ai_index=i,
                    skill_tool_indices=tuple(skill_tool_indices),
                    skill_tool_call_ids=frozenset(matched_skill_call_ids),
                    skill_tool_tokens=skill_tool_tokens,
                    skill_key="|".join(sorted(skill_key_parts)),
                    skill_paths=tuple(skill_key_parts),
                )
            )
            i = j

        return bundles

    def _select_bundles_to_rescue(self, bundles: list[_SkillBundle]) -> list[_SkillBundle]:
        """Pick bundles to keep, walking newest-first under count/token budgets."""
        selected: list[_SkillBundle] = []
        if not bundles:
            return selected

        seen_skill_keys: set[str] = set()
        total_tokens = 0
        kept = 0

        for bundle in reversed(bundles):
            if kept >= self._preserve_recent_skill_count:
                break
            if bundle.skill_key in seen_skill_keys:
                continue
            if bundle.skill_tool_tokens > self._preserve_recent_skill_tokens_per_skill:
                continue
            if total_tokens + bundle.skill_tool_tokens > self._preserve_recent_skill_tokens:
                continue

            selected.append(bundle)
            total_tokens += bundle.skill_tool_tokens
            kept += 1
            seen_skill_keys.add(bundle.skill_key)

        selected.reverse()
        return selected

    def _is_skill_tool_call(self, tool_call: dict[str, Any], skills_root: str) -> bool:
        """Return True when ``tool_call`` reads a file under the configured skills root."""
        name = tool_call.get("name") or ""
        if name not in self._skill_file_read_tool_names:
            return False
        path = _tool_call_path(tool_call)
        if not path:
            return False
        normalized_root = skills_root.rstrip("/")
        return path == normalized_root or path.startswith(normalized_root + "/")

    def _fire_hooks(
        self,
        messages_to_summarize: list[AnyMessage],
        preserved_messages: list[AnyMessage],
        runtime: Runtime,
    ) -> None:
        if not self._before_summarization_hooks:
            return

        event = SummarizationEvent(
            messages_to_summarize=tuple(messages_to_summarize),
            preserved_messages=tuple(preserved_messages),
            thread_id=_resolve_thread_id(runtime),
            agent_name=_resolve_agent_name(runtime),
            runtime=runtime,
        )

        for hook in self._before_summarization_hooks:
            try:
                hook(event)
            except Exception:
                hook_name = getattr(hook, "__name__", None) or type(hook).__name__
                logger.exception("before_summarization hook %s failed", hook_name)

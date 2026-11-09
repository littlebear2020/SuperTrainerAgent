import logging
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig

from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.memory.summarization_hook import memory_flush_hook
from deerflow.agents.middlewares.clarification_middleware import ClarificationMiddleware
from deerflow.agents.middlewares.loop_detection_middleware import build_loop_detection_from_config
from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.agents.middlewares.model_length_finish_reason_middleware import ModelLengthFinishReasonMiddleware
from deerflow.agents.middlewares.safety_finish_reason_middleware import SafetyFinishReasonMiddleware
from deerflow.agents.middlewares.slow_llm_call_middleware import SlowLLMCallMiddleware
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.agents.middlewares.summarization_middleware import BeforeSummarizationHook, DeerFlowSummarizationMiddleware
from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.agents.middlewares.token_attention_middleware import TokenAttentionMiddleware
from deerflow.agents.middlewares.token_usage_middleware import TokenUsageMiddleware
from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware
from deerflow.agents.thread_state import ThreadState
from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.config.memory_config import get_memory_config
from deerflow.config.summarization_config import get_summarization_config
from deerflow.models import create_chat_model
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _get_runtime_config(config: RunnableConfig) -> dict:
    """Merge legacy configurable options with LangGraph runtime context."""
    cfg = dict(config.get("configurable", {}) or {})
    context = config.get("context", {}) or {}
    if isinstance(context, dict):
        cfg.update(context)
    return cfg


def _resolve_model_name(requested_model_name: str | None = None, *, app_config: AppConfig | None = None) -> str:
    """Resolve a runtime model name safely, falling back to default if invalid. Returns None if no models are configured."""
    app_config = app_config or get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware(*, app_config: AppConfig | None = None) -> DeerFlowSummarizationMiddleware | None:
    """Create and configure the summarization middleware from config."""
    config = get_summarization_config()

    if not config.enabled:
        return None

    # Prepare trigger parameter
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # Prepare keep parameter
    keep = config.keep.to_tuple()

    # Prepare model parameter.
    # Bind "middleware:summarize" tag so RunJournal identifies these LLM calls
    # as middleware rather than lead_agent (SummarizationMiddleware is a
    # LangChain built-in, so we tag the model at creation time).
    if config.model_name:
        model = create_chat_model(name=config.model_name, thinking_enabled=False, app_config=app_config, attach_tracing=False)
    else:
        model = create_chat_model(thinking_enabled=False, app_config=app_config, attach_tracing=False)
    model = model.with_config(tags=["middleware:summarize"])

    # Prepare kwargs
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    hooks: list[BeforeSummarizationHook] = []
    if get_memory_config().enabled:
        hooks.append(memory_flush_hook)

    # The logic below relies on two assumptions holding true: this factory is
    # the sole entry point for DeerFlowSummarizationMiddleware, and the runtime
    # config is not expected to change after startup.
    try:
        resolved_app_config = app_config or get_app_config()
        skills_container_path = resolved_app_config.skills.container_path or "/mnt/skills"
    except Exception:
        logger.exception("Failed to resolve skills container path; falling back to default")
        skills_container_path = "/mnt/skills"

    return DeerFlowSummarizationMiddleware(
        **kwargs,
        skills_container_path=skills_container_path,
        skill_file_read_tool_names=config.skill_file_read_tool_names,
        before_summarization=hooks,
        preserve_recent_skill_count=config.preserve_recent_skill_count,
        preserve_recent_skill_tokens=config.preserve_recent_skill_tokens,
        preserve_recent_skill_tokens_per_skill=config.preserve_recent_skill_tokens_per_skill,
        skill_ledger_max_entries=config.skill_ledger_max_entries,
        resummarize_min_new_messages=config.resummarize_min_new_messages,
        resummarize_min_new_tokens=config.resummarize_min_new_tokens,
    )


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """Create and configure the TodoList middleware.

    Args:
        is_plan_mode: Whether to enable plan mode with TodoList middleware.

    Returns:
        TodoMiddleware instance if plan mode is enabled, None otherwise.
    """
    if not is_plan_mode:
        return None

    # Custom prompts matching DeerFlow's style
    system_prompt = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Best Practices:**
- Break down complex tasks into smaller, actionable steps
- Use clear, descriptive task names
- Remove tasks that become irrelevant
- Add new tasks discovered during implementation
- Don't be afraid to revise the todo list as you learn more

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""

    tool_description = """Use this tool to create and manage a structured task list for complex work sessions.

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## When to Use

Use this tool in these scenarios:
1. **Complex multi-step tasks**: When a task requires 3 or more distinct steps or actions
2. **Non-trivial tasks**: Tasks requiring careful planning or multiple operations
3. **User explicitly requests todo list**: When the user directly asks you to track tasks
4. **Multiple tasks**: When users provide a list of things to be done
5. **Dynamic planning**: When the plan may need updates based on intermediate results

## When NOT to Use

Skip this tool when:
1. The task is straightforward and takes less than 3 steps
2. The task is trivial and tracking provides no benefit
3. The task is purely conversational or informational
4. It's clear what needs to be done and you can just do it

## How to Use

1. **Starting a task**: Mark it as `in_progress` BEFORE beginning work
2. **Completing a task**: Mark it as `completed` IMMEDIATELY after finishing
3. **Updating the list**: Add new tasks, remove irrelevant ones, or update descriptions as needed
4. **Multiple updates**: You can make several updates at once (e.g., complete one task and start the next)

## Task States

- `pending`: Task not yet started
- `in_progress`: Currently working on (can have multiple if tasks run in parallel)
- `completed`: Task finished successfully

## Task Completion Requirements

**CRITICAL: Only mark a task as completed when you have FULLY accomplished it.**

Never mark a task as completed if:
- There are unresolved issues or errors
- Work is partial or incomplete
- You encountered blockers preventing completion
- You couldn't find necessary resources or dependencies
- Quality standards haven't been met

If blocked, keep the task as `in_progress` and create a new task describing what needs to be resolved.

## Best Practices

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
- Remove tasks that are no longer relevant
- **IMPORTANT**: When you write the todo list, mark your first task(s) as `in_progress` immediately
- **IMPORTANT**: Unless all tasks are completed, always have at least one task `in_progress` to show progress

Being proactive with task management demonstrates thoroughness and ensures all requirements are completed successfully.

**Remember**: If you only need a few tool calls to complete a task and it's clear what to do, it's better to just do the task directly and NOT use this tool at all.
"""

    return TodoMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# ThreadDataMiddleware must be before SandboxMiddleware to ensure thread_id is available
# UploadsMiddleware should be after ThreadDataMiddleware to access thread_id
# DanglingToolCallMiddleware patches missing ToolMessages before model sees the history
# SummarizationMiddleware should be early to reduce context before other processing
# TodoListMiddleware should be before ClarificationMiddleware to allow todo management
# TitleMiddleware generates title after first exchange
# MemoryMiddleware queues conversation for memory update (after TitleMiddleware)
# ViewImageMiddleware should be before ClarificationMiddleware to inject image details before LLM
# ToolErrorHandlingMiddleware should be before ClarificationMiddleware to convert tool exceptions to ToolMessages
# ClarificationMiddleware should be last to intercept clarification requests after model calls
def _build_middlewares(
    config: RunnableConfig,
    model_name: str | None,
    agent_name: str | None = None,
    custom_middlewares: list[AgentMiddleware] | None = None,
    *,
    memory_write_agent_name: str | None = None,
    memory_scope: str | None = None,
    app_config: AppConfig | None = None,
):
    """Build middleware chain based on runtime configuration.

    Args:
        config: Runtime configuration containing configurable options like is_plan_mode.
        agent_name: Agent name for prompt loading and per-agent memory read.
        memory_write_agent_name: Target for memory writes. None = global, string = per-agent.
        memory_scope: Agent config's memory_scope; "none" omits MemoryMiddleware
            entirely (D-5 — no extraction LLM call after each run).
        custom_middlewares: Optional list of custom middlewares to inject into the chain.

    Returns:
        List of middleware instances.
    """
    resolved_app_config = app_config or get_app_config()
    middlewares = build_lead_runtime_middlewares(app_config=resolved_app_config, lazy_init=True)

    # Always inject current date (and optionally memory) as <system-reminder> into the
    # first HumanMessage to keep the system prompt fully static for prefix-cache
    # reuse (D-3). memory_scope: "none" suppresses the memory block here too.
    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware

    middlewares.append(DynamicContextMiddleware(agent_name=agent_name, memory_scope=memory_scope))

    # Add summarization middleware if enabled
    summarization_middleware = _create_summarization_middleware(app_config=resolved_app_config)
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # Add TodoList middleware if plan mode is enabled
    cfg = _get_runtime_config(config)
    is_plan_mode = cfg.get("is_plan_mode", False)
    todo_list_middleware = _create_todo_list_middleware(is_plan_mode)
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)

    # Add TokenUsageMiddleware when token_usage tracking is enabled
    if resolved_app_config.token_usage.enabled:
        middlewares.append(TokenUsageMiddleware())
        # Pure observer: logs a run whose usage crosses the attention
        # threshold. Deliberately does NOT warn the model or strip tool_calls
        # (see token_attention_middleware's module docstring).
        middlewares.append(TokenAttentionMiddleware(resolved_app_config.token_usage))

    # Add TitleMiddleware
    middlewares.append(TitleMiddleware())

    # Add MemoryMiddleware (after TitleMiddleware) — unless the agent opts out
    # of memory entirely via memory_scope: none (D-5).
    # Use memory_write_agent_name to determine write target (global vs per-agent)
    # None = global user memory, string = per-agent memory
    if memory_scope != "none":
        middlewares.append(MemoryMiddleware(agent_name=memory_write_agent_name))

    # Add ViewImageMiddleware only if the current model supports vision.
    # Use the resolved runtime model_name from make_lead_agent to avoid stale config values.
    model_config = resolved_app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # Add DeferredToolFilterMiddleware to hide deferred tool schemas from model binding
    if resolved_app_config.tool_search.enabled:
        from deerflow.agents.middlewares.deferred_tool_filter_middleware import DeferredToolFilterMiddleware

        middlewares.append(DeferredToolFilterMiddleware())

    # Coalesce every SystemMessage into a single leading one before the request
    # reaches the provider. Strict backends (vLLM, SGLang, Qwen, Anthropic)
    # reject non-leading SystemMessages — hard dependency of
    # DynamicContextMiddleware's mid-history SystemMessage reminders (D-3).
    from deerflow.agents.middlewares.system_message_coalescing_middleware import SystemMessageCoalescingMiddleware

    middlewares.append(SystemMessageCoalescingMiddleware())

    # Add SubagentLimitMiddleware to truncate excess parallel task calls
    subagent_enabled = cfg.get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
        # max_total comes from the subagents config section (D3: per-run cap).
        from deerflow.config.subagents_config import get_subagents_app_config

        max_total_subagents = get_subagents_app_config().max_total
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents, max_total=max_total_subagents))

    # LoopDetectionMiddleware — detect and break repetitive tool call loops
    # (config-driven: omitted entirely when loop_detection.enabled is False)
    _loop_mw = build_loop_detection_from_config()
    if _loop_mw is not None:
        middlewares.append(_loop_mw)

    # SafetyFinishReasonMiddleware — suppress truncated tool calls left behind by
    # provider safety termination (doubao/360 gateway content_filter), and backfill
    # blank safety-terminated responses so they are not persisted empty.
    #
    # Registered *after* LoopDetectionMiddleware on purpose: LangChain wires
    # after_model edges in reverse list order, so a later registration observes
    # the model output earlier. Safety therefore clears unsafe tool calls first
    # and LoopDetection accounts against the already-cleaned message, rather than
    # counting tool calls that are about to be discarded.
    middlewares.append(SafetyFinishReasonMiddleware())

    # ModelLengthFinishReasonMiddleware — record provider output-length caps as a
    # run stop reason. Observation-only: it never rewrites content and returns
    # None, so its position relative to Safety carries no behavioural coupling.
    middlewares.append(ModelLengthFinishReasonMiddleware())

    # TokenBudgetMiddleware (2026-08-13 D7, T18): per-run token budget guard.
    # Ships enabled:false — thresholds calibrated against the local DB's real
    # run distribution (see TokenBudgetConfig docstring for positioning).
    token_budget_config = resolved_app_config.token_budget
    if token_budget_config.enabled:
        from deerflow.agents.middlewares.token_budget_middleware import TokenBudgetMiddleware

        middlewares.append(TokenBudgetMiddleware.from_config(token_budget_config))

    # TerminalResponseMiddleware (C-08, 2026-08-13; 2026-09-23 批 1 A1-A4 扩容):
    # an empty terminal response in a real-user-rooted run gets a budgeted
    # (config) retry with exponential cooldown and inference-window trimming,
    # then a visible Chinese error fallback instead of a silent "success".
    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

    middlewares.append(TerminalResponseMiddleware.from_app_config(resolved_app_config))

    # ToolCallProtocolMiddleware (2026-10-14 加固批 D)：正文伪工具调用守卫——
    # 只识别完整、JSON 可解析、无原生工具调用的 <tool_call>...</tool_call> 正文；
    # 首次移除伪消息并 jump_to="model" 注入恢复提示（每 run 一次），第二次抛
    # ToolProtocolError → run 落 status=error + stop_reason=tool_protocol_error；
    # 任何情况下不解析执行正文中的命令。
    #
    # 注册在 TerminalResponse **之后**是有意的：after_model 按注册反序执行，
    # 后注册者先看到模型输出——伪消息必须先被本守卫移除/打断，才轮得到
    # TerminalResponse 做"空终态"判定（伪正文非空，若先过 TerminalResponse
    # 会被当作合法最终答案放行，重演 0c8fd42c 事故的 success 假象）。
    # ClarificationMiddleware 仍保持最后。
    from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolCallProtocolMiddleware

    middlewares.append(ToolCallProtocolMiddleware())

    # SlowLLMCallMiddleware — observation only: times each model call and, past
    # slow_llm_warn_seconds, logs a warning, emits an `llm_slow` stream event and
    # increments slow_llm_call_count in runtime.context (the run worker persists
    # it as run completion data). Wrap-only (no before/after_model), so it adds
    # no graph nodes. Wrap handlers nest earlier-is-outer, so this late position
    # times the provider call — including the openai SDK's silent internal
    # retries — without the backoff sleeps LLMErrorHandlingMiddleware (outer, via
    # build_lead_runtime_middlewares) adds between attempts. It stays ahead of
    # the custom injection so the documented "custom before Clarification" tail
    # order is unchanged.
    middlewares.append(SlowLLMCallMiddleware(warn_seconds=resolved_app_config.slow_llm_warn_seconds))

    # Inject custom middlewares before ClarificationMiddleware
    if custom_middlewares:
        middlewares.extend(custom_middlewares)

    # ClarificationMiddleware should always be last
    middlewares.append(ClarificationMiddleware())
    return middlewares


def make_lead_agent(config: RunnableConfig, app_config: AppConfig | None = None):
    # Lazy import to avoid circular dependency
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins import setup_agent

    cfg = _get_runtime_config(config)
    resolved_app_config = app_config or get_app_config()

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
    is_bootstrap = cfg.get("is_bootstrap", False)
    agent_name = validate_agent_name(cfg.get("agent_name"))

    agent_config = load_agent_config(agent_name) if not is_bootstrap else None

    # SEC-C3/M14 runtime hard gate (2026-09-04): a CUSTOM agent reaching us
    # with null tool_groups/skills must not degrade to "all tool groups" /
    # "all enabled skills" — that would expose bash/file groups and other
    # users' custom skills from the shared skills directory. Converge to the
    # write-side POST defaults (["web"] / []). None=全组 remains reserved for
    # the default agent (agent_config is None), bootstrap, DeerFlowClient and
    # task_tool inheritance — tools.py semantics are deliberately untouched.
    runtime_tool_groups: list[str] | None = agent_config.tool_groups if agent_config else None
    runtime_skills: set[str] | None = None
    if agent_config is not None:
        if runtime_tool_groups is None:
            logger.warning(
                "Agent '%s' reached runtime with null tool_groups; converging to ['web'] (SEC-C3)",
                agent_name,
            )
            runtime_tool_groups = ["web"]
        if agent_config.skills is None:
            logger.warning(
                "Agent '%s' reached runtime with null skills; converging to [] (M14)",
                agent_name,
            )
            runtime_skills = set()
        else:
            runtime_skills = set(agent_config.skills)
    # Custom agent model from agent config (if any), or None to let _resolve_model_name pick the default
    agent_model_name = agent_config.model if agent_config and agent_config.model else None

    # Determine memory write target: orchestrator agents (memory_scope: global) write to global user memory,
    # specialist agents write to their own per-agent memory. memory_scope: none
    # opts out of memory entirely (D-5 — no extraction middleware, no injection).
    memory_scope = agent_config.memory_scope if agent_config else None
    memory_write_agent_name = agent_name  # default: write to per-agent
    if agent_config and agent_config.memory_scope == "global":
        memory_write_agent_name = None  # write to global user memory

    # Final model name resolution: request → agent config → global default, with fallback for unknown names
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=resolved_app_config)

    model_config = resolved_app_config.get_model_config(model_name)

    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    # Inject run metadata for LangSmith trace tagging
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "langfuse_user_id": get_effective_user_id(),
            "langfuse_session_id": config.get("configurable", {}).get("thread_id"),
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
            "tool_groups": runtime_tool_groups,
            "available_skills": ["bootstrap"] if is_bootstrap else (sorted(runtime_skills) if runtime_skills is not None else None),
        }
    )

    # Inject tracing callbacks at the graph invocation root so a single LangGraph
    # run produces one trace with all node / LLM / tool calls as child spans,
    # AND so the Langfuse handler propagates session_id / user_id from config["metadata"].
    tracing_callbacks = build_tracing_callbacks()
    if tracing_callbacks:
        existing = config.get("callbacks") or []
        if not isinstance(existing, list):
            existing = list(existing)
        config["callbacks"] = [*existing, *tracing_callbacks]

    if is_bootstrap:
        # Special bootstrap agent with minimal prompt for initial custom agent creation flow
        return create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, app_config=resolved_app_config, attach_tracing=False),
            tools=get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled, app_config=resolved_app_config) + [setup_agent],
            middleware=_build_middlewares(config, model_name=model_name, app_config=resolved_app_config),
            context_schema=dict[str, Any],  # R6(2026-09-24 W6): 开放 context 标注——中间件往 runtime.context 塞计数器/journal，无 schema 时序列化告警
            system_prompt=apply_prompt_template(
                subagent_enabled=subagent_enabled,
                max_concurrent_subagents=max_concurrent_subagents,
                available_skills=set(["bootstrap"]),
                app_config=resolved_app_config,
            ),
            state_schema=ThreadState,
        )

    # Default lead agent (unchanged behavior)
    return create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort, app_config=resolved_app_config, attach_tracing=False),
        tools=get_available_tools(
            model_name=model_name,
            groups=runtime_tool_groups,
            subagent_enabled=subagent_enabled,
            app_config=resolved_app_config,
        ),
        middleware=_build_middlewares(config, model_name=model_name, agent_name=agent_name, memory_write_agent_name=memory_write_agent_name, memory_scope=memory_scope, app_config=resolved_app_config),
        context_schema=dict[str, Any],  # R6(2026-09-24 W6): 开放 context 标注——中间件往 runtime.context 塞计数器/journal，无 schema 时序列化告警
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled,
            max_concurrent_subagents=max_concurrent_subagents,
            agent_name=agent_name,
            available_skills=runtime_skills,
            app_config=resolved_app_config,
        ),
        state_schema=ThreadState,
    )

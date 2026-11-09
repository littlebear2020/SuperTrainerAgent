"""Background agent execution.

Runs an agent graph inside an ``asyncio.Task``, publishing events to
a :class:`StreamBridge` as they are produced.

Uses ``graph.astream(stream_mode=[...])`` which gives correct full-state
snapshots for ``values`` mode, proper ``{node: writes}`` for ``updates``,
and ``(chunk, metadata)`` tuples for ``messages`` mode.

Note: ``events`` mode is not supported through the gateway — it requires
``graph.astream_events()`` which cannot simultaneously produce ``values``
snapshots.  The JS open-source LangGraph API server works around this via
internal checkpoint callbacks that are not exposed in the Python public API.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from langchain_core.messages import HumanMessage

from langgraph.errors import GraphRecursionError

from deerflow.config.app_config import AppConfig
from deerflow.config.paths import get_paths
from deerflow.runtime.serialization import serialize
from deerflow.runtime.stream_bridge import StreamBridge
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.tracing import inject_langfuse_metadata

from .manager import RunManager, RunRecord
from .naming import resolve_root_run_name
from .schemas import RunStatus

logger = logging.getLogger(__name__)

# Valid stream_mode values for LangGraph's graph.astream()
_VALID_LG_MODES = {"values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"}


@dataclass(frozen=True)
class RunContext:
    """Infrastructure dependencies for a single agent run.

    Groups checkpointer, store, and persistence-related singletons so that
    ``run_agent`` (and any future callers) receive one object instead of a
    growing list of keyword arguments.
    """

    checkpointer: Any
    store: Any | None = field(default=None)
    event_store: Any | None = field(default=None)
    run_events_config: Any | None = field(default=None)
    thread_store: Any | None = field(default=None)
    app_config: AppConfig | None = field(default=None)


def _compute_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return "app_config" in inspect.signature(agent_factory).parameters
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=128)
def _cached_agent_factory_supports_app_config(agent_factory: Any) -> bool:
    return _compute_agent_factory_supports_app_config(agent_factory)


def _agent_factory_supports_app_config(agent_factory: Any) -> bool:
    try:
        return _cached_agent_factory_supports_app_config(agent_factory)
    except TypeError:
        # Some callable instances are unhashable; fall back to a direct check.
        return _compute_agent_factory_supports_app_config(agent_factory)


async def run_agent(
    bridge: StreamBridge,
    run_manager: RunManager,
    record: RunRecord,
    *,
    ctx: RunContext,
    agent_factory: Any,
    graph_input: dict,
    config: dict,
    stream_modes: list[str] | None = None,
    stream_subgraphs: bool = False,
    interrupt_before: list[str] | Literal["*"] | None = None,
    interrupt_after: list[str] | Literal["*"] | None = None,
) -> None:
    """Execute an agent in the background, publishing events to *bridge*."""

    # Unpack infrastructure dependencies from RunContext.
    # NOTE: unpack ``app_config`` to a local once — the finally block rebinds
    # the name ``ctx`` to the runtime context *dict* (journal branch below),
    # and any later ``ctx.app_config`` read would raise AttributeError and
    # escape the finally, killing publish_end + both cleanup tasks
    # (2026-09-23 review #1, regression: test_run_worker_journal_completion.py).
    checkpointer = ctx.checkpointer
    store = ctx.store
    event_store = ctx.event_store
    run_events_config = ctx.run_events_config
    thread_store = ctx.thread_store
    app_config = ctx.app_config

    run_id = record.run_id
    thread_id = record.thread_id
    requested_modes: set[str] = set(stream_modes or ["values"])
    pre_run_checkpoint_id: str | None = None
    pre_run_snapshot: dict[str, Any] | None = None
    snapshot_capture_failed = False

    journal = None
    # Bound before the try block so the finally clause can read it even when
    # the run dies before the runtime is built.
    runtime = None

    # Track whether "events" was requested but skipped
    if "events" in requested_modes:
        logger.info(
            "Run %s: 'events' stream_mode not supported in gateway (requires astream_events + checkpoint callbacks). Skipping.",
            run_id,
        )

    try:
        # Initialize RunJournal + write human_message event.
        # These are inside the try block so any exception (e.g. a DB
        # error writing the event) flows through the except/finally
        # path that publishes an "end" event to the SSE bridge —
        # otherwise a failure here would leave the stream hanging
        # with no terminator.
        if event_store is not None:
            from deerflow.runtime.journal import RunJournal

            journal = RunJournal(
                run_id=run_id,
                thread_id=thread_id,
                event_store=event_store,
                track_token_usage=getattr(run_events_config, "track_token_usage", True),
            )

        # 1. Mark running
        await run_manager.set_status(run_id, RunStatus.running)

        # 0.5 Disk admission (DEF-04 C2, 2026-09-23 批 1): one microsecond-scale
        # shutil.disk_usage probe against the data dir. Enforced only in
        # containerized (docker/aio) sandbox mode; local-sandbox mode keeps the
        # warn-ratio WARNING only (Q5). DiskAdmissionBlocked propagates to the
        # generic except-Exception branch below, which stamps the Chinese
        # message onto the run row and the SSE error event.
        if app_config is not None:
            from deerflow.sandbox.disk_admission import check_disk_admission, sandbox_mode_is_containerized

            containerized = sandbox_mode_is_containerized(getattr(getattr(app_config, "sandbox", None), "use", None))
            check_disk_admission(
                get_paths().base_dir,
                block_bytes=app_config.disk_min_free_bytes_block if containerized else 0,
                warn_ratio=app_config.disk_warn_free_ratio,
                label="任务",
            )

        # Snapshot the latest pre-run checkpoint so rollback can restore it.
        if checkpointer is not None:
            try:
                config_for_check = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(config_for_check)
                if ckpt_tuple is not None:
                    ckpt_config = getattr(ckpt_tuple, "config", {}).get("configurable", {})
                    pre_run_checkpoint_id = ckpt_config.get("checkpoint_id")
                    pre_run_snapshot = {
                        "checkpoint_ns": ckpt_config.get("checkpoint_ns", ""),
                        "checkpoint": copy.deepcopy(getattr(ckpt_tuple, "checkpoint", {})),
                        "metadata": copy.deepcopy(getattr(ckpt_tuple, "metadata", {})),
                        "pending_writes": copy.deepcopy(getattr(ckpt_tuple, "pending_writes", []) or []),
                    }
            except Exception:
                snapshot_capture_failed = True
                logger.warning("Could not capture pre-run checkpoint snapshot for run %s", run_id, exc_info=True)

        # 2. Publish metadata — useStream needs both run_id AND thread_id
        await bridge.publish(
            run_id,
            "metadata",
            {
                "run_id": run_id,
                "thread_id": thread_id,
            },
        )

        # 3. Build the agent
        from langchain_core.runnables import RunnableConfig
        from langgraph.runtime import Runtime

        # Inject runtime context so middlewares can access thread_id
        # (langgraph-cli does this automatically; we must do it manually)
        runtime = Runtime(context={"thread_id": thread_id, "run_id": run_id}, store=store)
        # Expose the run-scoped journal so task_tool can transfer subagent
        # token records to the parent RunJournal.
        if journal is not None:
            runtime.context["__run_journal"] = journal
        # If the caller already set a ``context`` key (LangGraph >= 0.6.0
        # prefers it over ``configurable`` for thread-level data), make
        # sure ``thread_id`` is available there too.
        if "context" in config and isinstance(config["context"], dict):
            config["context"].setdefault("thread_id", thread_id)
            config["context"].setdefault("run_id", run_id)
        config.setdefault("configurable", {})["__pregel_runtime"] = runtime

        # Inject RunJournal as a LangChain callback handler.
        # on_llm_end captures token usage; on_chain_start/end captures lifecycle.
        if journal is not None:
            config.setdefault("callbacks", []).append(journal)

        # Inject Langfuse trace-attribute metadata so the langchain CallbackHandler
        # can lift session_id / user_id / trace_name / tags onto the root trace.
        inject_langfuse_metadata(
            config,
            thread_id=thread_id,
            user_id=get_effective_user_id(),
            assistant_id=record.assistant_id,
            model_name=getattr(record, "model_name", None),
            environment=os.environ.get("DEER_FLOW_ENV") or os.environ.get("ENVIRONMENT"),
        )

        # Resolve after runtime context installation so context/configurable reflect
        # the agent name that this run will actually execute.
        config.setdefault("run_name", resolve_root_run_name(config, record.assistant_id))
        runnable_config = RunnableConfig(**config)
        if app_config is not None and _agent_factory_supports_app_config(agent_factory):
            agent = agent_factory(config=runnable_config, app_config=app_config)
        else:
            agent = agent_factory(config=runnable_config)

        # 4. Attach checkpointer and store
        if checkpointer is not None:
            agent.checkpointer = checkpointer
        if store is not None:
            agent.store = store

        # 5. Set interrupt nodes
        if interrupt_before:
            agent.interrupt_before_nodes = interrupt_before
        if interrupt_after:
            agent.interrupt_after_nodes = interrupt_after

        # 6. Build LangGraph stream_mode list
        #    "events" is NOT a valid astream mode — skip it
        #    "messages-tuple" maps to LangGraph's "messages" mode
        lg_modes: list[str] = []
        for m in requested_modes:
            if m == "messages-tuple":
                lg_modes.append("messages")
            elif m == "events":
                # Skipped — see log above
                continue
            elif m in _VALID_LG_MODES:
                lg_modes.append(m)
        if not lg_modes:
            lg_modes = ["values"]

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for m in lg_modes:
            if m not in seen:
                seen.add(m)
                deduped.append(m)
        lg_modes = deduped

        logger.info("Run %s: streaming with modes %s (requested: %s)", run_id, lg_modes, requested_modes)

        # P2-1: periodically persist the journal's in-flight token snapshot so the
        # operations console shows live cost mid-run and a crash before completion
        # doesn't lose the usage. Cadence + the token-tracking gate live in the
        # journal (progress_flush_due); the store only writes while status is still
        # 'running', so this never resurrects a finished row.
        async def _flush_inflight_progress() -> None:
            if journal is None or not journal.progress_flush_due():
                return
            try:
                await run_manager.update_run_progress(run_id, **journal.get_progress_data())
            except Exception:
                logger.warning("Failed to flush in-flight progress for run %s (non-fatal)", run_id, exc_info=True)
            journal.mark_progress_flushed()

        # 7. Stream using graph.astream
        if len(lg_modes) == 1 and not stream_subgraphs:
            # Single mode, no subgraphs: astream yields raw chunks
            single_mode = lg_modes[0]
            async for chunk in agent.astream(graph_input, config=runnable_config, stream_mode=single_mode):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break
                sse_event = _lg_mode_to_sse_event(single_mode)
                await bridge.publish(run_id, sse_event, serialize(chunk, mode=single_mode))
                await _flush_inflight_progress()
        else:
            # Multiple modes or subgraphs: astream yields tuples
            async for item in agent.astream(
                graph_input,
                config=runnable_config,
                stream_mode=lg_modes,
                subgraphs=stream_subgraphs,
            ):
                if record.abort_event.is_set():
                    logger.info("Run %s abort requested — stopping", run_id)
                    break

                mode, chunk = _unpack_stream_item(item, lg_modes, stream_subgraphs)
                if mode is None:
                    continue

                sse_event = _lg_mode_to_sse_event(mode)
                await bridge.publish(run_id, sse_event, serialize(chunk, mode=mode))
                await _flush_inflight_progress()

        # 8. Final status
        if record.abort_event.is_set():
            action = record.abort_action
            if action == "rollback":
                await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
                try:
                    await _rollback_to_pre_run_checkpoint(
                        checkpointer=checkpointer,
                        thread_id=thread_id,
                        run_id=run_id,
                        pre_run_checkpoint_id=pre_run_checkpoint_id,
                        pre_run_snapshot=pre_run_snapshot,
                        snapshot_capture_failed=snapshot_capture_failed,
                    )
                    logger.info("Run %s rolled back to pre-run checkpoint %s", run_id, pre_run_checkpoint_id)
                except Exception:
                    logger.warning("Failed to rollback checkpoint for run %s", run_id, exc_info=True)
            else:
                await run_manager.set_status(run_id, RunStatus.interrupted)
        else:
            await run_manager.set_status(run_id, RunStatus.success)

    except asyncio.CancelledError:
        action = record.abort_action
        if action == "rollback":
            await run_manager.set_status(run_id, RunStatus.error, error="Rolled back by user")
            try:
                await _rollback_to_pre_run_checkpoint(
                    checkpointer=checkpointer,
                    thread_id=thread_id,
                    run_id=run_id,
                    pre_run_checkpoint_id=pre_run_checkpoint_id,
                    pre_run_snapshot=pre_run_snapshot,
                    snapshot_capture_failed=snapshot_capture_failed,
                )
                logger.info("Run %s was cancelled and rolled back", run_id)
            except Exception:
                logger.warning("Run %s cancellation rollback failed", run_id, exc_info=True)
        else:
            await run_manager.set_status(run_id, RunStatus.interrupted)
            logger.info("Run %s was cancelled", run_id)

    except GraphRecursionError as exc:
        # The graph burned through its super-step budget without hitting a stop
        # condition. The thread checkpoint is intact, so the user can resume
        # from the break point by simply replying — say so instead of dumping
        # the raw langgraph exception text. The raw error stays appended after
        # a newline for diagnosability.
        recursion_limit = config.get("recursion_limit")
        llm_call_count = journal.get_progress_data().get("llm_call_count") if journal is not None else None
        # F6（2026-09-29）：except 路径向 runtime.context 补 stop_reason——finally
        # 块现有合并逻辑（update_run_completion 前读 run_ctx["stop_reason"]）不动，
        # 打点后自然落库。setdefault 语义：中间件更早打的原因不覆盖。
        _run_ctx = getattr(runtime, "context", None) if runtime is not None else None
        if isinstance(_run_ctx, dict):
            _run_ctx.setdefault("stop_reason", "recursion_limit")
        logger.warning(
            "Run %s hit GraphRecursionError (recursion_limit=%s, llm_call_count=%s): %s",
            run_id,
            recursion_limit,
            llm_call_count,
            exc,
        )
        error_msg = f"步数预算（recursion_limit）已耗尽。对话现场已完整保留，直接回复「继续」即可从断点续作。\n{exc}"
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": "GraphRecursionError",
            },
        )

    except Exception as exc:
        error_msg = f"{exc}"
        logger.exception("Run %s failed: %s", run_id, error_msg)
        # F6（2026-09-29）：熔断/磁盘准入等 GraphBubbleUp 也走本分支——统一补
        # run_error（与中间件打点同一落库通道；setdefault 不覆盖更早原因）。
        _run_ctx = getattr(runtime, "context", None) if runtime is not None else None
        if isinstance(_run_ctx, dict):
            _run_ctx.setdefault("stop_reason", "run_error")
        await run_manager.set_status(run_id, RunStatus.error, error=error_msg)
        await bridge.publish(
            run_id,
            "error",
            {
                "message": error_msg,
                "name": type(exc).__name__,
            },
        )

    finally:
        # Flush any buffered journal events and persist completion data
        if journal is not None:
            try:
                await journal.flush()
            except Exception:
                logger.warning("Failed to flush journal for run %s", run_id, exc_info=True)

            try:
                # Persist token usage + convenience fields to RunStore
                completion = journal.get_completion_data()
                # The safety / model-length middlewares stamp why the run ended
                # into runtime.context; carry it to the row so the operations
                # console can tell a capped completion from a clean one.
                # (Named ``run_ctx`` — shadowing the ``RunContext`` parameter
                # ``ctx`` here used to break the D3 block below, which reads
                # ``app_config`` outside this branch; see review #1.)
                run_ctx = getattr(runtime, "context", None) if runtime is not None else None
                stop_reason = run_ctx.get("stop_reason") if isinstance(run_ctx, dict) else None
                if stop_reason:
                    completion["stop_reason"] = str(stop_reason)[:32]
                # SlowLLMCallMiddleware counts over-threshold LLM calls in
                # runtime.context; carry it to the row so the operations
                # console can tell a provider-stalled run from a fast one.
                slow_llm_call_count = run_ctx.get("slow_llm_call_count") if isinstance(run_ctx, dict) else None
                if slow_llm_call_count:
                    completion["slow_llm_call_count"] = int(slow_llm_call_count)
                # TerminalResponseMiddleware counts exhausted empty-response
                # fallbacks the same way (2026-09-23 plan A4): one column for
                # the OBS-02 weekly SQL report + retry-recovery-rate KPI.
                empty_fallback_count = run_ctx.get("empty_fallback_count") if isinstance(run_ctx, dict) else None
                if empty_fallback_count:
                    completion["empty_fallback_count"] = int(empty_fallback_count)
                await run_manager.update_run_completion(run_id, status=record.status.value, **completion)

                # Stamp the model that actually served the run. Nothing wrote
                # this column before (update_model_name had no callers), so
                # every pre-existing row has model_name NULL and the console
                # showed a blank model for all of them.
                if record.model_name is None:
                    primary_model = journal.primary_model_name()
                    if primary_model:
                        await run_manager.update_model_name(run_id, primary_model)
            except Exception:
                logger.warning("Failed to persist run completion for %s (non-fatal)", run_id, exc_info=True)

        # D3（OBS-03，2026-09-23 批 2）：thread 累计 tokens 告警——与 UI 徽章同
        # 口径（线程内全部 run 之和），阈值按任务类型（agent_name）解析
        # config.token_budget_warn_tokens。只告警不阻断：WARNING 日志 +
        # token_budget_warn 流事件；任务照常完成。
        if app_config is not None:
            try:
                from deerflow.runtime.runs.budget_warn import resolve_warn_threshold, should_warn_token_budget, thread_total_tokens

                task_type = None
                for container_name in ("context", "configurable"):
                    container = config.get(container_name)
                    if isinstance(container, dict) and isinstance(container.get("agent_name"), str):
                        task_type = container["agent_name"]
                        break
                thresholds = app_config.token_budget_warn_tokens
                if resolve_warn_threshold(task_type, thresholds) is not None:
                    aggregate = await run_manager.aggregate_thread_tokens(thread_id, include_active=False)
                    total = thread_total_tokens(aggregate)
                    if should_warn_token_budget(total, task_type, thresholds):
                        threshold = resolve_warn_threshold(task_type, thresholds)
                        logger.warning(
                            "Token budget warning for thread %s (task=%s): %d cumulative tokens exceed the %d threshold (advisory only)",
                            thread_id,
                            task_type,
                            total,
                            threshold,
                        )
                        # R10（Q6，2026-09-24 批 2）：走 custom 事件通道（与中间件
                        # stream-writer 的 custom 同一 SSE 事件名，SDK onCustomEvent
                        # 路由到前端 toast——llm_retry 同款通路）。修前用独立事件名
                        # token_budget_warn 直发，前端 onCustomEvent 永远收不到。
                        # 每 run 完成时最多发一次（本块每次 run 只走一遍）。
                        await bridge.publish(
                            run_id,
                            "custom",
                            {
                                "type": "token_budget_warn",
                                "message": "本会话累计 token 已超过该任务类型的告警线（仅提示，任务继续）。",
                                "thread_id": thread_id,
                                "task_type": task_type or "default",
                                "total_tokens": total,
                                "threshold": threshold,
                            },
                        )
            except Exception:
                logger.warning("Token budget warning failed for run %s (non-fatal)", run_id, exc_info=True)

        # Sync title from checkpoint to threads_meta.display_name
        if checkpointer is not None and thread_store is not None:
            try:
                ckpt_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ckpt_tuple = await checkpointer.aget_tuple(ckpt_config)
                if ckpt_tuple is not None:
                    ckpt = getattr(ckpt_tuple, "checkpoint", {}) or {}
                    title = ckpt.get("channel_values", {}).get("title")
                    if title:
                        await thread_store.update_display_name(thread_id, title)
            except Exception:
                logger.debug("Failed to sync title for thread %s (non-fatal)", thread_id)

        # Update threads_meta status based on run outcome
        if thread_store is not None:
            try:
                final_status = "idle" if record.status == RunStatus.success else record.status.value
                await thread_store.update_status(thread_id, final_status)
            except Exception:
                logger.debug("Failed to update thread_meta status for %s (non-fatal)", thread_id)

        await bridge.publish_end(run_id)
        asyncio.create_task(bridge.cleanup(run_id, delay=60))
        # Evict the in-memory RunRecord after a grace window so completed
        # runs don't accumulate in _runs forever (gateway OOM root cause #2).
        # 60s aligns with bridge.cleanup and covers the stream-reconnect
        # window; post-stream endpoints (/runs/{rid}/messages, token-usage,
        # feedback) read from event_store / run_store / feedback_repo, NOT
        # RunManager._runs, so evicting is safe. RunManager.get() also falls
        # back to the SQL run_store after eviction, so /runs/{rid} still works.
        asyncio.create_task(run_manager.cleanup(run_id, delay=60))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _call_checkpointer_method(checkpointer: Any, async_name: str, sync_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call a checkpointer method, supporting async and sync variants."""
    method = getattr(checkpointer, async_name, None) or getattr(checkpointer, sync_name, None)
    if method is None:
        raise AttributeError(f"Missing checkpointer method: {async_name}/{sync_name}")
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def _rollback_to_pre_run_checkpoint(
    *,
    checkpointer: Any,
    thread_id: str,
    run_id: str,
    pre_run_checkpoint_id: str | None,
    pre_run_snapshot: dict[str, Any] | None,
    snapshot_capture_failed: bool,
) -> None:
    """Restore thread state to the checkpoint snapshot captured before run start."""
    if checkpointer is None:
        logger.info("Run %s rollback requested but no checkpointer is configured", run_id)
        return

    if snapshot_capture_failed:
        logger.warning("Run %s rollback skipped: pre-run checkpoint snapshot capture failed", run_id)
        return

    if pre_run_snapshot is None:
        await _call_checkpointer_method(checkpointer, "adelete_thread", "delete_thread", thread_id)
        logger.info("Run %s rollback reset thread %s to empty state", run_id, thread_id)
        return

    checkpoint_to_restore = None
    metadata_to_restore: dict[str, Any] = {}
    checkpoint_ns = ""
    checkpoint = pre_run_snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        logger.warning("Run %s rollback skipped: invalid pre-run checkpoint snapshot", run_id)
        return
    checkpoint_to_restore = checkpoint
    if checkpoint_to_restore.get("id") is None and pre_run_checkpoint_id is not None:
        checkpoint_to_restore = {**checkpoint_to_restore, "id": pre_run_checkpoint_id}
    if checkpoint_to_restore.get("id") is None:
        logger.warning("Run %s rollback skipped: pre-run checkpoint has no checkpoint id", run_id)
        return
    metadata = pre_run_snapshot.get("metadata", {})
    metadata_to_restore = metadata if isinstance(metadata, dict) else {}
    raw_checkpoint_ns = pre_run_snapshot.get("checkpoint_ns")
    checkpoint_ns = raw_checkpoint_ns if isinstance(raw_checkpoint_ns, str) else ""

    channel_versions = checkpoint_to_restore.get("channel_versions")
    new_versions = dict(channel_versions) if isinstance(channel_versions, dict) else {}

    restore_config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}}
    restored_config = await _call_checkpointer_method(
        checkpointer,
        "aput",
        "put",
        restore_config,
        checkpoint_to_restore,
        metadata_to_restore if isinstance(metadata_to_restore, dict) else {},
        new_versions,
    )
    if not isinstance(restored_config, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config: expected dict")
    restored_configurable = restored_config.get("configurable", {})
    if not isinstance(restored_configurable, dict):
        raise RuntimeError(f"Run {run_id} rollback restore returned invalid config payload")
    restored_checkpoint_id = restored_configurable.get("checkpoint_id")
    if not restored_checkpoint_id:
        raise RuntimeError(f"Run {run_id} rollback restore did not return checkpoint_id")

    pending_writes = pre_run_snapshot.get("pending_writes", [])
    if not pending_writes:
        return

    writes_by_task: dict[str, list[tuple[str, Any]]] = {}
    for item in pending_writes:
        if not isinstance(item, (tuple, list)) or len(item) != 3:
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write is not a 3-tuple: {item!r}")
        task_id, channel, value = item
        if not isinstance(channel, str):
            raise RuntimeError(f"Run {run_id} rollback failed: pending_write has non-string channel: task_id={task_id!r}, channel={channel!r}")
        writes_by_task.setdefault(str(task_id), []).append((channel, value))

    for task_id, writes in writes_by_task.items():
        await _call_checkpointer_method(
            checkpointer,
            "aput_writes",
            "put_writes",
            restored_config,
            writes,
            task_id=task_id,
        )


def _lg_mode_to_sse_event(mode: str) -> str:
    """Map LangGraph internal stream_mode name to SSE event name.

    LangGraph's ``astream(stream_mode="messages")`` produces message
    tuples.  The SSE protocol calls this ``messages-tuple`` when the
    client explicitly requests it, but the default SSE event name used
    by LangGraph Platform is simply ``"messages"``.
    """
    # All LG modes map 1:1 to SSE event names — "messages" stays "messages"
    return mode


def _extract_human_message(graph_input: dict) -> HumanMessage | None:
    """Extract or construct a HumanMessage from graph_input for event recording.

    Returns a LangChain HumanMessage so callers can use .model_dump() to get
    the checkpoint-aligned serialization format.
    """
    from langchain_core.messages import HumanMessage

    messages = graph_input.get("messages")
    if not messages:
        return None
    last = messages[-1] if isinstance(messages, list) else messages
    if isinstance(last, HumanMessage):
        return last
    if isinstance(last, str):
        return HumanMessage(content=last) if last else None
    if hasattr(last, "content"):
        content = last.content
        return HumanMessage(content=content)
    if isinstance(last, dict):
        content = last.get("content", "")
        return HumanMessage(content=content) if content else None
    return None


def _unpack_stream_item(
    item: Any,
    lg_modes: list[str],
    stream_subgraphs: bool,
) -> tuple[str | None, Any]:
    """Unpack a multi-mode or subgraph stream item into (mode, chunk).

    Returns ``(None, None)`` if the item cannot be parsed.
    """
    if stream_subgraphs:
        if isinstance(item, tuple) and len(item) == 3:
            _ns, mode, chunk = item
            return str(mode), chunk
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
            return str(mode), chunk
        return None, None

    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
        return str(mode), chunk

    # Fallback: single-element output from first mode
    return lg_modes[0] if lg_modes else None, item

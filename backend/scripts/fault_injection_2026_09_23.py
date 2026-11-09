"""Mock-level fault injection for the 2026-09-23 batch-1 stability plan (A/C).

Three scenarios, zero network, zero config.yaml dependency — they drive the
real middleware/provider code with mocked failure sources:

  1. empty-response sequence  — a mocked 360-style channel that returns empty
     terminal responses; observes retry budget, exponential cooldown rhythm,
     inference-window trimming, llm_empty_retry / llm_empty_fallback stream
     events, and runtime.context empty_fallback_count (the run-row counter).
     Two sub-runs: (a) recovers on the 3rd retry, (b) stays empty to fallback.
  2. SANDBOX_DEAD consecutive hits — source-tagged dead-fingerprint tool
     results; observes the circuit breaker tripping at threshold 5 with the
     Chinese run-error message, plus a mixed sequence that resets and does
     not trip.
  3. disk interception — monkeypatched shutil.disk_usage; observes the
     block-tier Chinese rejection, the warn-tier WARNING-only behavior, and
     the containerized/local mode gate for the run-start checkpoint.

Run from backend/:  PYTHONPATH=. uv run python scripts/fault_injection_2026_09_23.py
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from types import SimpleNamespace

import langgraph.config as lg_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _install_stream_collector():
    events: list[dict] = []

    def writer(payload):
        events.append(payload)

    lg_config.get_stream_writer = lambda: writer
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: empty-response sequence (plan A1-A4)
# ─────────────────────────────────────────────────────────────────────────────


async def scenario_empty_response(recover_on_attempt: int | None, label: str) -> None:
    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware

    events = _install_stream_collector()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    mw = TerminalResponseMiddleware(
        max_retries=3,
        cooldown_base_seconds=2.0,
        cooldown_max_seconds=8.0,
        trim_on_retry=True,
    )
    mw._sleep = fake_sleep

    runtime = SimpleNamespace(context={"thread_id": "fi-thread", "run_id": f"fi-run-{label}"})
    await mw.abefore_agent({"messages": []}, runtime)

    # Thread state: real user message + one finished tool round (post-tool shape).
    messages: list = [
        HumanMessage(content="帮我把这份报告做成 PPT"),
        AIMessage(content="", tool_calls=[{"name": "bash", "id": "c0", "args": {}}]),
        ToolMessage(content="ok", tool_call_id="c0"),
    ]
    # Middle history bloat: 6 oversized tool rounds (the Input>=125K shape).
    for i in range(1, 7):
        messages.append(AIMessage(content="", tool_calls=[{"name": "bash", "id": f"c{i}", "args": {}}]))
        messages.append(ToolMessage(content="x" * 5000, tool_call_id=f"c{i}"))

    attempt = 0
    outcome = "unexpected-exit"
    for _ in range(10):
        # A recovered turn ends with a visible trailing AIMessage — the guard
        # must pass it through untouched.
        if messages and isinstance(messages[-1], AIMessage) and str(messages[-1].content).strip():
            result = await mw.aafter_model({"messages": list(messages)}, runtime)
            outcome = "visible-response-passed-through" if result is None else f"unexpected:{result}"
            break
        state = {"messages": [*messages, AIMessage(content="", id=f"empty-{attempt}")]}
        result = await mw.aafter_model(state, runtime)
        if result is None:
            outcome = "visible-response-passed-through"
            break
        if result.get("jump_to") == "model":
            attempt += 1
            # The retry goes through wrap_model_call: recovery prompt (and, from
            # the 2nd retry, middle-trim) applied to the inference window only.
            trimmed_seen = []

            def override(messages=None, **kw):
                trimmed_seen.extend(m for m in (messages or []) if isinstance(m, ToolMessage) and "middle truncated" in str(m.content))
                return SimpleNamespace(messages=messages)

            request = SimpleNamespace(messages=list(messages), runtime=runtime, override=override)

            async def handler(req):
                if recover_on_attempt is not None and attempt >= recover_on_attempt:
                    return AIMessage(content="已按您的报告生成了 12 页 PPT，见输出产物。")
                return "empty-model-result"

            await mw.awrap_model_call(request, handler)
            if trimmed_seen:
                print(f"    [trim] {len(trimmed_seen)} mid-history tool outputs truncated in the retry window (state untouched)")
            if recover_on_attempt is not None and attempt >= recover_on_attempt:
                messages.append(AIMessage(content="已按您的报告生成了 12 页 PPT，见输出产物。"))
            else:
                messages.append(AIMessage(content="", id=f"empty-{attempt}"))
        else:
            outcome = "fallback-persisted"
            fallback = result["messages"][0]
            print(f"    [fallback] content={fallback.content!r}")
            print(f"    [fallback] marker={fallback.additional_kwargs.get('deerflow_error_fallback')}")
            break

    await mw.aafter_agent({"messages": messages}, runtime)
    print(f"  outcome={outcome} attempts={attempt}")
    print(f"  cooldown rhythm (s)={sleeps}")
    print(f"  stream events={[e['type'] for e in events]}")
    retry_events = [e for e in events if e["type"] == "llm_empty_retry"]
    if retry_events:
        print(f"  retry copy={[e['message'] for e in retry_events]}")
    print(f"  runtime.context empty_fallback_count={runtime.context.get('empty_fallback_count')}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: SANDBOX_DEAD consecutive hits (plan C1)
# ─────────────────────────────────────────────────────────────────────────────


def scenario_sandbox_dead() -> None:
    from deerflow.agents.middlewares.sandbox_circuit_breaker_middleware import (
        SANDBOX_DEAD_MARKER,
        SandboxCircuitBreakerError,
        SandboxCircuitBreakerMiddleware,
    )

    runtime = SimpleNamespace(context={"thread_id": "fi-thread", "run_id": "fi-run-dead"})
    mw = SandboxCircuitBreakerMiddleware(failure_threshold=5)
    mw.before_agent({}, runtime)

    def request(i: int) -> SimpleNamespace:
        return SimpleNamespace(tool_call={"name": "bash", "id": f"c{i}", "args": {}}, runtime=runtime)

    print("  -- consecutive death streak (probe loop shape) --")
    for i in range(1, 5):
        result = mw.wrap_tool_call(request(i), lambda r, i=i: ToolMessage(content=f"Error: {SANDBOX_DEAD_MARKER} HTTPConnectionPool: connection refused", tool_call_id=f"c{i}"))
        print(f"    hit {i}/5 -> counted (result passthrough: {type(result).__name__})")
    try:
        mw.wrap_tool_call(request(5), lambda r: ToolMessage(content=f"Error: {SANDBOX_DEAD_MARKER} connection refused", tool_call_id="c5"))
        raise AssertionError("breaker did not trip")
    except SandboxCircuitBreakerError as exc:
        print("    trip at 5/5: GraphBubbleUp -> run error path")
        print(f"    user-facing message: {exc}")

    print("  -- mixed sequence (healthy result resets; no trip) --")
    mw.before_agent({}, runtime)
    for i in range(1, 4):
        mw.wrap_tool_call(request(10 + i), lambda r, i=i: ToolMessage(content=f"Error: {SANDBOX_DEAD_MARKER} connection refused", tool_call_id=f"c{10 + i}"))
    mw.wrap_tool_call(request(14), lambda r: ToolMessage(content="src/main.py\nREADME.md", tool_call_id="c14"))
    for i in range(1, 3):
        mw.wrap_tool_call(request(20 + i), lambda r, i=i: ToolMessage(content=f"Error: {SANDBOX_DEAD_MARKER} connection refused", tool_call_id=f"c{20 + i}"))
    print(f"    3 dead + 1 healthy + 2 dead -> consecutive={mw._consecutive.get(mw._key(runtime))} (< 5, no trip)")

    print("  -- untagged natural-language errors never count --")
    for i in range(8):
        mw.wrap_tool_call(request(30 + i), lambda r, i=i: ToolMessage(content="Error: command 'gcc' failed with exit status 1", tool_call_id=f"c{30 + i}"))
    print(f"    8 untagged failures -> consecutive map={dict(mw._consecutive)} (zero tolerance honored)")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: disk interception (plan C2)
# ─────────────────────────────────────────────────────────────────────────────


def scenario_disk() -> None:
    import deerflow.sandbox.disk_admission as da

    real_usage = shutil.disk_usage

    class FakeUsage:
        def __init__(self, free: int, total: int) -> None:
            self.free, self.total = free, total

    print("  -- block tier: 0.4 GiB free vs 5 GiB gate (aio/container run start) --")
    shutil.disk_usage = lambda path: FakeUsage(free=int(0.4 * 1024**3), total=500 * 1024**3)
    try:
        da.check_disk_admission("/data", block_bytes=5 * 1024**3, warn_ratio=0.15, label="任务")
        raise AssertionError("admission did not block")
    except da.DiskAdmissionBlocked as exc:
        print(f"    blocked: {exc}")
    finally:
        shutil.disk_usage = real_usage

    print("  -- warn tier: 8% free, block gate off (local sandbox mode shape) --")
    shutil.disk_usage = lambda path: FakeUsage(free=int(0.08 * 500 * 1024**3), total=500 * 1024**3)
    try:
        da.check_disk_admission("/data", block_bytes=0, warn_ratio=0.15, label="任务")
        print("    warn-only: no exception (WARNING logged by the deerflow.sandbox.disk_admission logger)")
    finally:
        shutil.disk_usage = real_usage

    print("  -- mode gate --")
    print(f"    aio provider use -> containerized={da.sandbox_mode_is_containerized('deerflow.community.aio_sandbox.aio_sandbox_provider:AioSandboxProvider')}")
    print(f"    local provider use -> containerized={da.sandbox_mode_is_containerized('deerflow.sandbox.local:LocalSandboxProvider')} (run-start gate enforces 0-byte block, WARN only)")

    print("  -- lazy acquire blocked through the REAL bash_tool path (R1, 2026-09-24 批 0) --")
    # 修前：bash_tool 的 generic except Exception 把 DiskAdmissionBlocked 兜成
    # "Error: ..." 字符串 → 无标记、不熔断、run 不落 error，agent 无限重试
    # （DEF-04 死循环复刻）。修后：GraphBubbleUp 上抛 → worker except Exception
    # 分支（worker.py）落 RunStatus.error + 中文文案，checkpoint 保留最后成功
    # superstep，「继续」可断点续作。
    from types import SimpleNamespace as _NS

    from deerflow.sandbox import tools as sandbox_tools

    class _DeniedProvider:
        def acquire(self, thread_id):
            raise da.DiskAdmissionBlocked("服务器磁盘可用空间不足（0.4 GiB < 准入门槛 5.0 GiB），任务已拒绝启动：请清理磁盘空间后重试；已开始的任务不受影响。")

        def get(self, sandbox_id):
            raise AssertionError("unreachable")

    real_provider = sandbox_tools.get_sandbox_provider
    sandbox_tools.get_sandbox_provider = lambda: _DeniedProvider()
    try:
        runtime = _NS(state={}, context={"thread_id": "fi-thread-disk"}, config={})
        sandbox_tools.bash_tool.func(runtime, description="run command", command="echo hi")
        raise AssertionError("bash_tool did not raise DiskAdmissionBlocked")
    except da.DiskAdmissionBlocked as exc:
        print(f"    bash_tool raised DiskAdmissionBlocked (escapes the tool-error fallback): {exc}")
        print("    → run 落 error 终态（worker except Exception 分支）+ 中文文案 + checkpoint 保留（「继续」续作）")
    finally:
        sandbox_tools.get_sandbox_provider = real_provider


async def main() -> None:
    print("=" * 78)
    print("Scenario 1a: empty-response sequence, recovery on 3rd retry")
    print("=" * 78)
    await scenario_empty_response(recover_on_attempt=3, label="recover")
    print()
    print("=" * 78)
    print("Scenario 1b: empty-response sequence, never recovers (fallback)")
    print("=" * 78)
    await scenario_empty_response(recover_on_attempt=None, label="fallback")
    print()
    print("=" * 78)
    print("Scenario 2: SANDBOX_DEAD consecutive hits -> circuit breaker")
    print("=" * 78)
    scenario_sandbox_dead()
    print()
    print("=" * 78)
    print("Scenario 3: disk admission interception")
    print("=" * 78)
    scenario_disk()
    print()
    print("ALL SCENARIOS DONE")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="  [log] %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
    sys.exit(0)

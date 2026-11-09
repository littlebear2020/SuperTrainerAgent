"""Tests for LoopDetectionMiddleware.

2026-10-14 加固批：警告生命周期移植上游 #2752/#5344/#6804（函数级）——
警告只在下一次模型请求（wrap_model_call）临时注入、不进 checkpoint；
全部检测状态按 ``(thread_id, run_id)`` 隔离；warn 文案允许先保存阶段成果
和一次 read-before-write 重试。本仓私有行为（F4 失败重试帽、F6 stop_reason、
recursion 联动、polling 豁免、窗口化频率）保留原测试。
"""

import copy
import threading
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool as as_tool
from pydantic import PrivateAttr

from deerflow.agents.middlewares import loop_detection_middleware as loop_detection_module
from deerflow.agents.middlewares.loop_detection_middleware import (
    _HARD_STOP_MSG,
    LoopDetectionMiddleware,
    _hash_tool_calls,
)


def _make_runtime(thread_id="test-thread", run_id="test-run"):
    """Build a minimal Runtime mock with context.

    ``stream_writer`` gets an explicit per-instance object so the no-run_id
    invocation fallback (anchored on stream_writer identity) is stable for
    one runtime and distinct across runtimes — mirroring real graphs.
    """
    runtime = MagicMock()
    runtime.context = {"thread_id": thread_id}
    if run_id is not None:
        runtime.context["run_id"] = run_id
    runtime.stream_writer = object()
    runtime.execution_info = None
    return runtime


def _scope_key(thread_id="test-thread", run_id="test-run"):
    return (thread_id, run_id)


def _pending_key(thread_id="test-thread", run_id="test-run"):
    return (thread_id, run_id)


def _key_of(mw, runtime):
    """Scope key for a runtime whose run_id may be a fallback token."""
    return mw._run_scope_key(runtime)


def _make_request(messages, runtime):
    """Build a minimal ModelRequest stand-in for wrap_model_call tests."""
    request = MagicMock()
    request.messages = list(messages)
    request.runtime = runtime
    request.override = lambda **updates: _override_request(request, updates)
    return request


def _override_request(request, updates):
    """Mimic ModelRequest.override(): return a copy with fields replaced."""
    new = MagicMock()
    new.messages = updates.get("messages", request.messages)
    new.runtime = updates.get("runtime", request.runtime)
    new.override = lambda **u: _override_request(new, u)
    return new


def _capture_handler():
    """Build a sync handler that records the request it was called with."""
    captured: list = []

    def handler(req):
        captured.append(req)
        return MagicMock()

    return captured, handler


class _CapturingFakeMessagesListChatModel(FakeMessagesListChatModel):
    """Fake chat model that records each model request's messages."""

    _seen_messages: list[list[Any]] = PrivateAttr(default_factory=list)

    @property
    def seen_messages(self) -> list[list[Any]]:
        return self._seen_messages

    def bind_tools(
        self,
        tools: Any,
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Runnable:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._seen_messages.append(list(messages))
        return super()._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )


def _make_state(tool_calls=None, content=""):
    """Build a minimal AgentState dict with an AIMessage.

    Deep-copies *content* when it is mutable (e.g. list) so that
    successive calls never share the same object reference.
    """
    safe_content = copy.deepcopy(content) if isinstance(content, list) else content
    msg = AIMessage(content=safe_content, tool_calls=tool_calls or [])
    return {"messages": [msg]}


def _bash_call(cmd="ls"):
    return {"name": "bash", "id": f"call_{cmd}", "args": {"command": cmd}}


class TestHashToolCalls:
    def test_same_calls_same_hash(self):
        a = _hash_tool_calls([_bash_call("ls")])
        b = _hash_tool_calls([_bash_call("ls")])
        assert a == b

    def test_different_calls_different_hash(self):
        a = _hash_tool_calls([_bash_call("ls")])
        b = _hash_tool_calls([_bash_call("pwd")])
        assert a != b

    def test_order_independent(self):
        a = _hash_tool_calls([_bash_call("ls"), {"name": "read_file", "args": {"path": "/tmp"}}])
        b = _hash_tool_calls([{"name": "read_file", "args": {"path": "/tmp"}}, _bash_call("ls")])
        assert a == b

    def test_empty_calls(self):
        h = _hash_tool_calls([])
        assert isinstance(h, str)
        assert len(h) > 0

    def test_stringified_dict_args_match_dict_args(self):
        dict_call = {
            "name": "read_file",
            "args": {"path": "/tmp/demo.py", "start_line": "1", "end_line": "150"},
        }
        string_call = {
            "name": "read_file",
            "args": '{"path":"/tmp/demo.py","start_line":"1","end_line":"150"}',
        }

        assert _hash_tool_calls([dict_call]) == _hash_tool_calls([string_call])

    def test_reversed_read_file_range_matches_forward_range(self):
        forward_call = {
            "name": "read_file",
            "args": {"path": "/tmp/demo.py", "start_line": 10, "end_line": 300},
        }
        reversed_call = {
            "name": "read_file",
            "args": {"path": "/tmp/demo.py", "start_line": 300, "end_line": 10},
        }

        assert _hash_tool_calls([forward_call]) == _hash_tool_calls([reversed_call])

    def test_stringified_non_dict_args_do_not_crash(self):
        non_dict_json_call = {"name": "bash", "args": '"echo hello"'}
        plain_string_call = {"name": "bash", "args": "echo hello"}

        json_hash = _hash_tool_calls([non_dict_json_call])
        plain_hash = _hash_tool_calls([plain_string_call])

        assert isinstance(json_hash, str)
        assert isinstance(plain_hash, str)
        assert json_hash
        assert plain_hash

    def test_grep_pattern_affects_hash(self):
        grep_foo = {"name": "grep", "args": {"path": "/tmp", "pattern": "foo"}}
        grep_bar = {"name": "grep", "args": {"path": "/tmp", "pattern": "bar"}}

        assert _hash_tool_calls([grep_foo]) != _hash_tool_calls([grep_bar])

    def test_glob_pattern_affects_hash(self):
        glob_py = {"name": "glob", "args": {"path": "/tmp", "pattern": "*.py"}}
        glob_ts = {"name": "glob", "args": {"path": "/tmp", "pattern": "*.ts"}}

        assert _hash_tool_calls([glob_py]) != _hash_tool_calls([glob_ts])

    def test_write_file_content_affects_hash(self):
        v1 = {"name": "write_file", "args": {"path": "/tmp/a.py", "content": "v1"}}
        v2 = {"name": "write_file", "args": {"path": "/tmp/a.py", "content": "v2"}}
        assert _hash_tool_calls([v1]) != _hash_tool_calls([v2])

    def test_read_file_line_buckets_clamp_nonpositive_lines(self):
        """评审修复（PR#21 #1）回归：end_line<=0 时桶号必须钳位到 0，
        不得产生负桶（畸形 key path:0--1 使同调用集哈希漂移）。"""
        zero_lines = {"name": "read_file", "args": {"path": "/tmp/a.py", "start_line": 0, "end_line": 0}}
        one_lines = {"name": "read_file", "args": {"path": "/tmp/a.py", "start_line": 1, "end_line": 1}}
        assert _hash_tool_calls([zero_lines]) == _hash_tool_calls([one_lines])

    def test_str_replace_content_affects_hash(self):
        a = {
            "name": "str_replace",
            "args": {"path": "/tmp/a.py", "old_str": "foo", "new_str": "bar"},
        }
        b = {
            "name": "str_replace",
            "args": {"path": "/tmp/a.py", "old_str": "foo", "new_str": "baz"},
        }
        assert _hash_tool_calls([a]) != _hash_tool_calls([b])


class TestLoopDetection:
    def test_no_tool_calls_returns_none(self):
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        state = {"messages": [AIMessage(content="hello")]}
        result = mw._apply(state, runtime)
        assert result is None

    def test_below_threshold_returns_none(self):
        mw = LoopDetectionMiddleware(warn_threshold=3)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        # First two identical calls — no warning
        for _ in range(2):
            result = mw._apply(_make_state(tool_calls=call), runtime)
            assert result is None

    def test_warn_at_threshold_queues_but_does_not_mutate_state(self):
        """At warn threshold, ``after_model`` enqueues but returns None.

        Detection observes the just-emitted AIMessage(tool_calls=...). The
        tools node hasn't run yet, so injecting any non-tool message here
        would split the assistant's tool_calls from their ToolMessage
        responses and break OpenAI/Moonshot pairing. The warning is
        delivered later from ``wrap_model_call`` (upstream #2752).
        """
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=5)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(2):
            mw._apply(_make_state(tool_calls=call), runtime)

        # Third identical call triggers warning detection.
        result = mw._apply(_make_state(tool_calls=call), runtime)
        # Detection must not mutate state — the AIMessage with tool_calls is
        # left untouched so the tools node runs normally, and nothing is
        # written into checkpoint history.
        assert result is None
        # ...but a warning is queued for the next model call.
        assert mw._pending_warnings[_pending_key()]
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key()][0]

    def test_warn_injected_at_next_model_call(self):
        """``wrap_model_call`` appends a HumanMessage(loop_warning) to the
        outgoing messages — *after* every existing message — so that the
        AIMessage(tool_calls=...) -> ToolMessage(...) pairing stays intact.
        """
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]
        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        # Build the messages the agent runtime would assemble for the next
        # turn: prior AIMessage(tool_calls), its ToolMessage responses, ...
        ai_msg = AIMessage(content="", tool_calls=call)
        tool_msg = ToolMessage(content="ok", tool_call_id=call[0]["id"], name="bash")
        request = _make_request([ai_msg, tool_msg], runtime)

        captured, handler = _capture_handler()
        mw.wrap_model_call(request, handler)

        sent = captured[0].messages
        # AIMessage and ToolMessage stay in order, untouched.
        assert sent[0] is ai_msg
        assert sent[1] is tool_msg
        # HumanMessage(warning) appears AFTER the ToolMessage — pairing intact.
        assert isinstance(sent[2], HumanMessage)
        assert sent[2].name == "loop_warning"
        assert "LOOP DETECTED" in sent[2].content

    def test_warn_queue_drained_after_injection(self):
        """A queued warning must be emitted exactly once per detection event."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]
        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        request = _make_request([AIMessage(content="hi")], runtime)
        captured, handler = _capture_handler()

        # First call: warning is appended.
        mw.wrap_model_call(request, handler)
        first = captured[0].messages
        assert any(isinstance(m, HumanMessage) for m in first)

        # Subsequent call without new detection: no warning re-emitted.
        request2 = _make_request([AIMessage(content="hi")], runtime)
        mw.wrap_model_call(request2, handler)
        second = captured[1].messages
        assert not any(isinstance(m, HumanMessage) for m in second)

    def test_warn_queue_scoped_by_run_id(self):
        """A warning queued for one run must not be injected into another run."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime_a = _make_runtime(run_id="run-A")
        runtime_b = _make_runtime(run_id="run-B")
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime_a)

        request_b = _make_request([AIMessage(content="hi")], runtime_b)
        captured, handler = _capture_handler()
        mw.wrap_model_call(request_b, handler)
        assert not any(isinstance(m, HumanMessage) for m in captured[0].messages)
        assert mw._pending_warnings.get(_pending_key(run_id="run-A"))

        request_a = _make_request([AIMessage(content="hi")], runtime_a)
        mw.wrap_model_call(request_a, handler)
        assert any(isinstance(message, HumanMessage) and message.name == "loop_warning" for message in captured[1].messages)

    def test_missing_run_id_uses_invocation_fallback_scope(self):
        """When runtime.context has no ``run_id`` key, warning handling falls
        back to a key scoped to the LangGraph invocation (stream_writer
        identity anchor) instead of a shared literal — concurrent no-run_id
        invocations must not share one budget."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime(run_id=None)
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        fallback_run_id = mw._get_run_id(runtime)
        assert fallback_run_id != "test-run"
        assert mw._pending_warnings.get(_pending_key(run_id=fallback_run_id))

        request = _make_request([AIMessage(content="hi")], runtime)
        captured, handler = _capture_handler()
        mw.wrap_model_call(request, handler)

        loop_warnings = [message for message in captured[0].messages if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert len(loop_warnings) == 1
        assert "LOOP DETECTED" in loop_warnings[0].content
        assert not mw._pending_warnings.get(_pending_key(run_id=fallback_run_id))

    def test_missing_run_id_shares_scope_across_runtime_wrappers(self):
        """LangGraph replaces ``Runtime`` per node but preserves the
        invocation's stream_writer — wrappers of one invocation share scope."""
        mw = LoopDetectionMiddleware()
        invocation_writer = object()
        first_runtime = SimpleNamespace(context={"thread_id": "test-thread"}, stream_writer=invocation_writer, execution_info=None)
        later_runtime = SimpleNamespace(context={"thread_id": "test-thread"}, stream_writer=invocation_writer, execution_info=None)

        assert mw._get_run_id(first_runtime) == mw._get_run_id(later_runtime)

    def test_missing_run_id_fallback_survives_reused_object_address(self, monkeypatch):
        """A later invocation must not inherit a freed anchor's fallback key."""
        mw = LoopDetectionMiddleware()
        monkeypatch.setattr(loop_detection_module, "id", lambda _value: 42, raising=False)
        first_runtime = SimpleNamespace(context={"thread_id": "test-thread"}, stream_writer=object(), execution_info=None)
        first_run_id = mw._get_run_id(first_runtime)
        mw.after_agent({"messages": []}, first_runtime)
        assert not mw._fallback_run_ids

        later_runtime = SimpleNamespace(context={"thread_id": "test-thread"}, stream_writer=object(), execution_info=None)

        assert mw._get_run_id(later_runtime) != first_run_id

    def test_missing_run_id_fallback_map_is_bounded_on_abnormal_exits(self):
        mw = LoopDetectionMiddleware(max_tracked_threads=2)

        for _ in range(10):
            runtime = SimpleNamespace(context={"thread_id": "test-thread"}, stream_writer=object(), execution_info=None)
            mw._get_run_id(runtime)

        assert len(mw._fallback_run_ids) == mw._max_fallback_run_ids == 4

    def test_before_agent_preserves_pending_warning_for_sibling_run(self):
        """An overlapping run must not erase a warning owned by another run
        (upstream #5344: before_agent is a topology-preserving no-op)."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime_a = _make_runtime(run_id="run-A")
        runtime_b = _make_runtime(run_id="run-B")
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime_a)

        assert mw._pending_warnings.get(_pending_key(run_id="run-A"))
        mw.before_agent({"messages": []}, runtime_b)
        assert mw._pending_warnings.get(_pending_key(run_id="run-A"))

        request = _make_request([AIMessage(content="hi")], runtime_a)
        captured, handler = _capture_handler()
        mw.wrap_model_call(request, handler)
        loop_warnings = [message for message in captured[0].messages if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert len(loop_warnings) == 1
        assert not mw._pending_warnings.get(_pending_key(run_id="run-A"))

    def test_after_agent_clears_current_run_pending_warnings(self):
        """Run cleanup should drop warnings that never reached wrap_model_call."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        assert mw._pending_warnings.get(_pending_key())
        mw.after_agent({"messages": []}, runtime)
        assert not mw._pending_warnings.get(_pending_key())

    def test_multiple_pending_warnings_are_merged_into_one_message(self):
        """Edge-case drains should produce one loop_warning prompt message."""
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        mw._pending_warnings[_pending_key()] = ["first warning", "second warning", "first warning"]
        request = _make_request([AIMessage(content="hi")], runtime)
        captured, handler = _capture_handler()

        mw.wrap_model_call(request, handler)

        loop_warnings = [message for message in captured[0].messages if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert len(loop_warnings) == 1
        assert loop_warnings[0].content == "first warning\n\nsecond warning"

    def test_warn_only_queued_once_per_hash(self):
        """Same hash repeated past the threshold should warn only once."""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(2):
            mw._apply(_make_state(tool_calls=call), runtime)

        # Third — warning queued
        mw._apply(_make_state(tool_calls=call), runtime)
        assert len(mw._pending_warnings[_pending_key()]) == 1

        # Fourth — already warned for this hash, no additional enqueue.
        mw._apply(_make_state(tool_calls=call), runtime)
        assert len(mw._pending_warnings[_pending_key()]) == 1

    def test_hard_stop_at_limit(self):
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        # Fourth call triggers hard stop
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 1
        # Hard stop strips tool_calls
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].tool_calls == []
        assert _HARD_STOP_MSG in msgs[0].content

    def test_different_calls_dont_trigger(self):
        mw = LoopDetectionMiddleware(warn_threshold=2)
        runtime = _make_runtime()

        # Each call is different
        for i in range(10):
            result = mw._apply(_make_state(tool_calls=[_bash_call(f"cmd_{i}")]), runtime)
            assert result is None

    def test_window_sliding(self):
        mw = LoopDetectionMiddleware(warn_threshold=3, window_size=5)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        # Fill with 2 identical calls
        mw._apply(_make_state(tool_calls=call), runtime)
        mw._apply(_make_state(tool_calls=call), runtime)

        # Push them out of the window with different calls
        for i in range(5):
            mw._apply(_make_state(tool_calls=[_bash_call(f"other_{i}")]), runtime)

        # Now the original call should be fresh again — no warning
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is None

    def test_warned_hashes_are_pruned_to_sliding_window(self):
        """A long-lived scope should not keep every historical warned hash."""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=100, window_size=4)
        runtime = _make_runtime()

        for i in range(12):
            call = [_bash_call(f"cmd_{i}")]
            mw._apply(_make_state(tool_calls=call), runtime)
            mw._apply(_make_state(tool_calls=call), runtime)

        assert len(mw._history[_scope_key()]) <= 4
        assert set(mw._warned[_scope_key()]).issubset(set(mw._history[_scope_key()]))
        assert len(mw._warned[_scope_key()]) <= 4

    def test_pending_warning_keys_are_capped(self):
        """Abnormal same-thread runs cannot grow pending-warning keys forever."""
        mw = LoopDetectionMiddleware(warn_threshold=2, max_tracked_threads=2)

        for i in range(10):
            runtime = _make_runtime(thread_id="same-thread", run_id=f"run-{i}")
            mw._queue_pending_warning(runtime, f"warning-{i}")

        assert len(mw._pending_warnings) == mw._max_pending_warning_keys
        assert len(mw._pending_warning_touch_order) == mw._max_pending_warning_keys
        assert _pending_key("same-thread", "run-9") in mw._pending_warnings

    def test_failed_model_call_restored_warning_reapplies_pending_key_cap(self):
        """A warning drained for a failed model call may be restored after its
        scope was evicted by a newer queue; the restore path must reapply the
        pending-key cap instead of growing past it."""
        mw = LoopDetectionMiddleware(warn_threshold=2, max_tracked_threads=2)
        runtimes = [_make_runtime(thread_id="same-thread", run_id=f"run-{name}") for name in "ABCD"]
        for runtime, name in zip(runtimes, "ABCD", strict=True):
            mw._queue_pending_warning(runtime, f"warning-{name}")

        request = _make_request([AIMessage(content="hi")], runtimes[0])

        def failing_handler(_request):
            # wrap_model_call 已在 handler 前 drain run A；run E 在调用飞行中
            # 补位到容量上限。失败的 A 恢复时超出上限，必须驱逐最老的 run B。
            runtime_e = _make_runtime(thread_id="same-thread", run_id="run-E")
            mw._queue_pending_warning(runtime_e, "warning-E")
            raise RuntimeError("transient provider failure")

        with pytest.raises(RuntimeError, match="transient provider failure"):
            mw.wrap_model_call(request, failing_handler)

        assert len(mw._pending_warnings) == mw._max_pending_warning_keys
        assert len(mw._pending_warning_touch_order) == mw._max_pending_warning_keys
        assert _pending_key("same-thread", "run-A") in mw._pending_warnings
        assert _pending_key("same-thread", "run-B") not in mw._pending_warnings
        assert _pending_key("same-thread", "run-E") in mw._pending_warnings

    def test_pending_warning_list_is_capped_and_deduped(self):
        """One run cannot accumulate an unbounded warning list."""
        max_pending = loop_detection_module._MAX_PENDING_WARNINGS_PER_RUN
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()

        for i in range(max_pending + 4):
            mw._queue_pending_warning(runtime, f"warning-{i}")
        mw._queue_pending_warning(runtime, f"warning-{max_pending + 3}")

        warnings = mw._pending_warnings[_pending_key()]
        assert len(warnings) == max_pending
        assert warnings == [f"warning-{i}" for i in range(4, max_pending + 4)]

    def test_pending_warning_touch_order_cleared_with_pending_key(self):
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        mw._queue_pending_warning(runtime, "warning")

        mw.after_agent({"messages": []}, runtime)

        assert mw._pending_warnings == {}
        assert mw._pending_warning_touch_order == OrderedDict()

    def test_reset_clears_state(self):
        mw = LoopDetectionMiddleware(warn_threshold=2)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        mw._apply(_make_state(tool_calls=call), runtime)
        mw._apply(_make_state(tool_calls=call), runtime)

        # Would trigger warning, but reset first
        mw.reset()
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is None
        assert not mw._pending_warnings.get(_pending_key())

    def test_non_ai_message_ignored(self):
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        state = {"messages": [SystemMessage(content="hello")]}
        result = mw._apply(state, runtime)
        assert result is None

    def test_empty_messages_ignored(self):
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        result = mw._apply({"messages": []}, runtime)
        assert result is None

    def test_thread_id_from_runtime_context(self):
        """Thread ID should come from runtime.context, not state."""
        mw = LoopDetectionMiddleware(warn_threshold=2)
        runtime_a = _make_runtime("thread-A")
        runtime_b = _make_runtime("thread-B")
        call = [_bash_call("ls")]

        # One call on thread A
        mw._apply(_make_state(tool_calls=call), runtime_a)
        # One call on thread B
        mw._apply(_make_state(tool_calls=call), runtime_b)

        # Second call on thread A — queues warning under thread-A only.
        mw._apply(_make_state(tool_calls=call), runtime_a)
        assert mw._pending_warnings.get(_pending_key("thread-A"))
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key("thread-A")][0]
        assert not mw._pending_warnings.get(_pending_key("thread-B"))

        # Second call on thread B — independent queue.
        mw._apply(_make_state(tool_calls=call), runtime_b)
        assert mw._pending_warnings.get(_pending_key("thread-B"))
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key("thread-B")][0]

    def test_lru_eviction(self):
        """Old scopes should be evicted when max_tracked_threads is exceeded."""
        mw = LoopDetectionMiddleware(warn_threshold=2, max_tracked_threads=3)
        call = [_bash_call("ls")]

        # Fill up 3 threads
        for i in range(3):
            runtime = _make_runtime(f"thread-{i}")
            mw._apply(_make_state(tool_calls=call), runtime)

        # Add a 4th thread — should evict thread-0's scope
        runtime_new = _make_runtime("thread-new")
        mw._apply(_make_state(tool_calls=call), runtime_new)

        assert _scope_key("thread-0", "test-run") not in mw._history
        assert _scope_key("thread-0", "test-run") not in mw._tool_freq
        assert _scope_key("thread-0", "test-run") not in mw._tool_freq_window
        assert _scope_key("thread-0", "test-run") not in mw._tool_freq_warned
        assert _scope_key("thread-new", "test-run") in mw._history
        assert len(mw._history) == 3

    def test_thread_safe_mutations(self):
        """Two threads alternating ``_apply`` on one scope lose no updates.

        Guards the ``self._lock`` critical section in ``_track_and_check``:
        the window append/trim and the freq counter's read-modify-write must
        each land exactly once per call, so after 2xN interleaved calls the
        history holds 2N hashes and the bash counter reads 2N.
        """
        mw = LoopDetectionMiddleware(
            warn_threshold=10_000,
            hard_limit=10_000,
            window_size=10_000,
            tool_freq_warn=10_000,
            tool_freq_hard_limit=10_000,
        )
        runtime = _make_runtime("shared-thread")
        key = _key_of(mw, runtime)
        iterations = 300
        errors: list[BaseException] = []

        def _worker(tag: str) -> None:
            try:
                for i in range(iterations):
                    result = mw._apply(_make_state(tool_calls=[_bash_call(f"{tag}_{i}")]), runtime)
                    if result is not None:
                        errors.append(AssertionError(f"unexpected loop verdict: {result!r}"))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(tag,)) for tag in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(mw._history[key]) == 2 * iterations
        assert mw._tool_freq[key]["bash"] == 2 * iterations

    def test_fallback_thread_id_when_missing(self):
        """When runtime context has no thread_id, should use 'default'."""
        mw = LoopDetectionMiddleware(warn_threshold=2)
        runtime = MagicMock()
        runtime.context = {}
        runtime.stream_writer = object()
        call = [_bash_call("ls")]

        mw._apply(_make_state(tool_calls=call), runtime)
        assert ("default", mw._get_run_id(runtime)) in mw._history


class TestAppendText:
    """Unit tests for LoopDetectionMiddleware._append_text."""

    def test_none_content_returns_text(self):
        result = LoopDetectionMiddleware._append_text(None, "hello")
        assert result == "hello"

    def test_str_content_concatenates(self):
        result = LoopDetectionMiddleware._append_text("existing", "appended")
        assert result == "existing\n\nappended"

    def test_empty_str_content_concatenates(self):
        result = LoopDetectionMiddleware._append_text("", "appended")
        assert result == "\n\nappended"

    def test_list_content_appends_text_block(self):
        """List content (e.g. Anthropic thinking mode) should get a new text block."""
        content = [
            {"type": "thinking", "text": "Let me think..."},
            {"type": "text", "text": "Here is my answer"},
        ]
        result = LoopDetectionMiddleware._append_text(content, "stop msg")
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0] == content[0]
        assert result[1] == content[1]
        assert result[2] == {"type": "text", "text": "\n\nstop msg"}

    def test_empty_list_content_appends_text_block(self):
        result = LoopDetectionMiddleware._append_text([], "stop msg")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == {"type": "text", "text": "\n\nstop msg"}

    def test_unexpected_type_coerced_to_str(self):
        """Unexpected content types should be coerced to str as a fallback."""
        result = LoopDetectionMiddleware._append_text(42, "stop msg")
        assert isinstance(result, str)
        assert result == "42\n\nstop msg"

    def test_list_content_not_mutated_in_place(self):
        """_append_text must not modify the original list."""
        original = [{"type": "text", "text": "hello"}]
        result = LoopDetectionMiddleware._append_text(original, "appended")
        assert len(original) == 1  # original unchanged
        assert len(result) == 2  # new list has the appended block


class TestHardStopWithListContent:
    """Regression tests: hard stop must not crash when AIMessage.content is a list."""

    def test_hard_stop_with_list_content(self):
        """Hard stop on list content should not raise TypeError (regression)."""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        # Build state with list content (e.g. Anthropic thinking mode)
        list_content = [
            {"type": "thinking", "text": "Let me think..."},
            {"type": "text", "text": "I'll run ls"},
        ]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call, content=list_content), runtime)

        # Fourth call triggers hard stop — must not raise TypeError
        result = mw._apply(_make_state(tool_calls=call, content=list_content), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        assert msg.tool_calls == []
        # Content should remain a list with the stop message appended
        assert isinstance(msg.content, list)
        assert len(msg.content) == 3
        assert msg.content[2]["type"] == "text"
        assert _HARD_STOP_MSG in msg.content[2]["text"]

    def test_hard_stop_with_none_content(self):
        """Hard stop on None content should produce a plain string."""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        # Fourth call with default empty-string content
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg.content, str)
        assert _HARD_STOP_MSG in msg.content

    def test_hard_stop_with_str_content(self):
        """Hard stop on str content should concatenate the stop message."""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        for _ in range(3):
            mw._apply(_make_state(tool_calls=call, content="thinking..."), runtime)

        result = mw._apply(_make_state(tool_calls=call, content="thinking..."), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg.content, str)
        assert msg.content.startswith("thinking...")
        assert _HARD_STOP_MSG in msg.content

    def test_hard_stop_clears_raw_tool_call_metadata(self):
        """Forced-stop messages must not retain provider-level raw tool-call payloads."""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        runtime = _make_runtime()
        call = [_bash_call("ls")]

        def _make_provider_state():
            return {
                "messages": [
                    AIMessage(
                        content="thinking...",
                        tool_calls=call,
                        additional_kwargs={
                            "tool_calls": [
                                {
                                    "id": "call_ls",
                                    "type": "function",
                                    "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                                    "thought_signature": "sig-1",
                                }
                            ],
                            "function_call": {"name": "bash", "arguments": '{"command":"ls"}'},
                        },
                        response_metadata={"finish_reason": "tool_calls"},
                    )
                ]
            }

        for _ in range(3):
            mw._apply(_make_provider_state(), runtime)

        result = mw._apply(_make_provider_state(), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert msg.tool_calls == []
        assert "tool_calls" not in msg.additional_kwargs
        assert "function_call" not in msg.additional_kwargs
        assert msg.response_metadata["finish_reason"] == "stop"


class TestToolFrequencyDetection:
    """Tests for per-tool-type frequency detection (Layer 2).

    This catches the case where an agent calls the same tool type many times
    with *different* arguments (e.g. read_file on 40 different files), which
    bypasses hash-based detection.
    """

    def _read_call(self, path):
        return {"name": "read_file", "id": f"call_read_{path}", "args": {"path": path}}

    def test_below_freq_warn_returns_none(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=5, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        for i in range(4):
            result = mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)
            assert result is None

    def test_freq_warn_at_threshold(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=5, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        for i in range(4):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)

        # 5th call to read_file (different file each time) queues a freq
        # warning; state untouched, delivery deferred to wrap_model_call.
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_4.py")]), runtime)
        assert result is None
        queued = mw._pending_warnings.get(_pending_key(), [])
        assert queued
        assert "read_file" in queued[0]
        assert "LOOP DETECTED" in queued[0]

    def test_freq_warn_only_queued_once(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)

        # 3rd queues a frequency warning.
        mw._apply(_make_state(tool_calls=[self._read_call("/file_2.py")]), runtime)
        assert len(mw._pending_warnings[_pending_key()]) == 1

        # 4th: same tool name, no additional enqueue.
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_3.py")]), runtime)
        assert result is None
        assert len(mw._pending_warnings[_pending_key()]) == 1

    def test_freq_hard_stop_at_limit(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=6)
        runtime = _make_runtime()

        for i in range(5):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)

        # 6th call triggers hard stop
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_5.py")]), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        assert msg.tool_calls == []
        assert "FORCED STOP" in msg.content
        assert "read_file" in msg.content

    def test_different_tools_tracked_independently(self):
        """read_file and bash should have independent frequency counters."""
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        # 2 read_file calls
        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)

        # 2 bash calls — should not trigger (bash count = 2, read_file count = 2)
        for i in range(2):
            result = mw._apply(_make_state(tool_calls=[_bash_call(f"cmd_{i}")]), runtime)
            assert result is None

        # 3rd read_file triggers (read_file count = 3)
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_2.py")]), runtime)
        assert result is None
        assert "read_file" in mw._pending_warnings[_pending_key()][0]

    def test_freq_reset_clears_state(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime)

        mw.reset()

        # After reset, count restarts — should not trigger
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_new.py")]), runtime)
        assert result is None

    def test_freq_reset_per_thread_clears_only_target(self):
        """reset(thread_id=...) should clear frequency state for that thread only."""
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime_a = _make_runtime("thread-A")
        runtime_b = _make_runtime("thread-B")

        # 2 calls on each thread
        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/a_{i}.py")]), runtime_a)
            mw._apply(_make_state(tool_calls=[self._read_call(f"/b_{i}.py")]), runtime_b)

        # Reset only thread-A
        mw.reset(thread_id="thread-A")

        assert _scope_key("thread-A", "test-run") not in mw._tool_freq
        assert _scope_key("thread-A", "test-run") not in mw._tool_freq_window
        assert _scope_key("thread-A", "test-run") not in mw._tool_freq_warned

        # thread-B state should still be intact — 3rd call queues a warn.
        result = mw._apply(_make_state(tool_calls=[self._read_call("/b_2.py")]), runtime_b)
        assert result is None
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key("thread-B")][0]

        # thread-A restarted from 0 — should not trigger
        result = mw._apply(_make_state(tool_calls=[self._read_call("/a_new.py")]), runtime_a)
        assert result is None

    def test_freq_per_thread_isolation(self):
        """Frequency counts should be independent per thread."""
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime_a = _make_runtime("thread-A")
        runtime_b = _make_runtime("thread-B")

        # 2 calls on thread A
        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/file_{i}.py")]), runtime_a)

        # 2 calls on thread B — should NOT push thread A over threshold
        for i in range(2):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/other_{i}.py")]), runtime_b)

        # 3rd call on thread A — triggers (count=3 for thread A only)
        result = mw._apply(_make_state(tool_calls=[self._read_call("/file_2.py")]), runtime_a)
        assert result is None
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key("thread-A")][0]
        assert not mw._pending_warnings.get(_pending_key("thread-B"))

    def test_multi_tool_single_response_counted(self):
        """When a single response has multiple tool calls, each is counted."""
        mw = LoopDetectionMiddleware(tool_freq_warn=5, tool_freq_hard_limit=10)
        runtime = _make_runtime()

        # Response 1: 2 read_file calls → count = 2
        call = [self._read_call("/a.py"), self._read_call("/b.py")]
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is None

        # Response 2: 2 more → count = 4
        call = [self._read_call("/c.py"), self._read_call("/d.py")]
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is None

        # Response 3: 1 more → count = 5 → triggers warn
        result = mw._apply(_make_state(tool_calls=[self._read_call("/e.py")]), runtime)
        assert result is None
        assert "read_file" in mw._pending_warnings[_pending_key()][0]

    def test_hash_detection_takes_priority(self):
        """Hash-based hard stop fires before frequency check for identical calls."""
        mw = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=3,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        runtime = _make_runtime()
        call = [self._read_call("/same_file.py")]

        for _ in range(2):
            mw._apply(_make_state(tool_calls=call), runtime)

        # 3rd identical call → hash hard_limit=3 fires (not freq)
        result = mw._apply(_make_state(tool_calls=call), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        assert _HARD_STOP_MSG in msg.content


class TestToolFreqSlidingWindow:
    """A4（2026-10-07 反馈核实批）：Layer 2 频率计数从跨 run 终身累计改为滑窗。

    旧实现的 ``_tool_freq`` 按 (thread, tool) 只增不减——某线程某工具累计达
    hard limit 后永久硬停（线程毒化）。窗口化后计数随窗口滑出自然衰减：
      * 窗内集中爆发仍能触达 warn/hard（窗口长度 ≥ effective hard limit，
        否则阈值是死代码）；
      * 滑出窗口后重新计数、warn 旗标重新武装，下一段爆发可再次告警；
      * 跨 run：计数从窗口现状起步而非历史累计，不再毒化。
    """

    def _read_call(self, path):
        return {"name": "read_file", "id": f"call_read_{path}", "args": {"path": path}}

    def _other_call(self, name):
        return {"name": name, "id": f"call_{name}", "args": {}}

    def test_freq_counts_slide_out_of_window_and_rewarn(self):
        """滑出窗口后重新计数：稀释调用挤掉 read_file 计数，warn 旗标重新武装。

        window = max(window_size=4, hard=8) = 8。第一段 3 次 read_file 触发
        warn；8 次异名稀释调用把 read_file 全部挤出窗口；第二段 3 次
        read_file 再次触发 warn。旧累计实现下第二段永不告警（旗标终身）。
        """
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=8, window_size=4)
        runtime = _make_runtime()

        warnings = 0
        # 第一段：3 次 read_file（不同路径，Layer 1 不触发）→ 第 3 次 warn 入队。
        for i in range(3):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/first_{i}.py")]), runtime)
        warnings += len(mw._pending_warnings.get(_pending_key(), []))
        assert warnings == 1
        # 排空队列，模拟警告已注入。
        mw._pending_warnings.pop(_pending_key(), None)

        # 稀释：8 个异名调用恰好填满整个窗口，read_file 计数滑出归零。
        for i in range(8):
            result = mw._apply(_make_state(tool_calls=[self._other_call(f"other_{i}")]), runtime)
            assert result is None

        # 第二段：3 次 read_file 重新计数 → 第 3 次再次 warn（旗标已重置）。
        for i in range(3):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/second_{i}.py")]), runtime)
        assert len(mw._pending_warnings.get(_pending_key(), [])) == 1
        assert warnings + 1 == 2

    def test_no_permanent_thread_poisoning_across_runs(self):
        """跨 run 不再毒化：多轮累计远超 hard limit 也不永久硬停。

        5 个"run"循环，每轮 9 次 read_file（< hard 10）+ 20 次异名稀释调用
        清空窗口。累计 read_file = 45 远超 hard 10——旧累计实现第 10 次即
        硬停；窗口化后每轮窗内计数 ≤ 9，全程无硬停。
        """
        mw = LoopDetectionMiddleware(tool_freq_warn=1_000_000, tool_freq_hard_limit=10, window_size=20)
        runtime = _make_runtime()

        for cycle in range(5):
            for i in range(9):
                result = mw._apply(_make_state(tool_calls=[self._read_call(f"/c{cycle}_{i}.py")]), runtime)
                assert result is None, f"unexpected verdict in cycle {cycle}: {result!r}"
            for i in range(20):
                result = mw._apply(_make_state(tool_calls=[self._other_call(f"other_{cycle}_{i}")]), runtime)
                assert result is None, f"unexpected verdict in cycle {cycle}: {result!r}"

    def test_hard_limit_reachable_when_window_size_below_hard(self):
        """窗口尺寸红线：deque 长度必须 ≥ hard limit，否则 hard 分支是死代码。

        window_size=4 < hard=6：窗口按 max(window_size, hard)=6 取尺寸，
        连续 6 次同工具调用第 6 次硬停。若误按 window_size=4 截窗，窗内
        计数永不超过 4，本测试变红（窗口尺寸的变异守卫）。
        """
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=6, window_size=4)
        runtime = _make_runtime()

        for i in range(5):
            result = mw._apply(_make_state(tool_calls=[self._read_call(f"/f_{i}.py")]), runtime)
            assert result is None

        result = mw._apply(_make_state(tool_calls=[self._read_call("/f_5.py")]), runtime)
        assert result is not None
        msg = result["messages"][0]
        assert isinstance(msg, AIMessage)
        assert msg.tool_calls == []
        assert "FORCED STOP" in msg.content
        assert "read_file" in msg.content


# ---------------------------------------------------------------------------
# Polling-tool whitelist + recursion_limit linkage
# ---------------------------------------------------------------------------


def _status_call(name="check_video_status", task_id="vg_abc"):
    return {"name": name, "id": f"call_{task_id}", "args": {"task_id": task_id}}


class TestPollingExempt:
    def test_exempt_polling_tool_never_triggers_hash_hard_stop(self):
        mw = LoopDetectionMiddleware(polling_exempt={"check_video_status"})
        state = _make_state([_status_call()])
        runtime = _make_runtime("t")
        # 10 identical calls — well past the default hard_limit of 5.
        for _ in range(10):
            warning, hard = mw._track_and_check(state, runtime)
        assert not hard, "exempt polling tool was force-stopped"
        assert warning is None, "exempt polling tool was warned"

    def test_non_exempt_identical_calls_still_hard_stop(self):
        mw = LoopDetectionMiddleware(polling_exempt={"check_video_status"})
        state = _make_state([_bash_call("ls")])
        runtime = _make_runtime("t")
        hard = False
        for _ in range(5):
            _, hard = mw._track_and_check(state, runtime)
        assert hard, "non-exempt identical bash calls should still hard-stop at 5"


class TestFreqRecursionLinkage:
    """recursion_limit is read via get_config() (langgraph.config), NOT runtime.config
    (Runtime has no .config — see langgraph/runtime.py). Patch the module-level
    get_config the middleware imports.

    Formula (2026-08-13 regression fix, v2): ``min(max(configured, rec), max(rec // 2, configured))``.
    Mutation guard: reverting to the old ``max(configured, rec)`` turns the
    rec=1000 / rec=300 expectations red (500/150 become 1000/300)."""

    def _patch_get_config(self, rec):
        from unittest.mock import patch

        return patch(
            "deerflow.agents.middlewares.loop_detection_middleware.get_config",
            return_value={"recursion_limit": rec},
        )

    def test_freq_hard_limit_is_half_of_large_recursion_limit(self):
        mw = LoopDetectionMiddleware()  # default tool_freq_hard_limit=100
        # Web runs carry recursion_limit=1000 → effective freq cap 500 so the
        # hard stop fires ~1 superstep before the GraphRecursionError wall.
        with self._patch_get_config(1000):
            assert mw._effective_freq_hard_limit() == 500

    def test_freq_hard_limit_300_recursion_runs_at_150(self):
        mw = LoopDetectionMiddleware()
        # A video-gen run has recursion_limit=300; the effective freq limit must
        # stay comfortably below the wall (rec//2) instead of dead-coding at 300.
        with self._patch_get_config(300):
            assert mw._effective_freq_hard_limit() == 150

    def test_freq_hard_limit_im_channel_rec100_unchanged(self):
        # N1 (v2): rec=100 must keep effective=100 — the v1 formula tightened it
        # to 50, which sits below tool_freq_warn=60 and dead-codes the warning.
        mw = LoopDetectionMiddleware()
        with self._patch_get_config(100):
            assert mw._effective_freq_hard_limit() == 100

    def test_freq_hard_limit_floor_is_configured_default(self):
        mw = LoopDetectionMiddleware(tool_freq_hard_limit=100)
        with self._patch_get_config(25):
            assert mw._effective_freq_hard_limit() == 100

    def test_freq_hard_limit_falls_back_when_get_config_unavailable(self):
        # Outside a runnable context get_config() raises; the helper must fall
        # back to the configured default rather than crash.
        from unittest.mock import patch

        mw = LoopDetectionMiddleware(tool_freq_hard_limit=100)

        def _raise():
            raise RuntimeError("not in runnable context")

        with patch(
            "deerflow.agents.middlewares.loop_detection_middleware.get_config",
            side_effect=_raise,
        ):
            assert mw._effective_freq_hard_limit() == 100

    def test_freq_hard_stop_fires_before_recursion_wall_at_1000(self):
        # End-to-end linkage: with rec=1000 a sequential varying-args loop (one
        # read_file per round, distinct paths so Layer-1 hashing never trips)
        # must hard-stop at call 500 — not burn to the recursion wall at 1000.
        # Under the old max(configured, rec) formula the stop fires at 1000 and
        # this test goes red (mutation guard for the formula itself).
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        hard_stopped_at = None
        with self._patch_get_config(1000):
            for i in range(1, 501):
                result = mw._apply(
                    _make_state(tool_calls=[{"name": "read_file", "id": f"call_{i}", "args": {"path": f"/f_{i}.py"}}]),
                    runtime,
                )
                if result is not None:
                    msg = result["messages"][0]
                    if isinstance(msg, AIMessage) and "FORCED STOP" in msg.content:
                        hard_stopped_at = i
                        break
        assert hard_stopped_at == 500


# ---------------------------------------------------------------------------
# F4 (2026-09-29 长任务中断修复计划): error-retry targeted exemption
# ---------------------------------------------------------------------------


def _history_state(call, n_prior: int, status: str = "error", dangling: bool = False):
    """Build a cumulative conversation state for retry-loop simulation.

    For each of *n_prior* prior rounds: AIMessage(same call set) + ToolMessage
    with the given status; then the current AIMessage (what _apply inspects).
    Tool call ids differ per round (real retries get new ids) but the
    name+args hash is identical.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    msgs = []
    for i in range(n_prior):
        msgs.append(AIMessage(content="", tool_calls=[dict(call, id=f"call_{i}")]))
        if not dangling:
            msgs.append(ToolMessage(content="err" if status == "error" else "ok", tool_call_id=f"call_{i}", name=call["name"], status=status))
    msgs.append(AIMessage(content="", tool_calls=[dict(call, id="call_now")]))
    return {"messages": msgs}


class TestErrorRetryExemption:
    """失败后重试定向豁免：第 5/6 次放行（帽 2 次），第 7 次硬停——边界钉死。"""

    def test_failed_retries_exempted_then_hard_stop_at_seventh(self):
        """默认 warn=3/hard=5/帽=2：第 5 次消耗豁免 #1、第 6 次消耗 #2、第 7 次豁免耗尽硬停。"""
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        call = _bash_call("render-part-1")

        # attempts 1..7; prior rounds all failed with status="error"
        for attempt in range(1, 8):
            result = mw._apply(_history_state(call, n_prior=attempt - 1), runtime)
            if attempt == 3:
                # warn path: warning queued, state untouched
                assert result is None
                assert "LOOP DETECTED" in mw._pending_warnings[_pending_key()][0]
            elif attempt <= 6:
                # 1,2,4 = below limit; 5,6 = exemption consumed → 放行（不硬停）
                assert result is None, f"attempt {attempt} should pass"
            else:
                # 7th: exemptions exhausted → hard stop
                assert result is not None
                msg = result["messages"][0]
                assert isinstance(msg, AIMessage)
                assert msg.tool_calls == []
                assert _HARD_STOP_MSG in msg.content

    def test_successful_prior_round_is_not_exempted(self):
        """上一次同哈希调用成功 → 重复即真循环，第 5 次立即硬停（成功路径行为不变）。"""
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        call = _bash_call("ls")

        for attempt in range(1, 5):
            assert mw._apply(_history_state(call, n_prior=attempt - 1, status="success"), runtime) is None

        result = mw._apply(_history_state(call, n_prior=4, status="success"), runtime)
        assert result is not None
        assert isinstance(result["messages"][0], AIMessage)
        assert _HARD_STOP_MSG in result["messages"][0].content

    def test_dangling_prior_round_is_not_exempted(self):
        """找不到同调用集全部 tool_call_id 的 ToolMessage（悬空）→ 保守不豁免，第 5 次硬停。"""
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        call = _bash_call("ls")

        for attempt in range(1, 5):
            mw._apply(_history_state(call, n_prior=attempt - 1, dangling=True), runtime)

        result = mw._apply(_history_state(call, n_prior=4, dangling=True), runtime)
        assert result is not None
        assert isinstance(result["messages"][0], AIMessage)
        assert _HARD_STOP_MSG in result["messages"][0].content

    def test_parallel_call_set_matched_as_whole(self):
        """P-5：豁免判据按调用集整体——集合内任一调用成功即不豁免；全部 error 才豁免。"""
        set_call = [
            {"name": "bash", "id": "call_a", "args": {"command": "render"}},
            {"name": "read_file", "id": "call_b", "args": {"path": "/tmp/log"}},
        ]

        def _mixed_state(n_prior: int, read_status: str):
            msgs = []
            for i in range(n_prior):
                msgs.append(
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "bash", "id": f"call_a_{i}", "args": {"command": "render"}},
                            {"name": "read_file", "id": f"call_b_{i}", "args": {"path": "/tmp/log"}},
                        ],
                    )
                )
                msgs.append(ToolMessage(content="err", tool_call_id=f"call_a_{i}", name="bash", status="error"))
                msgs.append(ToolMessage(content="ok", tool_call_id=f"call_b_{i}", name="read_file", status=read_status))
            msgs.append(AIMessage(content="", tool_calls=[dict(tc, id=f"{tc['id']}_now") for tc in set_call]))
            return {"messages": msgs}

        # read_file 成功 → 集合非全 error → 第 5 次硬停
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        for attempt in range(1, 5):
            mw._apply(_mixed_state(attempt - 1, read_status="success"), runtime)
        result = mw._apply(_mixed_state(4, read_status="success"), runtime)
        assert result is not None
        assert isinstance(result["messages"][0], AIMessage)
        assert _HARD_STOP_MSG in result["messages"][0].content

        # 全部 error → 豁免生效
        mw2 = LoopDetectionMiddleware()
        for attempt in range(1, 5):
            mw2._apply(_mixed_state(attempt - 1, read_status="error"), runtime)
        assert mw2._apply(_mixed_state(4, read_status="error"), runtime) is None

    def test_exemption_cap_is_per_hash(self):
        """帽按（作用域, 哈希）计数：hash A 耗尽后硬停不影响 hash B 的豁免额度。"""
        mw = LoopDetectionMiddleware()
        runtime = _make_runtime()
        call_a = _bash_call("render-part-1")
        call_b = _bash_call("render-part-2")

        # Burn A's two exemptions + land the 7th-call hard stop
        for attempt in range(1, 8):
            mw._apply(_history_state(call_a, n_prior=attempt - 1), runtime)

        # B is a different hash — its 5th call still gets an exemption
        for attempt in range(1, 5):
            mw._apply(_history_state(call_b, n_prior=attempt - 1), runtime)
        assert mw._apply(_history_state(call_b, n_prior=4), runtime) is None

        counts = mw._error_retry_exemptions[_key_of(mw, runtime)]
        assert len(counts) == 2

    def test_exemption_dict_cleared_by_lru_eviction(self):
        """P-2：豁免字典必须挂入 _evict_if_needed 的 LRU 驱逐清单。"""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4, max_tracked_threads=3)
        call = _bash_call("ls")

        # Burn one exemption on thread-0 (4 calls, 4th exempted)
        runtime0 = _make_runtime("thread-0")
        for attempt in range(1, 5):
            mw._apply(_history_state(call, n_prior=attempt - 1), runtime0)
        assert mw._error_retry_exemptions.get(_key_of(mw, runtime0))

        # Push 3 more threads → thread-0 scope evicted
        for i in range(1, 4):
            mw._apply(_make_state(tool_calls=[_bash_call(f"other_{i}")]), _make_runtime(f"thread-{i}"))

        assert _key_of(mw, runtime0) not in mw._history
        assert _key_of(mw, runtime0) not in mw._error_retry_exemptions

    def test_exemption_dict_cleared_by_reset(self):
        """P-2：reset() 两个分支都必须清理豁免字典。"""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=4)
        call = _bash_call("ls")

        runtime_a = _make_runtime("thread-A")
        runtime_b = _make_runtime("thread-B")
        for attempt in range(1, 5):
            mw._apply(_history_state(call, n_prior=attempt - 1), runtime_a)
            mw._apply(_history_state(call, n_prior=attempt - 1), runtime_b)
        assert mw._error_retry_exemptions.get(_key_of(mw, runtime_a))
        assert mw._error_retry_exemptions.get(_key_of(mw, runtime_b))

        mw.reset(thread_id="thread-A")
        assert _key_of(mw, runtime_a) not in mw._error_retry_exemptions
        assert _key_of(mw, runtime_b) in mw._error_retry_exemptions

        mw.reset()
        assert not mw._error_retry_exemptions

    def test_no_prior_occurrence_hard_stops(self):
        """状态里没有历史（首条消息）时触限 → 保守硬停，不豁免。"""
        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3)
        runtime = _make_runtime()
        call = _bash_call("ls")

        for _ in range(3):
            result = mw._apply(_make_state(tool_calls=[call]), runtime)
        assert result is not None
        assert isinstance(result["messages"][0], AIMessage)
        assert _HARD_STOP_MSG in result["messages"][0].content


# ---------------------------------------------------------------------------
# 2026-10-14 加固批：run 级状态隔离（上游 #5344 函数级移植）
# ---------------------------------------------------------------------------


class TestRunScopedTracking:
    def test_identical_call_history_isolated_between_runs_on_same_thread(self):
        """Separate user runs on a cached graph must not share repeat counts."""
        mw = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=3,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        first_run = _make_runtime("shared-thread", "run-1")
        second_run = _make_runtime("shared-thread", "run-2")
        state = _make_state(tool_calls=[_bash_call("pwd")])

        assert mw._apply(state, first_run) is None
        mw.after_agent({"messages": []}, first_run)
        assert mw._apply(state, second_run) is None

        assert not mw._pending_warnings.get(_pending_key("shared-thread", "run-2"))
        assert mw._history[_scope_key("shared-thread", "run-1")] == mw._history[_scope_key("shared-thread", "run-2")]
        assert len(mw._history[_scope_key("shared-thread", "run-1")]) == 1

    def test_same_run_accumulates_across_agent_hook_cycles_for_goal_continuation(self):
        """One Gateway run may re-enter the graph for hidden goal continuations."""
        mw = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=10,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        runtime = _make_runtime("goal-thread", "goal-run")
        state = _make_state(tool_calls=[_bash_call("pwd")])

        assert mw._apply(state, runtime) is None
        mw.after_agent({"messages": []}, runtime)
        assert mw._apply(state, runtime) is None

        assert mw._pending_warnings.get(_pending_key("goal-thread", "goal-run"))
        assert len(mw._history[_scope_key("goal-thread", "goal-run")]) == 2

    def test_tool_frequency_history_isolated_between_runs_on_same_thread(self):
        mw = LoopDetectionMiddleware(
            warn_threshold=100,
            hard_limit=200,
            tool_freq_warn=2,
            tool_freq_hard_limit=3,
        )
        first_run = _make_runtime("shared-thread", "run-1")
        second_run = _make_runtime("shared-thread", "run-2")

        assert mw._apply(_make_state(tool_calls=[_bash_call("first")]), first_run) is None
        mw.after_agent({"messages": []}, first_run)
        assert mw._apply(_make_state(tool_calls=[_bash_call("second")]), second_run) is None

        assert not mw._pending_warnings.get(_pending_key("shared-thread", "run-2"))
        assert mw._tool_freq[_scope_key("shared-thread", "run-1")]["bash"] == 1
        assert mw._tool_freq[_scope_key("shared-thread", "run-2")]["bash"] == 1

    def test_warned_hash_is_run_scoped(self):
        """A warning in one run must not suppress the same warning in a later run."""
        mw = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=100,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        state = _make_state(tool_calls=[_bash_call("pwd")])

        for run_id in ("run-1", "run-2"):
            runtime = _make_runtime("shared-thread", run_id)
            assert mw._apply(state, runtime) is None
            assert mw._apply(state, runtime) is None
            assert mw._pending_warnings.get(_pending_key("shared-thread", run_id))
            mw.after_agent({"messages": []}, runtime)

        assert len(mw._warned[_scope_key("shared-thread", "run-1")]) == 1
        assert len(mw._warned[_scope_key("shared-thread", "run-2")]) == 1

    def test_tool_frequency_warning_suppression_is_run_scoped(self):
        mw = LoopDetectionMiddleware(
            warn_threshold=100,
            hard_limit=200,
            tool_freq_warn=2,
            tool_freq_hard_limit=100,
        )

        for run_id in ("run-1", "run-2"):
            runtime = _make_runtime("shared-thread", run_id)
            for suffix in ("first", "second"):
                assert mw._apply(_make_state(tool_calls=[_bash_call(f"{run_id}-{suffix}")]), runtime) is None
            assert mw._pending_warnings.get(_pending_key("shared-thread", run_id))
            mw.after_agent({"messages": []}, runtime)

        assert mw._tool_freq_warned[_scope_key("shared-thread", "run-1")] == {"bash"}
        assert mw._tool_freq_warned[_scope_key("shared-thread", "run-2")] == {"bash"}

    def test_reset_thread_clears_every_run_scope_and_preserves_other_threads(self):
        mw = LoopDetectionMiddleware(
            warn_threshold=2,
            hard_limit=100,
            tool_freq_warn=3,
            tool_freq_hard_limit=100,
        )
        target_runs = [
            _make_runtime("thread-A", "run-A1"),
            _make_runtime("thread-A", "run-A2"),
        ]
        other_run = _make_runtime("thread-B", "run-B1")
        state = _make_state(tool_calls=[_bash_call("pwd")])
        for runtime in [*target_runs, other_run]:
            mw._apply(state, runtime)
            mw._apply(state, runtime)

        mw.reset(thread_id="thread-A")

        expected_keys = {_scope_key("thread-B", "run-B1")}
        for mapping in (
            mw._history,
            mw._warned,
            mw._tool_freq,
            mw._tool_freq_window,
            mw._tool_freq_warned,
            mw._pending_warnings,
            mw._pending_warning_touch_order,
        ):
            assert set(mapping) == expected_keys, f"{mapping} still holds thread-A scopes"
        # 豁免字典只在真实消耗豁免时才有条目——thread-B 触限前为空属预期；
        # 只需断言 thread-A 的 scope 已被清掉。
        assert not any(key[0] == "thread-A" for key in mw._error_retry_exemptions)

    def test_lru_evicts_one_run_scope_without_dropping_sibling_run_warning(self):
        mw = LoopDetectionMiddleware(
            warn_threshold=100,
            hard_limit=200,
            max_tracked_threads=2,
            tool_freq_warn=100,
            tool_freq_hard_limit=200,
        )
        run_1 = _make_runtime("shared-thread", "run-1")
        run_2 = _make_runtime("shared-thread", "run-2")
        run_3 = _make_runtime("shared-thread", "run-3")

        mw._apply(_make_state(tool_calls=[_bash_call("one")]), run_1)
        mw._queue_pending_warning(run_1, "run-1 warning")
        mw._apply(_make_state(tool_calls=[_bash_call("two")]), run_2)
        mw._queue_pending_warning(run_2, "run-2 warning")
        mw._apply(_make_state(tool_calls=[_bash_call("one-again")]), run_1)
        mw._apply(_make_state(tool_calls=[_bash_call("three")]), run_3)

        assert list(mw._history) == [
            _scope_key("shared-thread", "run-1"),
            _scope_key("shared-thread", "run-3"),
        ]
        assert _scope_key("shared-thread", "run-2") not in mw._tool_freq_window
        assert _scope_key("shared-thread", "run-2") not in mw._tool_freq
        assert _pending_key("shared-thread", "run-1") in mw._pending_warnings
        assert _pending_key("shared-thread", "run-2") not in mw._pending_warnings


# ---------------------------------------------------------------------------
# 2026-10-14 加固批：真实 agent 图集成（瞬态注入 / run 隔离 / 保存成果）
# ---------------------------------------------------------------------------


class TestPendingWarningRestoredOnModelFailure:
    """2026-10-15 评审二轮（上游 #5433 同型）：drain 后模型调用抛异常——警告必须
    回队，外层 LLMErrorHandling 的重试仍能看到它；不得因一次瞬时 provider 故障
    永久丢失护栏。"""

    def test_warning_restored_after_failed_model_call(self):
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]
        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)
        assert mw._pending_warnings.get(_pending_key())

        def failing_handler(_req):
            raise RuntimeError("transient provider 500")

        with pytest.raises(RuntimeError):
            mw.wrap_model_call(_make_request([AIMessage(content="hi")], runtime), failing_handler)
        # 失败后警告已回队
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key()][0]

        captured, ok_handler = _capture_handler()
        mw.wrap_model_call(_make_request([AIMessage(content="hi")], runtime), ok_handler)
        warnings_sent = [m for m in captured[0].messages if isinstance(m, HumanMessage) and m.name == "loop_warning"]
        assert len(warnings_sent) == 1

    @pytest.mark.asyncio
    async def test_warning_restored_after_failed_async_model_call(self):
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]
        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        async def failing_handler(_req):
            raise RuntimeError("transient provider 500")

        request = _make_request([AIMessage(content="hi")], runtime)
        with pytest.raises(RuntimeError):
            await mw.awrap_model_call(request, failing_handler)
        assert "LOOP DETECTED" in mw._pending_warnings[_pending_key()][0]

    def test_successful_call_still_drains(self):
        """成功调用后警告正常消费，不回队。"""
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        call = [_bash_call("ls")]
        for _ in range(3):
            mw._apply(_make_state(tool_calls=call), runtime)

        captured, handler = _capture_handler()
        mw.wrap_model_call(_make_request([AIMessage(content="hi")], runtime), handler)
        assert any(isinstance(m, HumanMessage) for m in captured[0].messages)
        assert not mw._pending_warnings.get(_pending_key())


class TestLoopDetectionAgentGraphIntegration:
    def test_loop_warning_is_transient_in_real_agent_graph(self):
        """after_model queues the warning; wrap_model_call injects it request-only."""

        @as_tool
        def bash(command: str) -> str:
            """Run a fake shell command."""
            return f"ran: {command}"

        repeated_calls = [[{"name": "bash", "id": f"call_ls_{i}", "args": {"command": "ls"}}] for i in range(3)]
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content="", tool_calls=repeated_calls[0]),
                AIMessage(content="", tool_calls=repeated_calls[1]),
                AIMessage(content="", tool_calls=repeated_calls[2]),
                AIMessage(content="final answer"),
            ],
        )
        graph = create_agent(model=model, tools=[bash], middleware=[mw])

        result = graph.invoke(
            {"messages": [("user", "inspect the directory")]},
            context={"thread_id": "integration-thread", "run_id": "integration-run"},
            config={"recursion_limit": 20},
        )

        assert len(model.seen_messages) == 4
        loop_warnings_by_call = [[message for message in messages if isinstance(message, HumanMessage) and message.name == "loop_warning"] for messages in model.seen_messages]
        assert loop_warnings_by_call[0] == []
        assert loop_warnings_by_call[1] == []
        assert loop_warnings_by_call[2] == []
        assert len(loop_warnings_by_call[3]) == 1
        assert "LOOP DETECTED" in loop_warnings_by_call[3][0].content

        fourth_request = model.seen_messages[3]
        assert isinstance(fourth_request[-2], ToolMessage)
        assert fourth_request[-2].tool_call_id == "call_ls_2"
        assert fourth_request[-1] is loop_warnings_by_call[3][0]

        persisted_loop_warnings = [message for message in result["messages"] if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert persisted_loop_warnings == []
        assert result["messages"][-1].content == "final answer"
        assert mw._pending_warnings == {}
        assert mw._pending_warning_touch_order == OrderedDict()

    @pytest.mark.asyncio
    async def test_loop_warning_is_transient_in_async_agent_graph(self):
        """awrap_model_call injects loop_warning request-only in async graph runs."""

        @as_tool
        async def bash(command: str) -> str:
            """Run a fake shell command."""
            return f"ran: {command}"

        repeated_calls = [[{"name": "bash", "id": f"call_async_ls_{i}", "args": {"command": "ls"}}] for i in range(3)]
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content="", tool_calls=repeated_calls[0]),
                AIMessage(content="", tool_calls=repeated_calls[1]),
                AIMessage(content="", tool_calls=repeated_calls[2]),
                AIMessage(content="async final answer"),
            ],
        )
        graph = create_agent(model=model, tools=[bash], middleware=[mw])

        result = await graph.ainvoke(
            {"messages": [("user", "inspect the directory asynchronously")]},
            context={"thread_id": "async-integration-thread", "run_id": "async-integration-run"},
            config={"recursion_limit": 20},
        )

        assert len(model.seen_messages) == 4
        loop_warnings_by_call = [[message for message in messages if isinstance(message, HumanMessage) and message.name == "loop_warning"] for messages in model.seen_messages]
        assert loop_warnings_by_call[0] == []
        assert loop_warnings_by_call[1] == []
        assert loop_warnings_by_call[2] == []
        assert len(loop_warnings_by_call[3]) == 1
        assert "LOOP DETECTED" in loop_warnings_by_call[3][0].content

        fourth_request = model.seen_messages[3]
        assert isinstance(fourth_request[-2], ToolMessage)
        assert fourth_request[-2].tool_call_id == "call_async_ls_2"
        assert fourth_request[-1] is loop_warnings_by_call[3][0]

        persisted_loop_warnings = [message for message in result["messages"] if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert persisted_loop_warnings == []
        assert result["messages"][-1].content == "async final answer"
        assert mw._pending_warnings == {}
        assert mw._pending_warning_touch_order == OrderedDict()

    def test_reused_agent_graph_isolates_loop_history_between_runs(self):
        """A cached graph must give each new run a fresh loop-detection budget —
        run A 已触发警告后，run B 的合法调用仍能照常执行。"""

        @as_tool
        def bash(command: str) -> str:
            """Run a fake shell command."""
            return f"ran: {command}"

        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-1-call", "args": {"command": "pwd"}}]),
                AIMessage(content="first final answer"),
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-2-call", "args": {"command": "pwd"}}]),
                AIMessage(content="second final answer"),
            ],
        )
        graph = create_agent(model=model, tools=[bash], middleware=[mw])

        first = graph.invoke(
            {"messages": [("user", "where am I?")]},
            context={"thread_id": "cached-thread", "run_id": "run-1"},
            config={"recursion_limit": 10},
        )
        second = graph.invoke(
            {"messages": [("user", "where am I now?")]},
            context={"thread_id": "cached-thread", "run_id": "run-2"},
            config={"recursion_limit": 10},
        )

        assert first["messages"][-1].content == "first final answer"
        assert second["messages"][-1].content == "second final answer"
        assert len(model.seen_messages) == 4
        assert not any(isinstance(message, HumanMessage) and message.name == "loop_warning" for request_messages in model.seen_messages for message in request_messages)
        assert set(mw._history) == {
            _scope_key("cached-thread", "run-1"),
            _scope_key("cached-thread", "run-2"),
        }

    def test_reused_agent_graph_without_run_id_gets_one_budget_per_invocation(self):
        """Library embedders may omit run_id while reusing one compiled graph —
        the stream_writer invocation anchor keeps one budget per invocation."""

        @as_tool
        def bash(command: str) -> str:
            """Run a fake shell command."""
            return f"ran: {command}"

        mw = LoopDetectionMiddleware(warn_threshold=2, hard_limit=3, tool_freq_warn=100, tool_freq_hard_limit=200)
        model = _CapturingFakeMessagesListChatModel(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-1-a", "args": {"command": "pwd"}}]),
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-1-b", "args": {"command": "pwd"}}]),
                AIMessage(content="first final answer"),
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-2-a", "args": {"command": "pwd"}}]),
                AIMessage(content="", tool_calls=[{"name": "bash", "id": "run-2-b", "args": {"command": "pwd"}}]),
                AIMessage(content="second final answer"),
            ],
        )
        graph = create_agent(model=model, tools=[bash], middleware=[mw])
        shared_context = {"thread_id": "library-thread"}

        first = graph.invoke(
            {"messages": [("user", "where am I?")]},
            context=shared_context,
            config={"recursion_limit": 15},
        )
        second = graph.invoke(
            {"messages": [("user", "where am I now?")]},
            context=shared_context,
            config={"recursion_limit": 15},
        )

        assert first["messages"][-1].content == "first final answer"
        assert second["messages"][-1].content == "second final answer"
        loop_warnings_by_call = [[message for message in messages if isinstance(message, HumanMessage) and message.name == "loop_warning"] for messages in model.seen_messages]
        assert [len(warnings) for warnings in loop_warnings_by_call] == [0, 0, 1, 0, 0, 1]
        assert not mw._fallback_run_ids


# ---------------------------------------------------------------------------
# 2026-10-14 加固批：警告后保存阶段成果（上游 #6804 文案移植）
# ---------------------------------------------------------------------------


class TestWarningLetsAgentSaveResultsFirst:
    """F67（上游 #6804）：warn 级循环消息不得让 agent 丢弃它被要求保存的文件。
    警告要求先用文件写入工具保存已收集结果再给最终答案；hard stop 不变。
    本仓无 ReadBeforeWriteMiddleware，真实门测试按本仓工具面适配为
    "警告后 write_file 真实落盘"。"""

    @staticmethod
    def _write_call(path="/mnt/user-data/workspace/out.md"):
        return {"name": "write_file", "id": "call_write", "args": {"path": path, "content": "partial results"}}

    @staticmethod
    def _read_call(path):
        return {"name": "read_file", "id": f"call_read_{path}", "args": {"path": path}}

    def test_hash_warning_asks_to_save_before_final_answer(self):
        mw = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10)
        runtime = _make_runtime()
        for _ in range(3):
            mw._apply(_make_state(tool_calls=[_bash_call("ls")]), runtime)
        warning = mw._pending_warnings[_pending_key()][0]
        assert "LOOP DETECTED" in warning
        assert "Stop calling tools and produce your final answer now" not in warning
        assert "write_file" in warning
        assert warning.index("write what you have collected") < warning.index("produce your final answer")

    def test_freq_warning_names_the_looping_tool_and_allows_saving(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime = _make_runtime()
        for i in range(3):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/f_{i}")]), runtime)
        warning = mw._pending_warnings[_pending_key()][0]
        assert "Stop calling read_file" in warning
        assert "Stop calling tools and produce your final answer now" not in warning
        assert "write_file" in warning

    def test_save_call_after_warning_is_not_stripped(self):
        mw = LoopDetectionMiddleware(tool_freq_warn=3, tool_freq_hard_limit=10)
        runtime = _make_runtime()
        for i in range(3):
            mw._apply(_make_state(tool_calls=[self._read_call(f"/f_{i}")]), runtime)
        assert mw._pending_warnings[_pending_key()]
        # The follow-up write is a different tool and a different call set:
        # neither detection layer acts on it, so the write reaches the tools node.
        assert mw._apply(_make_state(tool_calls=[self._write_call()]), runtime) is None

    @pytest.mark.parametrize("layer", ["hash", "frequency"])
    def test_warning_allows_a_bounded_read_before_write_recovery(self, layer):
        mw = LoopDetectionMiddleware(warn_threshold=3 if layer == "hash" else 10, hard_limit=20, tool_freq_warn=3 if layer == "frequency" else 10, tool_freq_hard_limit=20)
        runtime = _make_runtime()
        for i in range(3):
            mw._apply(_make_state(tool_calls=[_bash_call("ls" if layer == "hash" else f"cmd_{i}")]), runtime)
        warning = mw._pending_warnings[_pending_key()][0]
        assert "read-before-write" in warning
        assert "read the target file" in warning
        assert "retry the save once" in warning
        assert warning.index("retry the save once") < warning.index("produce your final answer")

    @pytest.mark.parametrize("layer", ["hash", "frequency"])
    def test_looping_writer_requires_an_alternative_or_an_unsaved_result(self, layer):
        mw = LoopDetectionMiddleware(warn_threshold=3 if layer == "hash" else 10, hard_limit=20, tool_freq_warn=3 if layer == "frequency" else 10, tool_freq_hard_limit=20)
        runtime = _make_runtime()
        for i in range(3):
            mw._apply(_make_state(tool_calls=[self._write_call("/out.md" if layer == "hash" else f"/out_{i}.md")]), runtime)
        warning = mw._pending_warnings[_pending_key()][0]
        assert "unless it is the looping tool" in warning
        assert "different available file-writing tool" in warning
        assert "Do not use a tool that this warning tells you to stop" in warning
        assert "include the collected results in your final answer" in warning
        assert "file was not saved" in warning

    @pytest.mark.asyncio
    async def test_warned_agent_actually_saves_results_to_disk(self, tmp_path):
        """计划 C 验收：警告后真实 write_file 落盘，而非只生成总结。"""

        @as_tool
        async def bash(command: str) -> str:
            """Return a deterministic research result without running a shell."""
            return "partial results"

        @as_tool
        async def write_file(path: str, content: str) -> str:
            """Save the collected results."""
            from pathlib import Path

            Path(path).write_text(content, encoding="utf-8")
            return "saved"

        output = tmp_path / "out.md"
        loop = LoopDetectionMiddleware(warn_threshold=3, hard_limit=10, tool_freq_warn=10, tool_freq_hard_limit=20)
        calls = [dict(_bash_call("ls"), id=f"research_{i}") for i in range(3)]
        calls.append(dict(self._write_call(str(output)), id="final_save"))
        model = _CapturingFakeMessagesListChatModel(
            responses=[*[AIMessage(content="", tool_calls=[call]) for call in calls], AIMessage(content="Saved partial results.")],
        )
        graph = create_agent(model=model, tools=[bash, write_file], middleware=[loop])
        result = await graph.ainvoke(
            {"messages": [("user", f"Save your research to {output}.")]},
            context={"thread_id": "save-thread", "run_id": "save-run"},
            config={"recursion_limit": 30},
        )

        warnings = [message for message in model.seen_messages[3] if isinstance(message, HumanMessage) and message.name == "loop_warning"]
        assert len(warnings) == 1 and "retry the save once" in warnings[0].content
        # The save really happened — the file on disk holds the partial results.
        assert output.read_text(encoding="utf-8") == "partial results"
        assert result["messages"][-1].content == "Saved partial results."

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("_HARD_STOP_MSG", "[FORCED STOP] Repeated tool calls exceeded the safety limit. Producing final answer with results collected so far."),
            ("_TOOL_FREQ_HARD_STOP_MSG", "[FORCED STOP] Tool {tool_name} called {count} times — exceeded the per-tool safety limit. Producing final answer with results collected so far."),
        ],
    )
    def test_hard_stop_message_unchanged(self, name, expected):
        message = getattr(loop_detection_module, name)
        assert message == expected
        assert loop_detection_module._WRAP_UP_INSTRUCTIONS not in message

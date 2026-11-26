"""Tests for TodoMiddleware context-loss detection."""

import asyncio
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.todo_middleware import (
    TodoMiddleware,
    _completion_reminder_count,
    _format_todos,
    _progress_reminder_after_last_update,
    _reminder_in_messages,
    _todos_in_messages,
    _tool_rounds_since_last_todo_update,
)


def _ai_with_write_todos():
    return AIMessage(content="", tool_calls=[{"name": "write_todos", "id": "tc_1", "args": {}}])


def _reminder_msg():
    return HumanMessage(name="todo_reminder", content="reminder")


def _make_runtime():
    runtime = MagicMock()
    runtime.context = {"thread_id": "test-thread"}
    return runtime


def _sample_todos():
    return [
        {"status": "completed", "content": "Set up project"},
        {"status": "in_progress", "content": "Write tests"},
        {"status": "pending", "content": "Deploy"},
    ]


class TestTodosInMessages:
    def test_true_when_write_todos_present(self):
        msgs = [HumanMessage(content="hi"), _ai_with_write_todos()]
        assert _todos_in_messages(msgs) is True

    def test_false_when_no_write_todos(self):
        msgs = [
            HumanMessage(content="hi"),
            AIMessage(content="hello", tool_calls=[{"name": "bash", "id": "tc_1", "args": {}}]),
        ]
        assert _todos_in_messages(msgs) is False

    def test_false_for_empty_list(self):
        assert _todos_in_messages([]) is False

    def test_false_for_ai_without_tool_calls(self):
        msgs = [AIMessage(content="hello")]
        assert _todos_in_messages(msgs) is False


class TestReminderInMessages:
    def test_true_when_reminder_present(self):
        msgs = [HumanMessage(content="hi"), _reminder_msg()]
        assert _reminder_in_messages(msgs) is True

    def test_false_when_no_reminder(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert _reminder_in_messages(msgs) is False

    def test_false_for_empty_list(self):
        assert _reminder_in_messages([]) is False

    def test_false_for_human_without_name(self):
        msgs = [HumanMessage(content="todo_reminder")]
        assert _reminder_in_messages(msgs) is False


class TestFormatTodos:
    def test_formats_multiple_items(self):
        todos = _sample_todos()
        result = _format_todos(todos)
        assert "- [completed] Set up project" in result
        assert "- [in_progress] Write tests" in result
        assert "- [pending] Deploy" in result

    def test_empty_list(self):
        assert _format_todos([]) == ""

    def test_missing_fields_use_defaults(self):
        todos = [{"content": "No status"}, {"status": "done"}]
        result = _format_todos(todos)
        assert "- [pending] No status" in result
        assert "- [done] " in result


class TestBeforeModel:
    def test_returns_none_when_no_todos(self):
        mw = TodoMiddleware()
        state = {"messages": [HumanMessage(content="hi")], "todos": []}
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_todos_is_none(self):
        mw = TodoMiddleware()
        state = {"messages": [HumanMessage(content="hi")], "todos": None}
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_write_todos_still_visible(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_with_write_todos()],
            "todos": _sample_todos(),
        }
        assert mw.before_model(state, _make_runtime()) is None

    def test_returns_none_when_reminder_already_present(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi"), _reminder_msg()],
            "todos": _sample_todos(),
        }
        assert mw.before_model(state, _make_runtime()) is None

    def test_injects_reminder_when_todos_exist_but_truncated(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi"), AIMessage(content="sure")],
            "todos": _sample_todos(),
        }
        result = mw.before_model(state, _make_runtime())
        assert result is not None
        msgs = result["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].name == "todo_reminder"

    def test_reminder_contains_formatted_todos(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi")],
            "todos": _sample_todos(),
        }
        result = mw.before_model(state, _make_runtime())
        content = result["messages"][0].content
        assert "Set up project" in content
        assert "Write tests" in content
        assert "Deploy" in content
        assert "system_reminder" in content


class TestAbeforeModel:
    def test_delegates_to_sync(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi")],
            "todos": _sample_todos(),
        }
        result = asyncio.run(mw.abefore_model(state, _make_runtime()))
        assert result is not None
        assert result["messages"][0].name == "todo_reminder"


def _completion_reminder_msg():
    return HumanMessage(name="todo_completion_reminder", content="finish your todos")


def _ai_no_tool_calls():
    return AIMessage(content="I'm done!")


def _incomplete_todos():
    return [
        {"status": "completed", "content": "Step 1"},
        {"status": "in_progress", "content": "Step 2"},
        {"status": "pending", "content": "Step 3"},
    ]


def _all_completed_todos():
    return [
        {"status": "completed", "content": "Step 1"},
        {"status": "completed", "content": "Step 2"},
    ]


class TestCompletionReminderCount:
    def test_zero_when_no_reminders(self):
        msgs = [HumanMessage(content="hi"), _ai_no_tool_calls()]
        assert _completion_reminder_count(msgs) == 0

    def test_counts_completion_reminders(self):
        msgs = [_completion_reminder_msg(), _completion_reminder_msg()]
        assert _completion_reminder_count(msgs) == 2

    def test_does_not_count_todo_reminders(self):
        msgs = [_reminder_msg(), _completion_reminder_msg()]
        assert _completion_reminder_count(msgs) == 1


class TestAfterModel:
    def test_returns_none_when_agent_still_using_tools(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_with_write_todos()],
            "todos": _incomplete_todos(),
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_returns_none_when_no_todos(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_no_tool_calls()],
            "todos": [],
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_returns_none_when_todos_is_none(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_no_tool_calls()],
            "todos": None,
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_returns_none_when_all_completed(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_no_tool_calls()],
            "todos": _all_completed_todos(),
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_returns_none_when_no_messages(self):
        mw = TodoMiddleware()
        state = {
            "messages": [],
            "todos": _incomplete_todos(),
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_injects_reminder_and_jumps_to_model_when_incomplete(self):
        mw = TodoMiddleware()
        state = {
            "messages": [HumanMessage(content="hi"), _ai_no_tool_calls()],
            "todos": _incomplete_todos(),
        }
        result = mw.after_model(state, _make_runtime())
        assert result is not None
        assert result["jump_to"] == "model"
        assert len(result["messages"]) == 1
        reminder = result["messages"][0]
        assert isinstance(reminder, HumanMessage)
        assert reminder.name == "todo_completion_reminder"
        assert "Step 2" in reminder.content
        assert "Step 3" in reminder.content

    def test_reminder_lists_only_incomplete_items(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_no_tool_calls()],
            "todos": _incomplete_todos(),
        }
        result = mw.after_model(state, _make_runtime())
        content = result["messages"][0].content
        assert "Step 1" not in content  # completed — should not appear
        assert "Step 2" in content
        assert "Step 3" in content

    def test_allows_exit_after_max_reminders(self):
        mw = TodoMiddleware()
        state = {
            "messages": [
                _completion_reminder_msg(),
                _completion_reminder_msg(),
                _ai_no_tool_calls(),
            ],
            "todos": _incomplete_todos(),
        }
        assert mw.after_model(state, _make_runtime()) is None

    def test_still_sends_reminder_before_cap(self):
        mw = TodoMiddleware()
        state = {
            "messages": [
                _completion_reminder_msg(),  # 1 reminder so far
                _ai_no_tool_calls(),
            ],
            "todos": _incomplete_todos(),
        }
        result = mw.after_model(state, _make_runtime())
        assert result is not None
        assert result["jump_to"] == "model"


class TestAafterModel:
    def test_delegates_to_sync(self):
        mw = TodoMiddleware()
        state = {
            "messages": [_ai_no_tool_calls()],
            "todos": _incomplete_todos(),
        }
        result = asyncio.run(mw.aafter_model(state, _make_runtime()))
        assert result is not None
        assert result["jump_to"] == "model"
        assert result["messages"][0].name == "todo_completion_reminder"


# ── 运行中停滞提醒（BUG-03）：write_todos 之后连续 K 轮业务工具调用没有更新清单 ──────────


def _write_todos_msg(call_id: str = "wt"):
    return AIMessage(content="", tool_calls=[{"name": "write_todos", "id": call_id, "args": {"todos": []}}])


def _tool_round(call_id: str, *names: str):
    """一条含工具调用的回复（并行调用算一轮）。"""
    return AIMessage(content="", tool_calls=[{"name": name, "id": f"{call_id}-{index}", "args": {}} for index, name in enumerate(names)])


def _progress_reminder_msg():
    return HumanMessage(name="todo_progress_reminder", content="stale", additional_kwargs={"hide_from_ui": True})


def _stale_messages(rounds: int):
    """write_todos 之后连续 *rounds* 轮业务工具调用。"""
    return [HumanMessage(content="go"), _write_todos_msg(), *[_tool_round(f"r{index}", "do_step") for index in range(rounds)]]


def _in_progress_todos():
    return [
        {"status": "in_progress", "content": "Step A"},
        {"status": "pending", "content": "Step B"},
    ]


@pytest.fixture()
def k6(monkeypatch):
    monkeypatch.setattr(TodoMiddleware, "_PROGRESS_STALE_TOOL_ROUNDS", 6)


@pytest.mark.usefixtures("k6")
class TestProgressReminder:
    def test_counts_tool_rounds_since_last_write_todos(self):
        messages = [
            HumanMessage(content="go"),
            _write_todos_msg("first"),
            _tool_round("old", "do_step"),  # 在最后一次 write_todos 之前：不计
            _write_todos_msg("second"),
            _tool_round("a", "do_step"),
            _tool_round("b", "do_step", "read_file"),  # 并行两个调用仍是一轮
            ToolMessage(content="ok", tool_call_id="b-0"),
            AIMessage(content="thinking aloud, no tool call"),  # 无工具调用：不计
        ]

        assert _tool_rounds_since_last_todo_update(messages) == 2

    def test_rounds_none_without_write_todos(self):
        assert _tool_rounds_since_last_todo_update([HumanMessage(content="go"), _tool_round("a", "do_step")]) is None

    def test_injects_progress_reminder_after_k_rounds(self):
        result = TodoMiddleware().before_model({"messages": _stale_messages(6), "todos": _in_progress_todos()}, _make_runtime())

        assert result is not None
        assert set(result) == {"messages"}  # 不返回 todos 键：绝不改任何状态
        (reminder,) = result["messages"]
        assert isinstance(reminder, HumanMessage)
        assert reminder.name == "todo_progress_reminder"
        assert reminder.additional_kwargs["hide_from_ui"] is True
        assert "6 rounds" in reminder.content
        assert "Do not mark a step `completed` unless it is fully done" in reminder.content

    def test_no_progress_reminder_below_k_rounds(self):
        assert TodoMiddleware().before_model({"messages": _stale_messages(5), "todos": _in_progress_todos()}, _make_runtime()) is None

    def test_progress_reminder_once_per_stale_window(self):
        messages = [*_stale_messages(6), _progress_reminder_msg(), *[_tool_round(f"late{index}", "do_step") for index in range(6)]]

        assert _progress_reminder_after_last_update(messages) is True
        assert TodoMiddleware().before_model({"messages": messages, "todos": _in_progress_todos()}, _make_runtime()) is None

    def test_progress_reminder_rearms_after_write_todos(self):
        messages = [
            *_stale_messages(6),
            _progress_reminder_msg(),
            _write_todos_msg("again"),
            *[_tool_round(f"next{index}", "do_step") for index in range(6)],
        ]

        assert _progress_reminder_after_last_update(messages) is False
        result = TodoMiddleware().before_model({"messages": messages, "todos": _in_progress_todos()}, _make_runtime())
        assert result is not None
        assert result["messages"][0].name == "todo_progress_reminder"

    @pytest.mark.parametrize(
        "todos",
        [
            [{"status": "pending", "content": "A"}, {"status": "pending", "content": "B"}],
            [{"status": "completed", "content": "A"}, {"status": "completed", "content": "B"}],
        ],
        ids=["all-pending", "all-completed"],
    )
    def test_no_progress_reminder_without_in_progress_item(self, todos):
        assert TodoMiddleware().before_model({"messages": _stale_messages(6), "todos": todos}, _make_runtime()) is None

    def test_context_loss_reminder_unchanged(self):
        """消息里已经没有 write_todos：仍走既有的上下文丢失提醒，不被新分支抢走。"""
        messages = [HumanMessage(content="go"), *[_tool_round(f"r{index}", "do_step") for index in range(8)]]

        result = TodoMiddleware().before_model({"messages": messages, "todos": _in_progress_todos()}, _make_runtime())

        assert result is not None
        assert result["messages"][0].name == "todo_reminder"

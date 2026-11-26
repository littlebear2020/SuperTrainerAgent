"""真实 Agent 图：To-dos 停滞时的运行中提醒要让清单在终态之前发生中间迁移（BUG-03）。

脚本模型只在「最后一条消息就是 todo_progress_reminder」时才推进清单——提醒没注入，
它就一直调业务工具，图会撞上递归上限而失败；因此这条用例同时证明了提醒确实经由真实
中间件链送达了模型，而不是靠脚本自己计数。
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.agents.thread_state import ThreadState


@tool
def do_step() -> str:
    """Perform one business step."""
    return "step done"


def _write_todos(call_id: str, *statuses: str) -> AIMessage:
    todos = [{"content": name, "status": status} for name, status in zip("AB", statuses, strict=True)]
    return AIMessage(content="", tool_calls=[{"name": "write_todos", "id": call_id, "args": {"todos": todos}}])


def _calls_write_todos(message: AIMessage) -> bool:
    return any(call["name"] == "write_todos" for call in message.tool_calls)


class _ScriptedModel(BaseChatModel):
    """第 1 轮写清单；之后每轮调 do_step；看到停滞提醒才把 A 标完成、B 开始；再 2 轮后全部完成并收尾。"""

    @property
    def _llm_type(self) -> str:
        return "scripted-todos"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        call_id = f"call-{len(messages)}"
        ai_positions = [index for index, message in enumerate(messages) if isinstance(message, AIMessage)]
        write_positions = [index for index in ai_positions if _calls_write_todos(messages[index])]
        last = messages[-1]

        if not write_positions:
            reply = _write_todos(call_id, "in_progress", "pending")
        elif isinstance(last, HumanMessage) and last.name == "todo_progress_reminder":
            reply = _write_todos(call_id, "completed", "in_progress")
        elif len(write_positions) >= 3:
            reply = AIMessage(content="all done")
        elif len(write_positions) == 2 and sum(1 for index in ai_positions if index > write_positions[1]) >= 2:
            reply = _write_todos(call_id, "completed", "completed")
        else:
            reply = AIMessage(content="", tool_calls=[{"name": "do_step", "id": call_id, "args": {}}])
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _statuses(snapshot: dict) -> list[str]:
    return [item["status"] for item in (snapshot.get("todos") or [])]


def _last_ai(snapshot: dict) -> AIMessage | None:
    return next((m for m in reversed(snapshot.get("messages") or []) if isinstance(m, AIMessage)), None)


def test_todos_advance_mid_run_after_progress_reminder():
    agent = create_agent(model=_ScriptedModel(), tools=[do_step], middleware=[TodoMiddleware()], state_schema=ThreadState)

    snapshots = list(agent.stream({"messages": [("user", "do the work")]}, {"recursion_limit": 200}, stream_mode="values"))

    # 清单在终态之前依次迁移：[A 进行中, B 待办] → [A 完成, B 进行中] → [A 完成, B 完成]
    advanced = next(index for index, snap in enumerate(snapshots) if _statuses(snap) == ["completed", "in_progress"])
    final_reply = next(index for index, snap in enumerate(snapshots) if (ai := _last_ai(snap)) is not None and not ai.tool_calls)
    assert advanced < final_reply
    assert _statuses(snapshots[-1]) == ["completed", "completed"]

    reminders = [m for m in snapshots[-1]["messages"] if isinstance(m, HumanMessage) and getattr(m, "name", None) == "todo_progress_reminder"]
    assert len(reminders) == 1
    assert reminders[0].additional_kwargs.get("hide_from_ui") is True

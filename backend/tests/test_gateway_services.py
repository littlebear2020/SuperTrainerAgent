"""Tests for app.gateway.services — run lifecycle service layer."""

from __future__ import annotations

import json

import pytest


def test_format_sse_basic():
    from app.gateway.services import format_sse

    frame = format_sse("metadata", {"run_id": "abc"})
    assert frame.startswith("event: metadata\n")
    assert "data: " in frame
    parsed = json.loads(frame.split("data: ")[1].split("\n")[0])
    assert parsed["run_id"] == "abc"


def test_format_sse_with_event_id():
    from app.gateway.services import format_sse

    frame = format_sse("metadata", {"run_id": "abc"}, event_id="123-0")
    assert "id: 123-0" in frame


def test_format_sse_end_event_null():
    from app.gateway.services import format_sse

    frame = format_sse("end", None)
    assert "data: null" in frame


def test_format_sse_no_event_id():
    from app.gateway.services import format_sse

    frame = format_sse("values", {"x": 1})
    assert "id:" not in frame


def test_normalize_stream_modes_none():
    from app.gateway.services import normalize_stream_modes

    assert normalize_stream_modes(None) == ["values"]


def test_normalize_stream_modes_string():
    from app.gateway.services import normalize_stream_modes

    assert normalize_stream_modes("messages-tuple") == ["messages-tuple"]


def test_normalize_stream_modes_list():
    from app.gateway.services import normalize_stream_modes

    assert normalize_stream_modes(["values", "messages-tuple"]) == ["values", "messages-tuple"]


def test_normalize_stream_modes_empty_list():
    from app.gateway.services import normalize_stream_modes

    assert normalize_stream_modes([]) == ["values"]


def test_normalize_input_none():
    from app.gateway.services import normalize_input

    assert normalize_input(None) == {}


def test_normalize_input_with_messages():
    from app.gateway.services import normalize_input

    result = normalize_input({"messages": [{"role": "user", "content": "hi"}]})
    assert len(result["messages"]) == 1
    assert result["messages"][0].content == "hi"


def test_normalize_input_passthrough():
    from app.gateway.services import normalize_input

    result = normalize_input({"custom_key": "value"})
    assert result == {"custom_key": "value"}


def test_normalize_input_preserves_additional_kwargs_and_id():
    """Regression: gh #3132 — frontend ships uploaded-file metadata in
    additional_kwargs.files (and a client-side message id).  The gateway must
    not strip them before the graph runs, otherwise UploadsMiddleware reports
    "(empty)" for new uploads and the frontend message loses its file chip.
    """
    from langchain_core.messages import HumanMessage

    from app.gateway.services import normalize_input

    files = [{"filename": "a.csv", "size": 100, "path": "/mnt/user-data/uploads/a.csv", "status": "uploaded"}]
    result = normalize_input(
        {
            "messages": [
                {
                    "type": "human",
                    "id": "client-msg-1",
                    "name": "user-input",
                    "content": [{"type": "text", "text": "clean it"}],
                    "additional_kwargs": {"files": files, "custom": "keep-me"},
                }
            ]
        }
    )
    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert isinstance(msg, HumanMessage)
    assert msg.id == "client-msg-1"
    assert msg.name == "user-input"
    assert msg.content == [{"type": "text", "text": "clean it"}]
    assert msg.additional_kwargs == {"files": files, "custom": "keep-me"}


def test_normalize_input_passes_through_basemessage_instances():
    from langchain_core.messages import HumanMessage

    from app.gateway.services import normalize_input

    msg = HumanMessage(content="hello", id="m-1", additional_kwargs={"files": [{"filename": "x"}]})
    result = normalize_input({"messages": [msg]})
    assert result["messages"][0] is msg


def test_normalize_input_rejects_malformed_message_with_400():
    """Boundary validation: ``convert_to_messages`` raises ``ValueError`` when a
    message dict is missing ``role``/``type``/``content``.  ``normalize_input``
    runs inside the gateway HTTP boundary, so a malformed payload should surface
    as a 400 referencing the offending entry — not bubble up as a 500.

    Raised after the Copilot review on PR #3136.
    """
    import pytest
    from fastapi import HTTPException

    from app.gateway.services import normalize_input

    with pytest.raises(HTTPException) as excinfo:
        normalize_input({"messages": [{"role": "human", "content": "ok"}, {"oops": "no role here"}]})
    assert excinfo.value.status_code == 400
    assert "input.messages[1]" in excinfo.value.detail


def test_normalize_input_handles_non_human_roles():
    """The previous implementation collapsed every role to HumanMessage with a
    `# TODO: handle other message types` comment.  Resuming a thread with prior
    AI/tool messages would silently rewrite them as human turns — corrupting
    the conversation.  Use langchain's standard conversion so ai/system/tool
    roles round-trip correctly.
    """
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    from app.gateway.services import normalize_input

    result = normalize_input(
        {
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "ai", "content": "hi", "id": "ai-1"},
                {"role": "tool", "content": "result", "tool_call_id": "call-1"},
            ]
        }
    )
    types = [type(m) for m in result["messages"]]
    assert types == [SystemMessage, AIMessage, ToolMessage]
    assert result["messages"][1].id == "ai-1"
    assert result["messages"][2].tool_call_id == "call-1"


def test_build_run_config_basic():
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", None, None)
    assert config["configurable"]["thread_id"] == "thread-1"
    assert config["recursion_limit"] == 100


def test_build_run_config_recursion_limit_override():
    """The delegated remotion agent needs more than 100 steps to write + render a
    real Remotion video; callers must be able to raise the limit per-run."""
    from unittest.mock import patch

    from app.gateway.services import build_run_config

    with patch("app.gateway.services._resolve_max_recursion_limit", return_value=2500):
        config = build_run_config("thread-1", None, None, recursion_limit=300)
    assert config["recursion_limit"] == 300


def test_build_run_config_default_recursion_limit_unchanged():
    """Other callers (IM channels, etc.) must keep the default 100 when no override is given."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", None, None, assistant_id="course-video")
    assert config["recursion_limit"] == 100


def test_build_run_config_clamps_runaway_client_recursion_limit():
    """C-03: a client-supplied 3000 must be clamped to max_recursion_limit (2500)
    instead of being passed through verbatim (runaway super-steps / cost)."""
    from unittest.mock import patch

    from app.gateway.services import build_run_config

    with patch("app.gateway.services._resolve_max_recursion_limit", return_value=2500):
        config = build_run_config("thread-1", {"recursion_limit": 3000}, None)
    assert config["recursion_limit"] == 2500


def test_build_run_config_client_recursion_limit_at_max_passes_through():
    """The web frontend sends recursion_limit=2500 on every chat run; it must
    pass the clamp unchanged (behavior-preserving for the main chat path)."""
    from unittest.mock import patch

    from app.gateway.services import build_run_config

    with patch("app.gateway.services._resolve_max_recursion_limit", return_value=2500):
        config = build_run_config("thread-1", {"recursion_limit": 2500}, None)
    assert config["recursion_limit"] == 2500


def test_clamp_recursion_limit_rejects_invalid_values():
    from app.gateway.services import _clamp_recursion_limit

    # bool is an int subclass — must not slip through as True/False.
    assert _clamp_recursion_limit(True, 2500) == 100
    assert _clamp_recursion_limit(False, 2500) == 100
    assert _clamp_recursion_limit("300", 2500) == 100
    assert _clamp_recursion_limit(3.5, 2500) == 100
    assert _clamp_recursion_limit(0, 2500) == 100
    assert _clamp_recursion_limit(-5, 2500) == 100
    assert _clamp_recursion_limit(None, 2500) == 100


def test_clamp_recursion_limit_caps_valid_values():
    from app.gateway.services import _clamp_recursion_limit

    assert _clamp_recursion_limit(500, 2500) == 500
    assert _clamp_recursion_limit(2500, 2500) == 2500
    assert _clamp_recursion_limit(3000, 2500) == 2500


def test_default_max_recursion_limit_fallback_is_2500():
    """When AppConfig cannot be loaded, the gateway fallback ceiling must match
    the product-approved budget (2026-09-22: raised from 1000 to 2500)."""
    from app.gateway import services

    assert services._DEFAULT_MAX_RECURSION_LIMIT == 2500


def test_app_config_max_recursion_limit_defaults_to_2500():
    from deerflow.config.app_config import AppConfig

    assert AppConfig.model_fields["max_recursion_limit"].default == 2500


def test_build_run_config_with_overrides():
    from app.gateway.services import build_run_config

    config = build_run_config(
        "thread-1",
        {"configurable": {"model_name": "gpt-4"}, "tags": ["test"]},
        {"user": "alice"},
    )
    assert config["configurable"]["model_name"] == "gpt-4"
    assert config["tags"] == ["test"]
    assert config["metadata"]["user"] == "alice"


# ---------------------------------------------------------------------------
# Regression tests for issue #1644:
# assistant_id not mapped to agent_name → custom agent SOUL.md never loaded
# ---------------------------------------------------------------------------


def test_build_run_config_custom_agent_injects_agent_name():
    """Custom assistant_id must be forwarded as configurable['agent_name']."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", None, None, assistant_id="finalis")
    assert config["configurable"]["agent_name"] == "finalis"


def test_build_run_config_lead_agent_no_agent_name():
    """'lead_agent' assistant_id must NOT inject configurable['agent_name']."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", None, None, assistant_id="lead_agent")
    assert "agent_name" not in config["configurable"]


def test_build_run_config_none_assistant_id_no_agent_name():
    """None assistant_id must NOT inject configurable['agent_name']."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", None, None, assistant_id=None)
    assert "agent_name" not in config["configurable"]


def test_build_run_config_explicit_agent_name_not_overwritten():
    """An explicit configurable['agent_name'] in the request must take precedence."""
    from app.gateway.services import build_run_config

    config = build_run_config(
        "thread-1",
        {"configurable": {"agent_name": "explicit-agent"}},
        None,
        assistant_id="other-agent",
    )
    assert config["configurable"]["agent_name"] == "explicit-agent"


def test_build_run_config_context_custom_agent_injects_agent_name():
    """Custom assistant_id must be forwarded as context['agent_name'] in context mode."""
    from app.gateway.services import build_run_config

    config = build_run_config(
        "thread-1",
        {"context": {"model_name": "deepseek-v3"}},
        None,
        assistant_id="finalis",
    )

    assert config["context"]["agent_name"] == "finalis"
    assert "configurable" not in config


def test_resolve_agent_factory_returns_make_lead_agent():
    """resolve_agent_factory always returns make_lead_agent regardless of assistant_id."""
    from app.gateway.services import resolve_agent_factory
    from deerflow.agents.lead_agent.agent import make_lead_agent

    assert resolve_agent_factory(None) is make_lead_agent
    assert resolve_agent_factory("lead_agent") is make_lead_agent
    assert resolve_agent_factory("finalis") is make_lead_agent
    assert resolve_agent_factory("custom-agent-123") is make_lead_agent


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Regression tests for issue #1699:
# context field in langgraph-compat requests not merged into configurable
# ---------------------------------------------------------------------------


def test_run_create_request_accepts_context():
    """RunCreateRequest must accept the ``context`` field without dropping it."""
    from app.gateway.routers.thread_runs import RunCreateRequest

    body = RunCreateRequest(
        input={"messages": [{"role": "user", "content": "hi"}]},
        context={
            "model_name": "deepseek-v3",
            "thinking_enabled": True,
            "is_plan_mode": True,
            "subagent_enabled": True,
            "thread_id": "some-thread-id",
        },
    )
    assert body.context is not None
    assert body.context["model_name"] == "deepseek-v3"
    assert body.context["is_plan_mode"] is True
    assert body.context["subagent_enabled"] is True


def test_run_create_request_context_defaults_to_none():
    """RunCreateRequest without context should default to None (backward compat)."""
    from app.gateway.routers.thread_runs import RunCreateRequest

    body = RunCreateRequest(input=None)
    assert body.context is None


def _capture_start_run_config(monkeypatch):
    """Wire start_run's dependencies with fakes; return (record, captured).

    The fake run_agent records the exact ``config`` dict start_run hands to the
    agent run, so tests assert on the real context-merge output instead of
    simulating the merge in the test body.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    import app.gateway.services as services

    record = SimpleNamespace(run_id="r1", status="pending")
    run_mgr = MagicMock()
    run_mgr.create_or_reject = AsyncMock(return_value=record)
    run_mgr.set_status = AsyncMock()

    async def _thread_get(thread_id, user_id=None):
        return {"user_id": "u-alice"}  # existing row -> update_status branch

    thread_store = MagicMock()
    thread_store.get = _thread_get
    thread_store.update_status = AsyncMock()

    captured: dict = {}

    def _fake_run_agent(*args, **kwargs):
        captured["config"] = kwargs["config"]

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(services, "get_stream_bridge", lambda request: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda request: run_mgr)
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=thread_store))
    monkeypatch.setattr(services, "run_agent", _fake_run_agent)
    return record, captured


def _run_create_body(**overrides):
    """A start_run-compatible body namespace with safe defaults."""
    from types import SimpleNamespace

    body = SimpleNamespace(
        assistant_id=None,
        on_disconnect="cancel",
        metadata={},
        input={"messages": []},
        config=None,
        multitask_strategy=None,
        stream_mode=None,
        stream_subgraphs=False,
        on_completion=None,
        interrupt_before=None,
        interrupt_after=None,
        context=None,
    )
    for key, value in overrides.items():
        setattr(body, key, value)
    return body


def _start_run_request():
    from types import SimpleNamespace

    return SimpleNamespace(state=SimpleNamespace(), cookies={})


@pytest.mark.asyncio
async def test_context_merges_into_configurable(monkeypatch):
    """start_run merges allowlisted body.context keys into config['configurable'].

    Drives the real start_run path (services.py): the merge runs over the
    production allowlist, and the resulting config is captured at the point
    run_agent receives it.
    """
    import app.gateway.services as services

    record, captured = _capture_start_run_config(monkeypatch)
    # agent_name 会过 start_run 末尾的 SEC-H8 可见性门（_gate_agent_runnable →
    # load_agent_config）。本测试对象是 context→configurable 的白名单 merge，
    # 门控有专属测试——桩掉注册表查找放行任意名，保持 agent_name 值的区分度。
    monkeypatch.setattr("deerflow.config.agents_config.load_agent_config", lambda name: None)

    body = _run_create_body(
        context={
            "model_name": "deepseek-v3",
            "mode": "ultra",
            "reasoning_effort": "high",
            "thinking_enabled": True,
            "is_plan_mode": True,
            "subagent_enabled": True,
            "max_concurrent_subagents": 5,
            # agent_name / is_bootstrap 是 2026-10-05 复查发现旧测试本地副本缺失
            # 的两个白名单键（白名单漂移过而旧测试照绿）——钉进断言防再漂移。
            "agent_name": "custom-agent-x",
            "is_bootstrap": True,
            "thread_id": "not-allowlisted",
            "unknown_key": "not-allowlisted",
        }
    )
    result = await services.start_run(body, "thread-1", _start_run_request())
    assert result is record
    await record.task  # let the stubbed run_agent finish

    configurable = captured["config"]["configurable"]
    assert configurable["model_name"] == "deepseek-v3"
    assert configurable["thinking_enabled"] is True
    assert configurable["is_plan_mode"] is True
    assert configurable["subagent_enabled"] is True
    assert configurable["max_concurrent_subagents"] == 5
    assert configurable["reasoning_effort"] == "high"
    assert configurable["mode"] == "ultra"
    assert configurable["agent_name"] == "custom-agent-x"
    assert configurable["is_bootstrap"] is True
    # thread_id is not an allowlisted merge key; the path param value wins
    assert configurable["thread_id"] == "thread-1"
    # Non-allowlisted context keys are not forwarded
    assert "unknown_key" not in configurable


@pytest.mark.asyncio
async def test_context_does_not_override_existing_configurable(monkeypatch):
    """Values already in config.configurable must NOT be overridden by body.context."""
    import app.gateway.services as services

    record, captured = _capture_start_run_config(monkeypatch)

    body = _run_create_body(
        config={"configurable": {"model_name": "gpt-4", "is_plan_mode": False}},
        context={
            "model_name": "deepseek-v3",
            "is_plan_mode": True,
            "subagent_enabled": True,
        },
    )
    result = await services.start_run(body, "thread-1", _start_run_request())
    assert result is record
    await record.task

    configurable = captured["config"]["configurable"]
    # Existing values must NOT be overridden
    assert configurable["model_name"] == "gpt-4"
    assert configurable["is_plan_mode"] is False
    # New allowlisted values should be added
    assert configurable["subagent_enabled"] is True


# ---------------------------------------------------------------------------
# build_run_config — context / configurable precedence (LangGraph >= 0.6.0)
# ---------------------------------------------------------------------------


def test_build_run_config_with_context():
    """When caller sends 'context', prefer it over 'configurable'."""
    from app.gateway.services import build_run_config

    config = build_run_config(
        "thread-1",
        {"context": {"user_id": "u-42", "thread_id": "thread-1"}},
        None,
    )
    assert "context" in config
    assert config["context"]["user_id"] == "u-42"
    assert "configurable" not in config
    assert config["recursion_limit"] == 100


def test_build_run_config_null_context_becomes_empty_context():
    """When caller sends context=null, treat it as an empty context object."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", {"context": None}, None)

    assert config["context"] == {}
    assert "configurable" not in config


def test_build_run_config_rejects_non_mapping_context():
    """When caller sends a non-object context, raise a clear error instead of a TypeError."""
    import pytest

    from app.gateway.services import build_run_config

    with pytest.raises(ValueError, match="context"):
        build_run_config("thread-1", {"context": "bad-context"}, None)


def test_build_run_config_null_context_custom_agent_injects_agent_name():
    """Custom assistant_id can still be injected when context=null starts context mode."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-1", {"context": None}, None, assistant_id="finalis")

    assert config["context"] == {"agent_name": "finalis"}
    assert "configurable" not in config


def test_build_run_config_context_plus_configurable_warns(caplog):
    """When caller sends both 'context' and 'configurable', prefer 'context' and log a warning."""
    import logging

    from app.gateway.services import build_run_config

    with caplog.at_level(logging.WARNING, logger="app.gateway.services"):
        config = build_run_config(
            "thread-1",
            {
                "context": {"user_id": "u-42"},
                "configurable": {"model_name": "gpt-4"},
            },
            None,
        )
    assert "context" in config
    assert config["context"]["user_id"] == "u-42"
    assert "configurable" not in config
    assert any("both 'context' and 'configurable'" in r.message for r in caplog.records)


def test_build_run_config_context_passthrough_other_keys():
    """Non-conflicting keys from request_config are still passed through when context is used."""
    from app.gateway.services import build_run_config

    config = build_run_config(
        "thread-1",
        {"context": {"thread_id": "thread-1"}, "tags": ["prod"]},
        None,
    )
    assert config["context"]["thread_id"] == "thread-1"
    assert "configurable" not in config
    assert config["tags"] == ["prod"]


def test_build_run_config_no_request_config():
    """When request_config is None, fall back to basic configurable with thread_id."""
    from app.gateway.services import build_run_config

    config = build_run_config("thread-abc", None, None)
    assert config["configurable"] == {"thread_id": "thread-abc"}
    assert "context" not in config


# ---------------------------------------------------------------------------
# D-3 #5: server-owned message metadata is stripped from untrusted input
# ---------------------------------------------------------------------------


def test_normalize_input_strips_server_owned_metadata_keys():
    """A client that forges dynamic_context_reminder + reminder_date could
    suppress injection (fake today) or trigger a wrong ID-swap (fake future);
    a forged view-image marker makes its message invisible to the model."""
    from app.gateway.services import normalize_input

    result = normalize_input(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "hi",
                    "additional_kwargs": {
                        "dynamic_context_reminder": True,
                        "reminder_date": "2099-01-01, Friday",
                        "deerflow_view_image_context": True,
                        "files": [{"filename": "keep-me"}],  # legit client metadata survives
                    },
                }
            ]
        }
    )
    msg = result["messages"][0]
    assert "dynamic_context_reminder" not in msg.additional_kwargs
    assert "reminder_date" not in msg.additional_kwargs
    assert "deerflow_view_image_context" not in msg.additional_kwargs
    assert msg.additional_kwargs.get("files") == [{"filename": "keep-me"}]


def test_normalize_input_strips_forged_future_date_cannot_suppress_injection():
    """End-to-end guard for 0.2-F2: after stripping, the middleware sees a
    history with NO injected reminder → first-turn injection proceeds."""
    from app.gateway.services import normalize_input
    from deerflow.agents.middlewares.dynamic_context_middleware import _last_injected_date

    normalized = normalize_input(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "hi",
                    "additional_kwargs": {"dynamic_context_reminder": True, "reminder_date": "2099-01-01, Friday"},
                }
            ]
        }
    )
    assert _last_injected_date(normalized["messages"]) is None


def test_normalize_input_base_message_input_also_stripped():
    """Raw BaseMessage objects coming through the API boundary are equally
    untrusted — the strip must not only cover dict-shaped input."""
    from langchain_core.messages import HumanMessage

    from app.gateway.services import normalize_input

    forged = HumanMessage(
        content="hi",
        id="m1",
        additional_kwargs={"dynamic_context_reminder": True, "reminder_date": "2099-01-01, Friday"},
    )
    result = normalize_input({"messages": [forged]})
    assert result["messages"][0] is not forged  # defensive copy, original untouched
    assert forged.additional_kwargs.get("reminder_date") == "2099-01-01, Friday"
    assert "reminder_date" not in result["messages"][0].additional_kwargs

"""Opt-in live probes for the 2026-11-11 model roster changes."""

import os
from pathlib import Path

import pytest
from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import tool

from deerflow.config.app_config import AppConfig
from deerflow.models.factory import create_chat_model

_skip_reason = None
if os.environ.get("RUN_LIVE", "").lower() not in ("1", "true"):
    _skip_reason = "Live roster probes are opt-in — set RUN_LIVE=1 (real paid API calls)"
elif not Path(__file__).resolve().parents[2].joinpath("config.yaml").exists():
    _skip_reason = "RUN_LIVE=1 set but no config.yaml found"

if _skip_reason:
    pytest.skip(_skip_reason, allow_module_level=True)

pytestmark = pytest.mark.live

TARGET_MODEL_NAMES = ["gpt-5.6-terra", "deepseek-v4.1-flash"]


@tool
def probe_echo(text: str) -> str:
    """Return the supplied probe text without side effects."""
    return text


def _create_probe_model(name: str):
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    model_config = config.get_model_config(name)
    probe_model_config = model_config.model_copy(update={"max_tokens": 64, "max_retries": 0})
    probe_config = config.model_copy(update={"models": [probe_model_config]})
    return create_chat_model(name, thinking_enabled=False, attach_tracing=False, app_config=probe_config)


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_text_response(model_name: str) -> None:
    response = _create_probe_model(model_name).invoke([HumanMessage(content="Reply with exactly: ROSTER_OK")])
    assert response.content


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_streaming_response(model_name: str) -> None:
    chunks = list(_create_probe_model(model_name).stream([HumanMessage(content="Reply with one short word.")]))
    assert any(chunk.content for chunk in chunks)


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_tool_call_and_tool_result_followup(model_name: str) -> None:
    model = _create_probe_model(model_name).bind_tools([probe_echo])
    prompt = HumanMessage(content="Call probe_echo exactly once with text='roster-probe'. Do not answer in plain text.")
    tool_response = model.invoke([prompt])

    assert tool_response.tool_calls
    call = tool_response.tool_calls[0]
    assert call["name"] == "probe_echo"
    assert call["args"] == {"text": "roster-probe"}

    followup = model.invoke([prompt, tool_response, ToolMessage(content="roster-probe", tool_call_id=call["id"])])
    assert followup.content

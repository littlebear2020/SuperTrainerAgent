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


def _create_probe_model(name: str, *, model_update: dict | None = None, thinking_enabled: bool = False):
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    model_config = config.get_model_config(name)
    probe_model_config = model_config.model_copy(update={"max_retries": 0, **(model_update or {})})
    probe_config = config.model_copy(update={"models": [probe_model_config]})
    return create_chat_model(name, thinking_enabled=thinking_enabled, attach_tracing=False, app_config=probe_config)


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

    followup = model.invoke(
        [
            prompt,
            tool_response,
            ToolMessage(content="roster-probe", tool_call_id=call["id"]),
            HumanMessage(content="The tool result has been received. Reply with exactly: TOOL_FOLLOWUP_OK"),
        ]
    )
    assert followup.content


def _red_image_data_url() -> str:
    import base64
    import struct
    import zlib

    width = height = 256
    raw = b"".join(b"\x00" + b"\xdc\x1e\x1e" * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def test_live_terra_vision_identifies_red_image() -> None:
    response = _create_probe_model("gpt-5.6-terra").invoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "What is the dominant color of this image? Answer with one word."},
                    {"type": "image_url", "image_url": {"url": _red_image_data_url()}},
                ]
            )
        ]
    )
    answer = str(response.content).lower()
    assert "red" in answer or "红" in answer


def test_live_terra_thinking_mode_contract() -> None:
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    if not config.get_model_config("gpt-5.6-terra").supports_thinking:
        with pytest.raises(ValueError, match="does not support thinking"):
            _create_probe_model(
                "gpt-5.6-terra",
                model_update={"when_thinking_enabled": {"extra_body": {"thinking": {"type": "enabled"}}}},
                thinking_enabled=True,
            )
        return

    response = _create_probe_model("gpt-5.6-terra", thinking_enabled=True).invoke([HumanMessage(content="Think briefly, then say OK.")])
    reasoning_fields = [response.additional_kwargs.get(key) for key in ("reasoning", "reasoning_content", "thinking")]
    has_reasoning_content = any(bool(value) for value in reasoning_fields)
    reasoning_tokens = (response.usage_metadata or {}).get("output_token_details", {}).get("reasoning", 0)
    assert has_reasoning_content or reasoning_tokens > 0

"""Opt-in live probes for the 2026-11-11 model roster changes."""

import os
import time
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

TARGET_MODEL_NAMES = ["gpt-5.6-terra", "deepseek-v4.1-flash", "glm-5.4"]
GLM_5_4 = "glm-5.4"


@tool
def probe_echo(text: str) -> str:
    """Return the supplied probe text without side effects."""
    return text


def _create_probe_model(
    name: str,
    *,
    model_update: dict | None = None,
    thinking_enabled: bool = False,
    reasoning_effort: str | None = None,
):
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    model_config = config.get_model_config(name)
    probe_model_config = model_config.model_copy(update={"max_retries": 0, **(model_update or {})})
    probe_config = config.model_copy(update={"models": [probe_model_config]})
    extra = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    return create_chat_model(name, thinking_enabled=thinking_enabled, attach_tracing=False, app_config=probe_config, **extra)


def _probe_model_for(name: str, *, model_update: dict | None = None):
    """glm-5.4 按产品路径探测（标准/Ultra 档：开思考 + reasoning_effort=high）；其他模型保持原样（关思考）。

    共享用例原本一律关思考：glm-5.4 若也关思考，关思考参数被拒会被误判成工具调用不通过。
    """
    if name != GLM_5_4:
        return _create_probe_model(name, model_update=model_update)
    config = AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))
    thinking = bool(config.get_model_config(name).supports_thinking)
    return _create_probe_model(name, model_update=model_update, thinking_enabled=thinking, reasoning_effort="high")


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_text_response(model_name: str) -> None:
    response = _probe_model_for(model_name).invoke([HumanMessage(content="Reply with exactly: ROSTER_OK")])
    assert response.content


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_streaming_response(model_name: str) -> None:
    started = time.monotonic()
    first_token_s: float | None = None
    chunks = []
    for chunk in _probe_model_for(model_name).stream([HumanMessage(content="Reply with one short word.")]):
        chunks.append(chunk)
        if first_token_s is None and chunk.content:
            first_token_s = time.monotonic() - started
    print(f"[first-token] {model_name}: {first_token_s}")
    assert any(chunk.content for chunk in chunks)


@pytest.mark.parametrize("model_name", TARGET_MODEL_NAMES)
def test_live_tool_call_and_tool_result_followup(model_name: str) -> None:
    model = _probe_model_for(model_name).bind_tools([probe_echo])
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


# ── glm-5.4（2026-11-26）：标准 + Ultra，开思考 + reasoning_effort=high ─────────────────


def _reasoning_signal(response) -> tuple[bool, int]:
    reasoning_fields = [response.additional_kwargs.get(key) for key in ("reasoning", "reasoning_content", "thinking")]
    reasoning_tokens = (response.usage_metadata or {}).get("output_token_details", {}).get("reasoning", 0) or 0
    return any(bool(value) for value in reasoning_fields), int(reasoning_tokens)


def test_live_glm_5_4_thinking_contract() -> None:
    """2 次调用：开思考 + high 与关思考各一次，把实测契约打印出来（能力位据此决定）。"""
    outcome: dict[str, dict] = {}

    try:
        response = _create_probe_model(GLM_5_4, thinking_enabled=True, reasoning_effort="high").invoke([HumanMessage(content="Think briefly, then say OK.")])
        has_reasoning, reasoning_tokens = _reasoning_signal(response)
        outcome["thinking_on"] = {"error": None, "answer_non_empty": bool(response.content), "reasoning_content": has_reasoning, "reasoning_tokens": reasoning_tokens}
    except Exception as exc:  # noqa: BLE001 — 探针：记录报错形态即可
        outcome["thinking_on"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    try:
        response = _create_probe_model(GLM_5_4, thinking_enabled=False).invoke([HumanMessage(content="Reply with exactly: OK")])
        has_reasoning, reasoning_tokens = _reasoning_signal(response)
        outcome["thinking_off"] = {"error": None, "answer_non_empty": bool(response.content), "reasoning_content": has_reasoning, "reasoning_tokens": reasoning_tokens}
    except Exception as exc:  # noqa: BLE001
        outcome["thinking_off"] = {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}

    print(f"[glm-5.4 thinking contract] {outcome}")
    on = outcome["thinking_on"]
    assert on["error"] is None
    assert on["answer_non_empty"]
    assert on["reasoning_content"] or on["reasoning_tokens"] > 0
    # 2026-11-26 实测：该模型始终思考，关思考请求被 360 以 400 拒绝（「该模型始终思考，不支持关闭思考；
    # 请使用 low、high 或 max」）。只影响以关思考调用的内部请求（目前仅建议追问，失败时返回空列表）；
    # 标准/Ultra 档开思考 + reasoning_effort=high，不受影响。
    off = outcome["thinking_off"]
    assert off["error"] is not None
    assert "BadRequestError" in off["error"]
    assert "400" in off["error"]


def test_live_glm_5_4_vision_identifies_red_image() -> None:
    response = _probe_model_for(GLM_5_4).invoke(
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


def test_live_glm_5_4_first_token_latency() -> None:
    started = time.monotonic()
    first_token_s: float | None = None
    for chunk in _probe_model_for(GLM_5_4).stream([HumanMessage(content="Reply with one short word.")]):
        if chunk.content:
            first_token_s = time.monotonic() - started
            break
    print(f"[first-token] glm-5.4 latency probe: {first_token_s}")
    assert first_token_s is not None


def test_live_glm_5_4_long_context_needle() -> None:
    filler = "这是一段与任务无关的填充文本，用来撑长上下文。" * 2000  # ≈ 5 万字符
    middle = len(filler) // 2
    needle = "口令是 ZEBRA-4417-OMEGA。"
    document = filler[:middle] + needle + filler[middle:]
    response = _probe_model_for(GLM_5_4).invoke([HumanMessage(content=f"{document}\n\n上面的文本里藏着一句口令。请原样写出口令本身（形如 XXXX-0000-XXXX），不要写别的。")])
    assert "ZEBRA-4417-OMEGA" in str(response.content)

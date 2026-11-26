"""Opt-in live evaluation (paid): does Ultra delegate independent workstreams? (BUG-02, 2026-11-26)

Each trial builds a real lead-style agent (real prompt, real model, real TodoMiddleware) whose
tools are stubs, so no subagent actually runs. A trial is capped at ``_MAX_MODEL_CALLS`` model
calls. Results are appended to the JSON file named by ``ULTRA_EVAL_OUT`` when it is set.
"""

import json
import os
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import StructuredTool

from deerflow.agents.lead_agent.agent import _create_todo_list_middleware
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.thread_state import ThreadState
from deerflow.config.app_config import AppConfig
from deerflow.models.factory import create_chat_model
from deerflow.tools.tools import get_available_tools

_skip_reason = None
if os.environ.get("RUN_LIVE", "").lower() not in ("1", "true"):
    _skip_reason = "Live Ultra delegation eval is opt-in — set RUN_LIVE=1 (real paid API calls)"
elif not Path(__file__).resolve().parents[2].joinpath("config.yaml").exists():
    _skip_reason = "RUN_LIVE=1 set but no config.yaml found"

if _skip_reason:
    pytest.skip(_skip_reason, allow_module_level=True)

pytestmark = pytest.mark.live

_MAX_MODEL_CALLS = 6
_BRANCHING_PROMPT = "请分别调研阿里云、腾讯云、华为云三家对象存储（OSS / COS / OBS）的标准存储单价与免费额度，三家各自独立整理成一段说明，最后汇总成一张对比表。"
_SIMPLE_PROMPT = "把这句话改写得更正式：明天的会改到下午三点。"


class _TrialBudgetExceeded(Exception):
    pass


class _CallBudget(BaseCallbackHandler):
    raise_error = True

    def __init__(self) -> None:
        self.calls = 0

    def on_chat_model_start(self, *args, **kwargs) -> None:
        self.calls += 1
        if self.calls > _MAX_MODEL_CALLS:
            raise _TrialBudgetExceeded


def _config() -> AppConfig:
    return AppConfig.from_file(str(Path(__file__).resolve().parents[2] / "config.yaml"))


def _stub_tools(cfg: AppConfig, model_name: str) -> list[StructuredTool]:
    real = get_available_tools(include_mcp=False, model_name=model_name, subagent_enabled=True, app_config=cfg)
    stubs = []
    for tool in real:
        reply = "子任务已完成（评测桩）" if tool.name == "task" else "（评测桩）已执行"
        stubs.append(
            StructuredTool.from_function(
                func=lambda _reply=reply, **_kwargs: _reply,
                name=tool.name,
                description=tool.description,
                args_schema=tool.tool_call_schema,
            )
        )
    return stubs


def _run_trial(cfg: AppConfig, model_name: str, prompt: str) -> list[int]:
    """Return the number of ``task`` calls in each AI message of one trial."""
    model_config = cfg.get_model_config(model_name)
    model = create_chat_model(
        model_name,
        thinking_enabled=model_config.supports_thinking is True,
        reasoning_effort="high",
        app_config=cfg,
        attach_tracing=False,
    )
    agent = create_agent(
        model=model,
        tools=_stub_tools(cfg, model_name),
        middleware=[_create_todo_list_middleware(True)],
        system_prompt=apply_prompt_template(subagent_enabled=True, max_concurrent_subagents=3, app_config=cfg),
        state_schema=ThreadState,
    )
    last: dict = {}
    try:
        for snapshot in agent.stream(
            {"messages": [("user", prompt)]},
            {"callbacks": [_CallBudget()], "recursion_limit": 100},
            stream_mode="values",
        ):
            last = snapshot
    except _TrialBudgetExceeded:
        pass
    counts = []
    for message in last.get("messages", []):
        if getattr(message, "type", None) == "ai":
            counts.append(sum(1 for call in (message.tool_calls or []) if call.get("name") == "task"))
    return counts


def _record(key: str, value: object) -> None:
    out = os.environ.get("ULTRA_EVAL_OUT")
    if not out:
        return
    path = Path(out)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _evaluate(cfg: AppConfig, model_name: str, branching_trials: int, simple_trials: int) -> dict:
    branching = [_run_trial(cfg, model_name, _BRANCHING_PROMPT) for _ in range(branching_trials)]
    simple = [_run_trial(cfg, model_name, _SIMPLE_PROMPT) for _ in range(simple_trials)]
    return {
        "branching_task_counts_per_reply": branching,
        "branching_parallel_trials": sum(1 for trial in branching if any(n >= 2 for n in trial)),
        "simple_task_total": sum(sum(trial) for trial in simple),
    }


def test_default_ultra_model_delegates_independent_workstreams() -> None:
    cfg = _config()
    name = cfg.mode_defaults["ultra"]
    result = _evaluate(cfg, name, 3, 1)
    _record(name, result)
    print(f"[ultra-eval] {name}: {result}")
    assert result["branching_parallel_trials"] >= 2, result
    assert result["simple_task_total"] == 0, result


def test_other_ultra_models_delegation_rates_recorded() -> None:
    cfg = _config()
    default = cfg.mode_defaults["ultra"]
    for model in cfg.models:
        if "ultra" not in (model.modes or []) or model.name == default:
            continue
        result = _evaluate(cfg, model.name, 3, 1)
        _record(model.name, result)
        print(f"[ultra-eval] {model.name}: {result}")

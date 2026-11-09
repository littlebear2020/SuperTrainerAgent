"""R6（2026-09-24 批 1，W6 序列化警告）：全 harness 包 create_agent 的 context_schema 守护.

中间件（slow_llm_call / terminal_response 等 12 个写入点）会往 ``runtime.context``
塞 ``slow_llm_call_count`` / ``empty_fallback_count`` / ``__run_journal``（RunJournal
对象）等键。``create_agent`` 不传 ``context_schema`` 时 context 类型标注为 None，
langgraph 序列化即告警 ``PydanticSerializationUnexpectedValue(field_name='context')``。

修复（评审 Q8 定稿）：**5 处** create_agent 调用点（lead_agent/agent.py ×2、
agents/factory.py、client.py、subagents/executor.py）显式传
``context_schema=dict[str, Any]``——开放声明，不做键枚举（防中间件新增键告警复发）。

本测试用 AST 扫描 ``packages/harness`` 全部 ``create_agent(`` 调用，**任何**一处
（含未来新增）漏传 ``context_schema`` 即红。
"""

from __future__ import annotations

import ast
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow"

# 排除测试自身与 docs；只扫 .py
_EXPECTED_SITES = {
    "agents/lead_agent/agent.py": 2,
    "agents/factory.py": 1,
    "client.py": 1,
    "subagents/executor.py": 1,
}


def _create_agent_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name == "create_agent":
                calls.append(node)
    return calls


def test_every_create_agent_call_passes_context_schema():
    found: dict[str, int] = {}
    missing: list[str] = []

    for py in sorted(HARNESS_DIR.rglob("*.py")):
        rel = str(py.relative_to(HARNESS_DIR))
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for call in _create_agent_calls(tree):
            found[rel] = found.get(rel, 0) + 1
            # **kwargs 展开的调用点（client.py）静态不可见——由
            # test_context_schema_uses_open_dict_typing 的形态断言覆盖
            if any(k.arg is None for k in call.keywords):
                continue
            keywords = {k.arg for k in call.keywords if k.arg}
            if "context_schema" not in keywords:
                missing.append(f"{rel}:{call.lineno}")

    assert not missing, f"create_agent 调用点漏传 context_schema（R6/W6：无标注时 runtime.context 序列化告警）：{missing}。全部调用点应传 context_schema=dict[str, Any]"
    # 已知 5 处锚定——新增调用点自动被上方断言覆盖，这里的数量锚防意外删除
    assert found == _EXPECTED_SITES, f"create_agent 调用点分布变化：{found}（预期 {_EXPECTED_SITES}）——新增/迁移调用点请同步更新本锚点并补 context_schema"


def test_context_schema_uses_open_dict_typing():
    """Q8：开放声明形态——必须是 dict[str, Any]（或等价），不得做键枚举。"""
    expected_snippets = {
        "agents/lead_agent/agent.py": 2,
        "agents/factory.py": 1,
        "client.py": 1,
        "subagents/executor.py": 1,
    }
    total = 0
    for rel, want in expected_snippets.items():
        src = (HARNESS_DIR / rel).read_text(encoding="utf-8")
        hits = src.count("context_schema=dict[str, Any]") + src.count('"context_schema": dict[str, Any]')
        assert hits >= want, f"{rel} 中开放形态 context_schema 声明不足：{hits} < {want}"
        total += hits
    assert total == 5

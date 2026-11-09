"""R10（Q6，2026-09-24 批 2）：token_budget_warn 走 custom 事件通道守护.

修前：worker 直接 ``bridge.publish(run_id, "token_budget_warn", {...})`` ——独立
SSE 事件名没有任何消费方，前端 onCustomEvent（只认 "custom"）永远收不到，告警
不可达。修后：与中间件 stream-writer 同一通路（SSE 事件名 "custom" + 载荷首键
``type: "token_budget_warn"``），前端 hooks 的 toastableCustomEventMessage 白名单
（frontend tests/unit/core/threads/custom-event-toast.test.ts）消费为 toast。

本测试用 AST 钉死 worker 的 publish 接线形态：回退成独立事件名即红。
"""

from __future__ import annotations

import ast
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "packages" / "harness" / "deerflow" / "runtime" / "runs" / "worker.py"


def _budget_warn_publishes() -> list[tuple[ast.Constant, ast.Call]]:
    """返回 worker 里载荷含 token_budget_warn 的 (事件名常量, publish 调用) 列表。"""
    tree = ast.parse(WORKER.read_text(encoding="utf-8"))
    hits: list[tuple[ast.Constant, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        func = getattr(call.func, "attr", None)
        if func != "publish" or len(call.args) < 3:
            continue
        payload = call.args[2]
        if not (isinstance(payload, ast.Dict)):
            continue
        type_value = None
        for k, v in zip(payload.keys, payload.values):
            if isinstance(k, ast.Constant) and k.value == "type" and isinstance(v, ast.Constant):
                type_value = v.value
        if type_value == "token_budget_warn":
            event_name = call.args[1]
            hits.append((event_name if isinstance(event_name, ast.Constant) else None, call))
    return hits


def test_token_budget_warn_published_on_custom_channel():
    publishes = _budget_warn_publishes()
    assert publishes, "worker.py 里找不到 token_budget_warn 的 bridge.publish——D3 告警通道被移除了？"
    for event_name, _ in publishes:
        assert event_name is not None and event_name.value == "custom", (
            f"token_budget_warn 必须以 SSE 事件名 'custom' 发布（载荷首键 type=token_budget_warn），现值：{getattr(event_name, 'value', None)!r}——独立事件名前端 onCustomEvent 收不到（R10/Q6）"
        )


def test_budget_warn_payload_carries_message_string():
    """toast 通路要求载荷带非空 message 字符串（toastableCustomEventMessage 校验）。"""
    publishes = _budget_warn_publishes()
    assert publishes
    _, call = publishes[0]
    payload = call.args[2]
    keys = {k.value for k in payload.keys if isinstance(k, ast.Constant)}
    assert {"type", "message"} <= keys, f"载荷缺 type/message 键：{sorted(x for x in keys if x)}"

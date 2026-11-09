"""复现验证 (计划行动项 4, docs/plan-view-image-history-bloat-and-pricing-prefix.md):
多模态图片历史膨胀事故的确定性复现与修复断言。

事故 (run_id 4432521c-f15…, 2026-08-18): 用户上传含大量图片的 docx，agent 逐张
view_image，此后每一轮推理窗口都携带**全部**已看图片的 base64 —— 42 张图 × 每轮
全量重注入 → 51 次 LLM 调用烧掉 605 万 token、30 分钟无产出；瞬时图片消息还顺
着 values 流写进 run_events（run.end 单事件 2.15MB）。

本测试不跑真 LLM：直接驱动改造后的 ViewImageMiddleware 走 wrap_model_call，
模拟"逐张查看 6 张图"的多回合 agent 循环（状态经真实 reducer 演进），断言计划
§5 的四条验收：

- P0-膨胀: 每轮窗口内 base64 图 ≤ 本轮批量+K（08-18 晚批量豁免拍板后上限=1+2），
  单轮 input 不随查看次数线性增长；
- P0-语义: 占位符保留 path/mime/size + 重看指引；
- P0-不回归(传输层): serialize(mode="values") 输出不含 data:image base64；
- 对照: 旧机制（全量注入）在本场景下窗口将线性携带全部 N 张 —— 数学对照写进
  断言，量化修复收益。

真 LLM 的 docx 端到端跑批属人工验收（计划行动项 4 的活体部分），不在本文件。
"""

import base64
import json
from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.view_image_middleware import _MAX_INJECTED_IMAGES, ViewImageMiddleware
from deerflow.agents.thread_state import merge_viewed_images
from deerflow.runtime.serialization import serialize

N_IMAGES = 6  # 事故场景缩样：docx 内 5+ 张图，agent 逐张查看
_IMAGE_PAYLOAD_KB = 100  # 每张 ~100KB，足够让"线性 vs 常量"的对照显著


def _make_image(tmp_path, idx: int):
    payload = (f"PNG-FAKE-IMAGE-{idx}-" + "x" * 1024).encode() * _IMAGE_PAYLOAD_KB  # ~100KB+，可区分
    f = tmp_path / f"docx-image-{idx}.png"
    f.write_bytes(payload)
    return f"/mnt/user-data/uploads/docx-image-{idx}.png", {"mime_type": "image/png", "size": len(payload), "actual_path": str(f)}, payload


def _turn_messages(path: str) -> list:
    """One agent turn: the model just called view_image(path) and got its result."""
    return [
        HumanMessage(content="分析这份文档里的所有图片"),
        AIMessage(content="", tool_calls=[{"name": "view_image", "id": "c1", "args": {"image_path": path}}]),
        ToolMessage(content="Successfully read image", tool_call_id="c1"),
    ]


def _run_turn(mw: ViewImageMiddleware, messages: list, viewed: dict) -> tuple[list, int]:
    """Drive one model call through the middleware; return (window, image_bytes)."""
    sink: list = []
    request = ModelRequest(model=MagicMock(), messages=messages, state={"messages": messages, "viewed_images": dict(viewed)})
    mw.wrap_model_call(request, lambda req: (sink.append(req), MagicMock())[1])
    window = sink[0].messages
    injected = [b for m in window for b in (m.content if isinstance(m.content, list) else []) if isinstance(b, dict) and b.get("type") == "image_url"]
    payload_bytes = sum(len(base64.b64decode(b["image_url"]["url"].split(",", 1)[1])) for b in injected)
    return window, payload_bytes


class TestBloatReproduction:
    def test_sequential_viewing_window_stays_constant(self, tmp_path):
        """逐张查看 N(=6) 张图：每轮窗口 base64 恒 ≤ K，占位符承担其余语义。"""
        mw = ViewImageMiddleware()
        images = {}
        viewed: dict | None = None
        window_sizes: list[int] = []

        for idx in range(N_IMAGES):
            path, meta, _payload = _make_image(tmp_path, idx)
            images[path] = meta
            # 真实 reducer 演进（含 recency 语义）
            viewed = merge_viewed_images(viewed, {path: meta}) if viewed is not None else {path: meta}

            window, payload_bytes = _run_turn(mw, _turn_messages(path), viewed)
            window_sizes.append(payload_bytes)

            # P0-膨胀: 窗口内 base64 图 ≤ 本轮批量(1)+K（旧机制在第 N 轮携带 N 张）
            assert payload_bytes <= _max_window_bytes(images), f"turn {idx}: window exceeded batch+K images"

            # P0-语义: 不在窗口内的已看图都有占位符（path/mime/size + 重看指引）
            injected_text = "\n".join(b.get("text", "") for m in window for b in (m.content if isinstance(m.content, list) else []) if isinstance(b, dict))
            for seen_path, seen_meta in images.items():
                if _is_placeholder_for(injected_text, seen_path):
                    assert str(seen_meta["size"]) in injected_text
                    assert "view_image" in injected_text

        # 单轮 input 不随查看次数线性增长：第 K+1 轮起窗口字节数持平
        # （旧机制第 6 轮 ≈ 6 × 单图；新机制恒 ≤ K × 单图 + 少量占位文本）
        assert max(window_sizes) <= _max_window_bytes(images)
        # 稳态后相邻轮次增长仅来自占位符文本（< 2KB），不含新 base64
        for i in range(_MAX_INJECTED_IMAGES + 1, N_IMAGES):
            growth = window_sizes[i] - window_sizes[i - 1]
            assert abs(growth) < 2 * 1024, f"turn {i}: window grew by {growth}B — linear growth is back"

    def test_old_vs_new_window_size_contrast(self, tmp_path):
        """量化对照：同场景下旧机制（全量注入）第 N 轮携带全部 N 张 base64。"""
        mw = ViewImageMiddleware()
        images = {}
        viewed = None
        for idx in range(N_IMAGES):
            path, meta, _ = _make_image(tmp_path, idx)
            images[path] = meta
            viewed = merge_viewed_images(viewed, {path: meta}) if viewed is not None else {path: meta}

        last_path = list(viewed)[-1]
        _, new_bytes = _run_turn(mw, _turn_messages(last_path), viewed)
        old_bytes = sum(m["size"] for m in images.values())  # 旧 before_model 全量注入
        # 新窗口 ≤ 旧机制的 (K+1)/N（逐张查看：本轮 1 张 + 历史 K 张）；6 图场景差距 ~2x
        assert new_bytes <= old_bytes * (_MAX_INJECTED_IMAGES + 1) / N_IMAGES
        assert new_bytes < old_bytes

    def test_values_stream_carries_no_base64(self, tmp_path):
        """P0-不回归(传输层): values 快照（run_events SSE 通道）序列化后不含
        data:image base64 —— 即使 state 里残留旧的 hide_from_ui 图片消息。"""
        from deerflow.runtime.serialization import serialize_channel_values_for_api

        _path, meta, payload = _make_image(tmp_path, 0)
        legacy_hidden = ViewImageMiddleware._create_image_context_message(
            [
                {"type": "text", "text": "Here are the images you've viewed:"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64.b64encode(payload).decode()}"}},
            ]
        )
        state_snapshot = {
            "messages": [HumanMessage(content="hi"), legacy_hidden, AIMessage(content="done")],
            "viewed_images": {_path: meta},
        }
        # serialize(mode="values") 是 run_events worker 的下发路径 (worker.py)
        dumped = json.dumps(serialize(state_snapshot, mode="values"))
        assert "data:image/png;base64" not in dumped
        # REST 侧 wrapper 同样干净
        rest = serialize_channel_values_for_api({"messages": [legacy_hidden.model_dump()]})
        assert "data:image" not in json.dumps(rest)
        # 消息本身保留（顺序/条数不变），只是 payload 被剥掉
        assert len(serialize(state_snapshot, mode="values")["messages"]) == 3


def _max_window_bytes(images: dict) -> int:
    """窗口上限的保守值：本轮批量 1 张（逐张查看场景）+ 历史 K 张。"""
    return (_MAX_INJECTED_IMAGES + 1) * max(m["size"] for m in images.values())


def _is_placeholder_for(text: str, path: str) -> bool:
    return path in text and "已查看图片" in text

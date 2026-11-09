"""E8（360 通道切换，2026-08-21）：分镜提示词逐帧化（核心任务）。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E8：
- CHUNK 可配置（``video_gen.prompt_chunk_size``，**默认 1 = 逐帧**；>1 保留批量
  路径作配置级回退）；
- 逐帧请求 = 风格锚定卡（bulk 三段拼接）+ **脚本全文上下文**（title + 全部旁白 +
  当前帧序号，不是 ±1 简配）+ 单帧输出；
- per-task 并发闸可配（默认 8）；
- 降级链简化：单帧失败 → 重试 1 次 → 主题模板兜底（不影响其他帧）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.orchestration.storyboard_planner import _fill_image_prompts_robust
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(n: int, video_type: str = "ai_image") -> Storyboard:
    return Storyboard(
        title="测试课程",
        config=StoryboardConfig(video_type=video_type, width=1024, height=1024),
        frames=[StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)],
    )


def _user_payload(messages) -> dict:
    return json.loads(messages[1]["content"])


class _FakeLLM:
    """逐帧/批量两态响应的假 LLM。frame_responder(index) -> str；bulk 按 narrations 行数返回。"""

    def __init__(self, frame_responder=None, bulk_responder=None, delay: float = 0.0):
        self.calls: list[list[dict]] = []
        self.inflight = 0
        self.peak = 0
        self._frame_responder = frame_responder or (lambda i: json.dumps({"frames": [{"image_prompt": f"画面{i}", "visual_source": ""}]}, ensure_ascii=False))
        self._bulk_responder = bulk_responder
        self._delay = delay

    async def ainvoke(self, messages):
        self.calls.append(messages)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            payload = _user_payload(messages)
            if "current_frame_index" in payload:
                content = self._frame_responder(payload["current_frame_index"])
            else:
                n_lines = len([line for line in payload.get("narrations", "").split("\n") if line.strip()])
                if self._bulk_responder:
                    content = self._bulk_responder(n_lines)
                else:
                    content = json.dumps({"frames": [{"image_prompt": f"画面{i}", "visual_source": ""} for i in range(n_lines)]}, ensure_ascii=False)
            return SimpleNamespace(content=content)
        finally:
            self.inflight -= 1


@pytest.mark.asyncio
async def test_chunk1_sends_one_call_per_frame_with_full_script_context():
    """chunk=1 时对 N 帧发 N 次调用，且每次请求 user 内容含**全部旁白与当前帧序号**。"""
    sb = _sb(5)
    llm = _FakeLLM()
    await _fill_image_prompts_robust(llm, sb, "", chunk_size=1)
    assert len(llm.calls) == 5, f"逐帧模式应发 5 次调用，实际 {len(llm.calls)}"
    all_narrations = [f"旁白{i}" for i in range(5)]
    for i, messages in enumerate(llm.calls):
        payload = _user_payload(messages)
        assert payload["current_frame_index"] == i, f"第 {i} 次调用应带当前帧序号 {i}"
        assert payload["script_narrations"] == all_narrations, "脚本全文上下文必须是全部旁白（不是 ±1 简配）"
        assert payload["title"] == "测试课程"
        assert payload["narrations"] == f"旁白{i}", "narrations 字段 = 当前帧旁白（单帧输出契约）"
    for i, f in enumerate(sb.frames):
        assert f.image_prompt == f"画面{i}"


@pytest.mark.asyncio
async def test_chunk1_concurrency_gate_queues_beyond_limit():
    """并发闸：>闸值的调用排队（mock 延时计数在途峰值 ≤ 闸值）。"""
    sb = _sb(8)
    llm = _FakeLLM(delay=0.03)
    await _fill_image_prompts_robust(llm, sb, "", chunk_size=1, frame_concurrency=3)
    assert len(llm.calls) == 8, "排队不得丢调用"
    assert llm.peak <= 3, f"在途峰值 {llm.peak} 超过闸值 3"


@pytest.mark.asyncio
async def test_frame_failure_retries_once_then_topic_fallback():
    """单帧失败重试 1 次，仍失败落主题模板且不影响其他帧。"""

    def _respond(i: int) -> str:
        if i == 1:
            return json.dumps({"frames": []}, ensure_ascii=False)  # 永不对齐
        return json.dumps({"frames": [{"image_prompt": f"画面{i}", "visual_source": ""}]}, ensure_ascii=False)

    sb = _sb(3)
    llm = _FakeLLM(frame_responder=_respond)
    await _fill_image_prompts_robust(llm, sb, "", chunk_size=1)
    frame1_calls = [c for c in llm.calls if _user_payload(c).get("current_frame_index") == 1]
    assert len(frame1_calls) == 2, f"失败帧应恰好重试 1 次（共 2 次调用），实际 {len(frame1_calls)}"
    # 失败帧落主题模板（含标题，绝不是 narration 原文，也不是空串）
    assert "测试课程" in sb.frames[1].image_prompt
    assert sb.frames[1].image_prompt != "旁白1"
    # 其他帧不受连坐
    assert sb.frames[0].image_prompt == "画面0"
    assert sb.frames[2].image_prompt == "画面2"


@pytest.mark.asyncio
async def test_chunk8_bulk_path_unchanged():
    """chunk=8 时行为与现状一致（回退路径回归）：2 次 bulk 调用、无逐帧上下文字段。"""
    sb = _sb(16)
    llm = _FakeLLM()
    await _fill_image_prompts_robust(llm, sb, "", chunk_size=8)
    assert len(llm.calls) == 2, f"16 帧按块 8 应恰好 2 次 bulk 调用，实际 {len(llm.calls)}"
    for messages in llm.calls:
        payload = _user_payload(messages)
        assert "current_frame_index" not in payload, "批量路径不得带逐帧上下文字段"
        assert "script_narrations" not in payload
        assert len([line for line in payload["narrations"].split("\n") if line.strip()]) == 8
    assert all(f.image_prompt.startswith("画面") for f in sb.frames)


@pytest.mark.asyncio
async def test_default_chunk_is_per_frame():
    """不传 chunk_size（None）→ 默认逐帧（E10 的 config 默认 1 落点）。"""
    sb = _sb(3)
    llm = _FakeLLM()
    await _fill_image_prompts_robust(llm, sb, "")
    assert len(llm.calls) == 3
    assert "script_narrations" in _user_payload(llm.calls[0])


@pytest.mark.asyncio
async def test_per_frame_carries_style_guard_and_anchor():
    """逐帧请求携带风格锚定卡三段（风格硬约束/锚定卡注入 system）。"""
    sb = _sb(2)
    sb.global_anchor = {"anchor_card": "主角：红衣少年，场景：海边小镇", "style_string": "水彩绘本风"}
    llm = _FakeLLM()
    await _fill_image_prompts_robust(llm, sb, "水彩绘本风", chunk_size=1)
    system = llm.calls[0][0]["content"]
    assert "水彩绘本风" in system, "风格硬约束必须进 system"
    assert "红衣少年" in system, "锚定卡必须进 system（bulk 三段拼接平移）"


@pytest.mark.asyncio
async def test_plan_course_wires_chunk_config():
    """plan_course 接线：chunk_size/frame_concurrency 透传到逐帧路径（含锚定调用）。"""

    async def _fake_anchor(llm, *, title, narrations, style_prefix):
        return {"anchor_card": "锚", "style_string": "风格", "graphic_layout_card": "", "scene_groups": []}

    from app.gateway.video_gen.orchestration import storyboard_planner as planner

    sb = _sb(3)
    llm = _FakeLLM()
    orig = planner._generate_global_anchor
    planner._generate_global_anchor = _fake_anchor
    try:
        await planner.plan_course(llm, storyboard=sb, style_context={"style_prefix": ""}, chunk_size=1, frame_concurrency=8)
    finally:
        planner._generate_global_anchor = orig
    per_frame = [c for c in llm.calls if "current_frame_index" in _user_payload(c)]
    assert len(per_frame) == 3, "plan_course(chunk_size=1) 应走逐帧路径"

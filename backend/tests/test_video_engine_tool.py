"""Tests for generate_video tool (阶段 1.6 + 1.9 wall-clock timeout).

Tool lives in app layer (P0-1: not in harness). It's a thin adapter: parse
args → call an injected executor (service-layer pipeline) → apply wall-clock
timeout (1.9, vimax 15-30min has no other timeout) → return result dict.
Progress flows via service SSE (P0-2), not through this tool's return.
The executor wiring (app.state → VideoGenService) is 1.8's integration job.
"""

import asyncio

import pytest

from app.gateway.video_gen.engines.tool import GenerateVideoTool


@pytest.mark.asyncio
async def test_generate_video_returns_done_result():
    async def executor(*, video_type, task_id, pipeline, style):
        return {"video_path": "/tmp/out.mp4", "mode": "finished"}

    tool = GenerateVideoTool(execute_fn=executor, timeout=30.0)
    result = await tool._arun(video_type="remotion", task_id="t1")
    assert result["status"] == "done"
    assert result["task_id"] == "t1"
    assert result["video_path"] == "/tmp/out.mp4"
    assert result["mode"] == "finished"


@pytest.mark.asyncio
async def test_generate_video_passes_vimax_knobs_to_executor():
    captured: dict = {}

    async def executor(*, video_type, task_id, pipeline, style):
        captured.update(video_type=video_type, task_id=task_id, pipeline=pipeline, style=style)
        return {"video_path": "/tmp/v.mp4", "mode": "finished"}

    tool = GenerateVideoTool(execute_fn=executor, timeout=30.0)
    await tool._arun(video_type="vimax", task_id="t9", pipeline="script2video", style="Cartoon")
    assert captured == {"video_type": "vimax", "task_id": "t9", "pipeline": "script2video", "style": "Cartoon"}


@pytest.mark.asyncio
async def test_generate_video_wall_clock_timeout_returns_timeout_status():
    """1.9: vimax 15-30min blocking has no other timeout; the tool enforces one."""

    async def slow_executor(*, video_type, task_id, pipeline, style):
        await asyncio.sleep(10)
        return {"video_path": "/tmp/never.mp4"}

    tool = GenerateVideoTool(execute_fn=slow_executor, timeout=0.05)
    result = await tool._arun(video_type="vimax", task_id="t1")
    assert result["status"] == "timeout"
    assert result["task_id"] == "t1"
    assert "0.05" in result["error"]


@pytest.mark.asyncio
async def test_generate_video_propagates_executor_error_as_status():
    async def failing_executor(*, video_type, task_id, pipeline, style):
        raise RuntimeError("render blew up")

    tool = GenerateVideoTool(execute_fn=failing_executor, timeout=30.0)
    result = await tool._arun(video_type="vimax", task_id="t1")
    assert result["status"] == "error"
    assert "render blew up" in result["error"]


def test_generate_video_tool_name_and_singleton():
    from app.gateway.video_gen.engines.tool import generate_video_tool

    assert generate_video_tool.name == "generate_video"
    # singleton exists for config.yaml tools[] registration (use: ...:generate_video_tool)
    assert generate_video_tool is not None


def test_generate_video_description_requires_ask_clarification_gate():
    """2026-07-21 (vimax 对话流修复): 前置确认门必须是 ask_clarification——唯一被
    ClarificationMiddleware 拦截并 goto=END 的"暂停等用户"通道。旧描述把确认写成
    "present_files 让用户确认蓝图"（展示+纯文本等待），纯文本回复（无 tool_calls）+
    未完成 todo 会被 TodoMiddleware 防退出逻辑打回，逼 agent 跳过确认直接渲染。"""
    tool = GenerateVideoTool()
    assert "present_files 让用户确认蓝图" not in tool.description
    assert "ask_clarification" in tool.description

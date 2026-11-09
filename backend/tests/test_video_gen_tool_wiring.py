"""Tests for generate_video tool → VideoGenService wiring (阶段 1.8).

The tool's ``execute_fn`` is wired at app startup (via
``wire_generate_video_executor``) to ``VideoGenService.run_pipeline_for_tool``,
which bridges the tool's ``(video_type, task_id, pipeline, style)`` signature to
the service's full pipeline lifecycle (``_wrap_pipeline``: progress/SSE/status/
retry/cancel-drain). The tool already wraps the call in ``asyncio.wait_for``
(1.9 wall-clock timeout); because ``_wrap_pipeline`` catches ``CancelledError``,
marks the task failed, and re-raises, a tool timeout yields correct
cancel+drain — the task is drained to a terminal status and the tool reports
``timeout``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.engines.tool import GenerateVideoTool
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


def _make_service(tmp_path):
    cfg = VideoGenConfig(qihu_api_key="t", engine_arch="multi_engine")
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


class _FakeTask:
    """Stand-in for a TaskRow — only the fields run_pipeline_for_tool reads."""

    def __init__(self, *, task_id="vg_1", user_id="u1", task_type="remotion", payload=None, status="ready", result=None, error_message=None):
        self.id = task_id
        self.user_id = user_id
        self.task_type = task_type
        self.payload = json.dumps(payload or {"script": "hi", "mode": "fixed"})
        self.status = status
        self.result = json.dumps(result) if result is not None else None
        self.error_message = error_message


# ── run_pipeline_for_tool ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_returns_submitted_mode(tmp_path):
    """M1.2: run_pipeline_for_tool fire-and-forgets the render and returns immediately
    with {mode: submitted, task_id}. It does NOT block on completion. Updated from the
    old blocking contract (which awaited _wrap_pipeline and returned video_path). The
    mode is 'submitted' (not 'rendering') because real progress is reported via the
    task SSE / check_video_status, not the tool's return value."""
    svc = _make_service(tmp_path)
    # remotion 已摘牌（2026-07-22，service 层 ValueError）；本测试测 submitted-mode
    # 契约，与引擎无关 → 用 vimax（task_type 显式化，不再吃 _FakeTask 的 remotion 默认值）
    task = _FakeTask(task_type="vimax", status="ready", result={"output_path": "/tmp/out.mp4"})
    svc.repo.get = AsyncMock(return_value=task)
    svc._wrap_pipeline = AsyncMock()

    result = await svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_1")

    assert result == {"task_id": "vg_1", "mode": "submitted"}
    # fire-and-forget: the background task is scheduled but may not have run yet.
    # yield to the loop so it starts.
    await asyncio.sleep(0)
    # SEC-H6（批次 2 项 2.2）：归属过滤生效后 pipeline 以 contextvar user 运行
    # （旧断言钉的是未过滤行的 task.user_id）。repo.get 同样带 user_id 过滤。
    from deerflow.runtime.user_context import get_effective_user_id

    svc._wrap_pipeline.assert_awaited_once_with("vg_1", get_effective_user_id(), "vimax", {"script": "hi", "mode": "fixed", "auto_render": True, "reuse_blueprint": True})
    assert svc.repo.get.await_args.kwargs.get("user_id") == get_effective_user_id()


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_rejects_foreign_task(tmp_path):
    """SEC-H6（批次 2 项 2.2）：他/她人 task_id 不得触发渲染——归属过滤后 None
    即 ValueError（与 HTTP 路由层同口径），不泄露存在性差异。"""
    svc = _make_service(tmp_path)

    async def _filtered_get(task_id, user_id=None):
        return None  # ownership filter hides the foreign row

    svc.repo.get = AsyncMock(side_effect=_filtered_get)
    svc._wrap_pipeline = AsyncMock()

    with pytest.raises(ValueError, match="not found"):
        await svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_someone_elses")

    svc._wrap_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_fire_and_forgets_long_render(tmp_path):
    """M1.2: generate_video must NOT block the chat run on a 15-min render. The
    agent's chat run (SSE stream) awaits the tool; if the tool awaits the full
    render, a stream disconnect/timeout cancels the await → CancelledError →
    task marked 'Cancelled by user' (the root cause behind idea2video crashing
    mid-run). Fix: fire-and-forget — schedule _wrap_pipeline as a background task
    and return immediately with {mode: submitted, task_id}. Progress flows via the
    task SSE; the agent tells the user it's rendering."""
    svc = _make_service(tmp_path)
    task = _FakeTask(task_type="vimax", payload={"script": "hi", "mode": "fixed"})
    svc.repo.get = AsyncMock(side_effect=[task])  # only fetched once (no re-fetch)

    ran = asyncio.Event()

    async def slow_pipeline(*a, **kw):
        # simulate a long render; if the tool awaited this, the test would hang
        ran.set()
        await asyncio.sleep(60)

    svc._wrap_pipeline = slow_pipeline

    result = await asyncio.wait_for(
        svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_1"),
        timeout=5.0,  # must return well before the 60s render
    )

    # returns immediately with a submitted-mode result, NOT awaiting completion
    assert result.get("mode") == "submitted"
    assert result.get("task_id") == "vg_1"
    # yield to the loop so the background task starts
    await asyncio.sleep(0)
    # the background pipeline was launched
    assert ran.is_set()
    # and tracked for cancellation/cleanup
    assert "vg_1" in svc._active_tasks


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_raises_when_task_missing(tmp_path):
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=None)

    # 用 vimax：remotion 会在摘牌 gate 先抛（guard 顺序：摘牌 → repo.get → not found）
    with pytest.raises(ValueError, match="not found"):
        await svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_nope")


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_injects_vimax_knobs_into_payload(tmp_path):
    """vimax pipeline/style knobs (阶段 2) ride into the engine via payload."""

    svc = _make_service(tmp_path)
    task = _FakeTask(status="ready", result={"output_path": "/tmp/v.mp4"})
    svc.repo.get = AsyncMock(return_value=task)
    svc._wrap_pipeline = AsyncMock()

    await svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_1", pipeline="script2video", style="Cartoon")

    # _wrap_pipeline(task_id, user_id, task_type, payload) — payload is positional arg 4
    payload = svc._wrap_pipeline.call_args.args[3]
    assert payload["pipeline"] == "script2video"
    assert payload["style"] == "Cartoon"
    assert payload["auto_render"] is True  # force the full render path, never the draft branch
    assert payload["reuse_blueprint"] is True  # A1: reuse the confirmed storyboard, no regenerate


@pytest.mark.asyncio
async def test_run_pipeline_for_tool_does_not_mutate_stored_payload(tmp_path):
    """The injected knobs must not leak back into the task row's persisted payload."""

    svc = _make_service(tmp_path)
    task = _FakeTask(status="ready", result={"output_path": "/tmp/v.mp4"})
    svc.repo.get = AsyncMock(return_value=task)
    svc._wrap_pipeline = AsyncMock()

    await svc.run_pipeline_for_tool(video_type="vimax", task_id="vg_1", pipeline="idea2video", style="Anime Style")

    assert "pipeline" not in json.loads(task.payload)
    assert "style" not in json.loads(task.payload)


# ── wire_generate_video_executor ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_wire_executor_makes_tool_delegate_end_to_end(tmp_path):
    """Wiring the real service method into a fresh tool makes _arun delegate through."""

    from app.gateway.video_gen.engines.tool import wire_generate_video_executor

    svc = _make_service(tmp_path)
    task = _FakeTask(task_type="vimax", status="ready", result={"output_path": "/tmp/x.mp4"})
    svc.repo.get = AsyncMock(side_effect=[task, task])
    svc._wrap_pipeline = AsyncMock()

    tool = GenerateVideoTool(timeout=30.0)  # fresh instance — don't touch the singleton
    assert tool.execute_fn is None

    wire_generate_video_executor(tool, svc)

    # Bound methods are fresh objects per attribute access, so compare by the
    # underlying function + instance, not identity.
    assert tool.execute_fn is not None
    assert tool.execute_fn.__func__ is type(svc).run_pipeline_for_tool
    assert tool.execute_fn.__self__ is svc
    result = await tool._arun(video_type="vimax", task_id="vg_1")  # remotion 已摘牌
    assert result["status"] == "done"
    assert result["mode"] == "submitted"  # M1.2 fire-and-forget contract
    assert result["task_id"] == "vg_1"

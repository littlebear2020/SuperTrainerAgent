"""Tests for check_video_status tool + VideoGenService.check_task_status_for_tool.

Why this tool exists: generate_video is fire-and-forget (returns
{mode: "submitted"} instantly). Without a way to read ground-truth status, the
agent keeps saying "rendering..." forever — even after the task FAILED. This
was the exact failure mode of vg_7477487923ef (agent narrated "ViMax GPU
aligning audio/video" while the task had been failed for 10 minutes). These
tests pin the three states the agent must be able to distinguish: in-flight,
ready (with output_url), and failed (with error_message).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.video_gen.engines.check_status_tool import (
    CheckVideoStatusTool,
    wire_check_status_executor,
)


def _row(*, status, progress=50, error_message=None, result_json="{}", updated_at=None):
    return TaskRow(
        id="vg_abc123",
        user_id="u1",
        domain="video_gen",
        task_type="vimax",
        status=status,
        progress=progress,
        payload="{}",
        result=result_json,
        error_message=error_message,
        retry_count=0,
        created_at=datetime.now(UTC),
        updated_at=updated_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_tool_returns_submitted_status_when_executor_not_wired():
    """An unwired tool fails loudly (not silently returns 'rendering')."""
    tool = CheckVideoStatusTool()  # no execute_fn
    result = await tool._arun(task_id="vg_x")
    assert result["status"] == "error"
    assert "not wired" in result["error"]


@pytest.mark.asyncio
async def test_tool_relays_executor_result_and_catches_exceptions():
    relayed = {}

    async def executor(*, task_id):
        relayed["task_id"] = task_id
        return {"task_id": task_id, "status": "ready", "progress": 100}

    tool = CheckVideoStatusTool(execute_fn=executor)
    result = await tool._arun(task_id="vg_y")
    assert relayed == {"task_id": "vg_y"}
    assert result["status"] == "ready"

    # executor raising → status:error (not a crash)
    async def boom(*, task_id):
        raise RuntimeError("db down")

    tool2 = CheckVideoStatusTool(execute_fn=boom)
    result2 = await tool2._arun(task_id="vg_z")
    assert result2["status"] == "error"
    assert "db down" in result2["error"]


@pytest.mark.asyncio
async def test_service_check_status_in_flight_task():
    """In-flight task: status + progress + updated_at, no output_url/error_message."""
    updated = datetime.now(UTC) - timedelta(minutes=2)
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=_row(status="rendering", progress=75, updated_at=updated))

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_abc123")

    assert info["task_id"] == "vg_abc123"
    assert info["status"] == "rendering"
    assert info["progress"] == 75
    assert "output_url" not in info
    assert "updated_at" in info
    # SEC-H6（批次 2 项 2.2）：状态读取按归属过滤（contextvar user_id，与 HTTP
    # 路由层同口径）——旧 owner-agnostic 读法是他/她人 output_url 泄露面。
    repo.get.assert_awaited_once_with("vg_abc123", user_id=ANY)


@pytest.mark.asyncio
async def test_service_check_status_foreign_task_is_not_found():
    """SEC-H6（批次 2 项 2.2）：他/她人任务对外与不存在同形（not_found）。"""
    repo = AsyncMock()

    # Simulate the ownership filter: another user's task row is invisible.
    async def _filtered_get(task_id, user_id=None):
        return None

    repo.get = AsyncMock(side_effect=_filtered_get)

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_someone_elses")

    assert info == {"task_id": "vg_someone_elses", "status": "not_found"}
    assert repo.get.await_args.kwargs.get("user_id") is not None


@pytest.mark.asyncio
async def test_service_check_status_ready_task_has_output_url():
    """Ready task: surface the download route so the agent can point the user at the
    video (D4, 2026-07-15 split: file_size pruned — not in the allowed return set)."""
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row(
            status="ready",
            progress=100,
            result_json='{"output_path": "/data/out.mp4", "file_size": 12345}',
        )
    )

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_abc123")

    assert info["status"] == "ready"
    assert info["output_url"] == "/api/video/tasks/vg_abc123/download"
    assert "file_size" not in info  # D4: file_size pruned (not in allowed set)


@pytest.mark.asyncio
async def test_service_check_status_failed_task_has_error_message():
    """Failed task: surface error_message verbatim — the agent must be able to tell the user it FAILED,
    not narrate 'still rendering'. This is the regression guard for vg_7477487923ef."""
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row(
            status="failed",
            progress=75,
            error_message="visual source for vimax not implemented in this phase",
        )
    )

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_abc123")

    assert info["status"] == "failed"
    assert info["error_message"] == "visual source for vimax not implemented in this phase"
    assert "output_url" not in info


@pytest.mark.asyncio
async def test_service_check_status_missing_task():
    """Unknown task_id → not_found (not a crash), so the agent can say 'no such task'."""
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_missing")

    assert info == {"task_id": "vg_missing", "status": "not_found"}


@pytest.mark.asyncio
async def test_wire_check_status_executor_binds_service_method():
    """wiring binds service.check_task_status_for_tool (idempotent)."""
    tool = CheckVideoStatusTool()
    assert tool.execute_fn is None

    service = MagicMock()
    wire_check_status_executor(tool, service)
    assert tool.execute_fn is service.check_task_status_for_tool


# ── D4 (2026-07-15 split): prune fields the agent could脑补 from ──────────────


@pytest.mark.asyncio
async def test_service_check_status_in_flight_with_error_does_not_leak_retry_note():
    """D4: an in-flight task that hit a transient error (a retry note in error_message)
    must NOT surface that note to the agent — otherwise the agent brainstorms
    'retry stage / GPU node / audio-video alignment' from it (the vg_7477487923ef
    failure mode). Only *failed* tasks surface error_message."""
    repo = AsyncMock()
    repo.get = AsyncMock(
        return_value=_row(
            status="rendering",
            progress=78,
            error_message="Retry 1/2: transient timeout, rescheduling",
        )
    )

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc.repo = repo

    info = await svc.check_task_status_for_tool(task_id="vg_abc123")

    assert info["status"] == "rendering"
    assert "note" not in info, f"in-flight retry note leaked: {info.get('note')}"
    assert "retry_count" not in info
    assert "error_message" not in info  # only failed surfaces error_message


@pytest.mark.asyncio
async def test_service_check_status_return_keys_are_pruned():
    """D4: the returned dict must only carry status/progress/output_url/error_message/
    updated_at (+ task_id echo) — no file_size, no internal thread id, no 阶段 details
    the agent could brainstorm from."""
    allowed = {"task_id", "status", "progress", "output_url", "error_message", "updated_at"}

    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)

    # ready task with file_size in result → must NOT leak file_size
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=_row(status="ready", progress=100, result_json='{"file_size": 999}'))
    svc.repo = repo
    info = await svc.check_task_status_for_tool(task_id="vg_abc123")
    assert set(info).issubset(allowed), f"unexpected keys: {set(info) - allowed}"
    assert "file_size" not in info


# ── D3 (2026-07-15 split): cap same-task polling刷屏 ─────────────────────────


@pytest.mark.asyncio
async def test_check_video_status_rate_limits_rapid_same_task_polls():
    """D3: rapid repeated check_video_status for the SAME task_id is rate-limited —
    the executor (DB read) is called once, the 2nd immediate call returns a
    polling_rate_limited hint without hitting the DB. Prevents the agent刷屏-polling a
    fire-and-forget render until the tool-call total limit (split §一 problem 3).
    A different task_id is not rate-limited (cap is per-task)."""
    calls = []

    async def executor(*, task_id):
        calls.append(task_id)
        return {"task_id": task_id, "status": "rendering", "progress": 75}

    tool = CheckVideoStatusTool(execute_fn=executor)
    r1 = await tool._arun(task_id="vg_x")
    r2 = await tool._arun(task_id="vg_x")  # immediate — within MIN_POLL_INTERVAL

    assert len(calls) == 1, "executor should be called once (2nd rate-limited)"
    assert r1["status"] == "rendering"
    assert r2["status"] == "polling_rate_limited"
    assert "retry_after_seconds" in r2

    # a DIFFERENT task_id is not rate-limited
    r3 = await tool._arun(task_id="vg_y")
    assert len(calls) == 2
    assert r3["status"] == "rendering"


# ── 2026-07-21: 收紧 description 触发条件（vimax 对话流修复）─────────────────


def test_description_limits_invocation_to_explicit_status_queries():
    """2026-07-21: description 不得再含"对话中涉及任何 vg_ 任务 ID 时必须先调用"的
    强制措辞——thread 历史一旦出现过任务 ID，agent 之后每一轮（甚至还没规划脚本）
    都被工具描述逼着先调本工具。保留"防编造进度"的核心目的（vg_7477487923ef），
    但触发条件限定为：用户明确询问进度/追问历史任务，或 agent 准备报告渲染结果前。"""
    tool = CheckVideoStatusTool()
    assert "涉及任何" not in tool.description
    # 仍须保留防编造的核心戒律（这是工具存在的意义）
    assert "禁止凭记忆" in tool.description
    # 触发条件限定措辞：用户询问 / 报告结果前
    assert "询问" in tool.description


def test_description_documents_all_real_statuses():
    """2026-07-22: 真实状态还有 composing（Worker 合成）与 story_ready（idea2video
    故事待确认）——词表缺失会让 agent 收到未文档化字符串时自由发挥。"""
    tool = CheckVideoStatusTool()
    for s in ("composing", "story_ready", "ready", "failed"):
        assert s in tool.description, f"status vocabulary missing {s!r}"

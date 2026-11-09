"""Tests for the SSE render-progress endpoint's terminal-status logic.

The ``/tasks/{id}/events`` stream must keep emitting while a task is in flight
(running / composing / draft / pending / sub-statuses) and only send the
terminal ``done`` event when the task reaches ``ready`` or ``failed``. Emitting
``done`` for any non-(running, composing) status — as an earlier version did —
closes the stream prematurely during the draft / pending phases.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import _render_is_terminal, router


def test_render_is_terminal_only_for_ready_and_failed():
    assert _render_is_terminal("ready") is True
    assert _render_is_terminal("failed") is True


def test_render_is_terminal_false_for_in_flight_statuses():
    # SSE must keep streaming through these, not emit "done" prematurely.
    for status in ("running", "composing", "draft", "pending", "synthesizing", "rendering"):
        assert _render_is_terminal(status) is False, f"{status!r} must not be treated as terminal"


def _make_streaming_app(tmp_path, *, statuses, progress=95):
    """App whose mock svc.get_task returns the given statuses in order (then repeats last).

    Driving composing→ready makes event_generator yield a progress heartbeat then
    terminal `done` and break, so the SSE stream terminates for testing.
    """
    import json as _json

    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    storage_root.mkdir()
    (storage_root / "test-user-sse" / "tasks" / "vg_sse1").mkdir(parents=True)
    mock_svc = MagicMock()
    mock_svc._storage_dir = str(storage_root)
    tasks = []
    for s in statuses:
        t = MagicMock()
        t.id = "vg_sse1"
        t.status = s
        t.progress = progress
        t.result = "{}"
        t.payload = _json.dumps({"audio_ready": False})
        tasks.append(t)
    mock_svc.get_task = AsyncMock(side_effect=tasks)
    app.state.video_gen_service = mock_svc
    return app


def test_sse_emits_progress_during_composing(tmp_path):
    """M3-3c: composing 期 SSE 应发 progress 心跳，否则前端 composing 盲等无信号。"""
    app = _make_streaming_app(tmp_path, statuses=["composing", "ready"], progress=95)
    client = TestClient(app)
    events = []
    with client.stream("GET", "/api/video/tasks/vg_sse1/events") as resp:
        for line in resp.iter_lines():
            if line.startswith("event:"):
                events.append(line)
            if line.startswith("event: done"):
                break
    assert any("progress" in e for e in events), f"应含 progress 事件，got {events}"

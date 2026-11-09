"""R5: recompose_task must NOT clear dirty_frames before dispatch.

If the pipeline fails and retries, the user must still see 'N 处修改' so
they can retry. Clearing happens on Worker success instead (see
test_worker_clears_dirty_frames.py). Uses the VideoGenService(repo=MagicMock)
fixture pattern from test_video_gen_metrics.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.service import VideoGenService


def _make_svc(tmp_path, task_row):
    cfg = MagicMock()
    cfg.frame_concurrency = 2
    cfg.max_concurrent_tasks = 2
    cfg.stall_timeout_seconds = 1800
    providers = MagicMock()
    repo = MagicMock()
    repo.get = AsyncMock(return_value=task_row)
    repo.update = AsyncMock()
    svc = VideoGenService(
        repo=repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    # Prevent the real _wrap_pipeline from running (we only test recompose_task).
    svc._wrap_pipeline = AsyncMock()
    svc._active_tasks = {}
    # E13: recompose 现要求 audio.mp3 存在（缺音频的 failed 任务会被拒）；测试需 seed。
    (tmp_path / "u1" / "tasks" / "t1" / "audio.mp3").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "u1" / "tasks" / "t1" / "audio.mp3").write_bytes(b"au")
    return svc, repo


def _make_task_row(status="ready", dirty_frames="[0, 3]"):
    row = MagicMock()
    row.id = "t1"
    row.user_id = "u1"
    row.task_type = "ai_image"
    row.status = status
    row.payload = '{"topic": "x"}'
    row.dirty_frames = dirty_frames
    return row


@pytest.mark.asyncio
async def test_recompose_does_not_clear_dirty_frames_before_dispatch(tmp_path):
    row = _make_task_row(status="ready", dirty_frames="[0, 3]")
    svc, repo = _make_svc(tmp_path, row)

    await svc.recompose_task("t1", "u1")
    # Drain the dispatched (mocked) pipeline task to avoid "Task was destroyed
    # but it is pending" warnings at test teardown.
    pending = svc._active_tasks.get("t1")
    if pending is not None:
        await pending

    # Must NOT have cleared dirty_frames — Worker success clears it instead.
    clear_calls = [kw for _, kw in repo.update.call_args_list if kw.get("dirty_frames") == "[]"]
    assert clear_calls == [], "recompose_task must not clear dirty_frames before dispatch (R5)"


@pytest.mark.asyncio
async def test_recompose_still_dispatches_pipeline(tmp_path):
    row = _make_task_row(status="ready", dirty_frames="[0, 3]")
    svc, repo = _make_svc(tmp_path, row)

    result = await svc.recompose_task("t1", "u1")
    pending = svc._active_tasks.get("t1")
    if pending is not None:
        await pending

    assert result == {"task_id": "t1", "status": "composing"}
    svc._wrap_pipeline.assert_awaited_once()

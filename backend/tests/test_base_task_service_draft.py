"""Tests for BaseTaskService._wrap_pipeline draft branch."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT


@pytest.mark.asyncio
async def test_wrap_pipeline_sets_draft_status_legacy_dict(tmp_path):
    """When _run_pipeline returns legacy {'_draft': True} dict, status should be set to 'draft'."""
    from app.gateway._shared.task_framework.service import BaseTaskService

    # Minimal concrete subclass
    class _TestService(BaseTaskService):
        domain = "test"
        id_prefix = "ts"

        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            return {"_draft": True, "storyboard": {"title": "test", "frames": []}}

    repo = MagicMock()
    repo.update = AsyncMock()
    repo.get = AsyncMock(return_value=MagicMock(result="{}"))
    svc = _TestService(repo=repo, storage_dir=str(tmp_path))

    # Patch _cleanup_task to avoid issues
    svc._cleanup_task = AsyncMock()

    # Make _active_tasks a real dict
    svc._active_tasks = {}

    await svc._wrap_pipeline(
        task_id="ts_test",
        user_id="u1",
        task_type="test",
        payload={},
    )

    # Verify update was called with status="draft"
    calls = repo.update.call_args_list
    status_calls = [c for c in calls if c.kwargs.get("status") == DRAFT]
    assert len(status_calls) == 1

    # Verify result was saved with storyboard data
    result_calls = [c for c in calls if "result" in c.kwargs]
    assert len(result_calls) >= 1
    import json

    result_data = json.loads(result_calls[0].kwargs["result"])
    assert "storyboard" in result_data


@pytest.mark.asyncio
async def test_wrap_pipeline_sets_draft_status_pipeline_draft(tmp_path):
    """When _run_pipeline returns PipelineDraft dataclass, status should be set to 'draft'."""
    from app.gateway._shared.task_framework.service import BaseTaskService
    from app.gateway.video_gen.pipeline_result import PipelineDraft

    class _TestService(BaseTaskService):
        domain = "test"
        id_prefix = "ts"

        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            return PipelineDraft(storyboard={"title": "test", "frames": []})

    repo = MagicMock()
    repo.update = AsyncMock()
    repo.get = AsyncMock(return_value=MagicMock(result="{}"))
    svc = _TestService(repo=repo, storage_dir=str(tmp_path))
    svc._cleanup_task = AsyncMock()
    svc._active_tasks = {}

    await svc._wrap_pipeline(
        task_id="ts_test_pd",
        user_id="u1",
        task_type="test",
        payload={},
    )

    calls = repo.update.call_args_list
    status_calls = [c for c in calls if c.kwargs.get("status") == DRAFT]
    assert len(status_calls) == 1

    import json

    result_calls = [c for c in calls if "result" in c.kwargs]
    result_data = json.loads(result_calls[0].kwargs["result"])
    assert result_data["storyboard"]["title"] == "test"


@pytest.mark.asyncio
async def test_wrap_pipeline_non_draft_result_unaffected(tmp_path):
    """When _run_pipeline returns None (non-draft), it should go to ready/composing as usual."""
    from app.gateway._shared.task_framework.service import BaseTaskService

    class _TestService(BaseTaskService):
        domain = "test"
        id_prefix = "ts"

        async def _run_pipeline(self, task_id, user_id, task_type, payload, task_dir, progress):
            return None  # Gateway-complete

    repo = MagicMock()
    repo.update = AsyncMock()
    svc = _TestService(repo=repo, storage_dir=str(tmp_path))
    svc._cleanup_task = AsyncMock()
    svc._active_tasks = {}

    await svc._wrap_pipeline(
        task_id="ts_test2",
        user_id="u1",
        task_type="test",
        payload={},
    )

    calls = repo.update.call_args_list
    # Should have a "ready" status call (not "draft")
    status_vals = [c.kwargs.get("status") for c in calls if "status" in c.kwargs]
    assert "ready" in status_vals
    assert "draft" not in status_vals

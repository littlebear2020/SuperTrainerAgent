import json
import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.gateway._shared.task_framework.schemas import TaskBrief
from app.gateway.routers.video_gen import _task_with_thumbnail


def make_task(*, status: str = "ready", result: dict | None = None, domain: str = "video_gen") -> SimpleNamespace:
    return SimpleNamespace(
        id="task-1",
        user_id="user-1",
        domain=domain,
        task_type="ai_video",
        status=status,
        progress=100,
        title=None,
        error_message=None,
        retry_count=0,
        dirty_frames=[],
        created_at=None,
        updated_at=None,
        thumbnail_url=None,
        artifact_missing=False,
        cost=None,
        currency=None,
        amount_source=None,
        queue_position=None,
        frame_count=None,
        total_duration=None,
        shot_count=None,
        shots_ready=False,
        payload=json.dumps({"title": "Delivery test"}),
        result=json.dumps(result or {}),
    )


def test_ready_task_uses_one_stat_for_real_delivery_metadata(tmp_path: Path) -> None:
    output = tmp_path / "lesson final.MP4"
    output.write_bytes(b"real video bytes")
    os.utime(output, (1_700_000_123, 1_700_000_123))
    task = make_task(result={"output_path": str(output)})
    original_stat = Path.stat
    output_stat_calls = 0

    def counting_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal output_stat_calls
        if self == output:
            output_stat_calls += 1
        return original_stat(self, *args, **kwargs)

    with patch("pathlib.Path.stat", counting_stat):
        detail = _task_with_thumbnail(task)

    assert output_stat_calls == 1
    assert detail.artifact_missing is False
    assert detail.delivery is not None
    assert detail.delivery.file_name == "lesson final.MP4"
    assert detail.delivery.file_size == len(b"real video bytes")
    assert detail.delivery.file_updated_at == datetime.fromtimestamp(1_700_000_123, UTC)
    assert detail.delivery.file_updated_at.tzinfo == UTC
    assert detail.delivery.media_kind == "video"
    assert detail.delivery.exists is True
    assert str(output) not in str(detail.delivery.model_dump())
    assert set(detail.delivery.model_dump()) == {"file_name", "file_size", "file_updated_at", "media_kind", "exists"}


def test_ready_task_with_missing_output_keeps_safe_missing_metadata(tmp_path: Path) -> None:
    output = tmp_path / "removed.mp4"

    detail = _task_with_thumbnail(make_task(result={"output_path": str(output)}))

    assert detail.artifact_missing is True
    assert detail.delivery is not None
    assert detail.delivery.file_name == "removed.mp4"
    assert detail.delivery.file_size is None
    assert detail.delivery.file_updated_at is None
    assert detail.delivery.media_kind == "video"
    assert detail.delivery.exists is False
    assert str(output) not in str(detail.delivery.model_dump())


@pytest.mark.parametrize("status", ["draft", "running", "failed", "cancelled"])
def test_non_ready_task_never_exposes_stale_delivery(tmp_path: Path, status: str) -> None:
    output = tmp_path / "old.mp4"
    output.write_bytes(b"old")

    detail = _task_with_thumbnail(make_task(status=status, result={"output_path": str(output)}))

    assert detail.delivery is None
    assert detail.artifact_missing is False


def test_ready_task_without_output_path_has_no_delivery() -> None:
    detail = _task_with_thumbnail(make_task(result={}))

    assert detail.delivery is None
    assert detail.artifact_missing is True


def test_stat_race_marks_missing_instead_of_failing_the_whole_list(tmp_path: Path) -> None:
    output = tmp_path / "race.mp4"
    task = make_task(result={"output_path": str(output)})

    with patch("pathlib.Path.exists", return_value=True), patch("pathlib.Path.stat", side_effect=FileNotFoundError("removed between checks")):
        detail = _task_with_thumbnail(task)

    assert detail.artifact_missing is True
    assert detail.delivery is not None
    assert detail.delivery.exists is False
    assert detail.delivery.file_size is None
    assert detail.delivery.file_updated_at is None


def test_other_domains_default_to_no_delivery() -> None:
    brief = TaskBrief(id="other-1", user_id="user-1", domain="writer", task_type="document", status="ready", progress=100)

    assert brief.delivery is None

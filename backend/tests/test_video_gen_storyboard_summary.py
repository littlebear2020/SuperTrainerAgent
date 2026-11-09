"""Task 4（2026-10-20）：任务详情分镜帧与 ViMax 镜头数分源展示。

- 盘面 ``storyboard.json`` 是分镜帧事实源；``result.storyboard`` 仅回落；
- ``result.vimax_shots`` 只表示渲染后的镜头 manifest；
- 列表端点不读盘（避免 N 次 JSON I/O），仅详情传 summary 标志。
"""

import json
import types
from pathlib import Path

from app.gateway.routers.video_gen import _task_with_thumbnail


def _task(result: dict, *, status: str = "failed") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="vg_sum",
        user_id="u1",
        domain="video_gen",
        task_type="vimax",
        status=status,
        progress=100 if status == "ready" else 40,
        retry_count=0,
        result=json.dumps(result, ensure_ascii=False),
        payload=json.dumps({"script": "x"}, ensure_ascii=False),
    )


def _seed_storyboard(tmp_path, frames: int) -> Path:
    task_dir = tmp_path / "u1" / "tasks" / "vg_sum"
    task_dir.mkdir(parents=True)
    path = task_dir / "storyboard.json"
    path.write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "vimax", "width": 1080, "height": 1920},
                "frames": [{"index": i, "narration": f"第{i}句", "duration": 2.0} for i in range(frames)],
                "total_duration": 238.06,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_detail_prefers_disk_frames_over_result_snapshot(tmp_path):
    _seed_storyboard(tmp_path, frames=12)
    task = _task({"storyboard": {"title": "t", "frames": [{"index": i} for i in range(8)]}})

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1", include_storyboard_summary=True)

    assert brief.frame_count == 12
    assert brief.total_duration == 238.06
    assert brief.shot_count == 0
    assert brief.shots_ready is False


def test_ready_task_reports_rendered_shot_count(tmp_path):
    _seed_storyboard(tmp_path, frames=12)
    shots = {str(i): {"prompt": f"p{i}"} for i in range(8)}
    task = _task({"storyboard": {"title": "t", "frames": []}, "vimax_shots": shots}, status="ready")

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1", include_storyboard_summary=True)

    assert brief.frame_count == 12
    assert brief.shot_count == 8
    assert brief.shots_ready is True


def test_failed_task_with_manifest_keeps_redo_available(tmp_path):
    _seed_storyboard(tmp_path, frames=12)
    shots = {str(i): {"prompt": f"p{i}"} for i in range(8)}
    task = _task({"storyboard": {"title": "t", "frames": []}, "vimax_shots": shots}, status="failed")

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1", include_storyboard_summary=True)

    assert brief.shot_count == 8
    assert brief.shots_ready is True, "manifest 可用性不得由 failed 状态否掉"


def test_disk_missing_falls_back_to_result_storyboard_frames(tmp_path):
    task = _task({"storyboard": {"title": "t", "frames": [{"index": i} for i in range(8)]}}, status="failed")

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1", include_storyboard_summary=True)

    assert brief.frame_count == 8
    assert brief.total_duration is None


def test_list_path_does_not_read_storyboard(tmp_path, monkeypatch):
    _seed_storyboard(tmp_path, frames=12)
    task = _task({"storyboard": {"title": "t", "frames": []}})
    calls: list[str] = []
    original = Path.read_text

    def _counting_read_text(self, *args, **kwargs):
        calls.append(str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _counting_read_text)

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1")

    assert brief.frame_count is None
    assert not any(path.endswith("storyboard.json") for path in calls), f"列表路径不得读盘：{calls}"

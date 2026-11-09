"""E4: 转场音画漂移 sidecar 提取 + router 回传 av_drift_warning。"""

import json
from unittest.mock import MagicMock


def test_extract_av_drift_reads_sidecar(tmp_path):
    """_extract_av_drift 读 av_drift.json → dict；缺失 → None。"""
    from app.gateway.video_gen.service import _extract_av_drift

    assert _extract_av_drift(tmp_path) is None  # 无 sidecar

    (tmp_path / "av_drift.json").write_text(
        json.dumps({"video_dur": 10.0, "audio_dur": 12.0, "drift_sec": 2.0, "transition_count": 3}),
        encoding="utf-8",
    )
    data = _extract_av_drift(tmp_path)
    assert data is not None
    assert data["drift_sec"] == 2.0
    assert data["transition_count"] == 3


def test_task_with_thumbnail_surfaces_av_drift_warning(tmp_path):
    """router _task_with_thumbnail：sidecar drift>0.5s → result['av_drift_warning']。"""
    from app.gateway.routers.video_gen import _task_with_thumbnail

    task_dir = tmp_path / "u1" / "tasks" / "vg_drift1"
    task_dir.mkdir(parents=True)
    (task_dir / "av_drift.json").write_text(
        json.dumps({"video_dur": 10.0, "audio_dur": 11.8, "drift_sec": 1.8, "transition_count": 4}),
        encoding="utf-8",
    )

    task = MagicMock()
    task.amount_source = None  # 2026-08-27 字段：mock 未跟上过不了 str 校验（同 test_thumbnail 修法）
    task.id = "vg_drift1"
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_image"
    task.status = "ready"
    task.progress = 100
    task.error_message = None
    task.retry_count = 0
    task.title = None
    task.created_at = None
    task.updated_at = None
    task.thumbnail_url = None
    task.cost = None
    task.currency = None
    task.payload = "{}"
    task.result = "{}"

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1")
    warn = brief.result.get("av_drift_warning")
    assert warn is not None, "drift>0.5s 须回传 av_drift_warning"
    assert "4" in warn and "1.8" in warn, f"提示应含转场数与漂移秒数：{warn}"


def test_task_with_thumbnail_no_warning_when_no_sidecar(tmp_path):
    """无 sidecar 时不回传 av_drift_warning（不误报）。"""
    from app.gateway.routers.video_gen import _task_with_thumbnail

    task_dir = tmp_path / "u1" / "tasks" / "vg_clean1"
    task_dir.mkdir(parents=True)

    task = MagicMock()
    task.amount_source = None  # 2026-08-27 字段：mock 未跟上过不了 str 校验（同 test_thumbnail 修法）
    task.id = "vg_clean1"
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_image"
    task.status = "ready"
    task.progress = 100
    task.error_message = None
    task.retry_count = 0
    task.title = None
    task.created_at = None
    task.updated_at = None
    task.thumbnail_url = None
    task.cost = None
    task.currency = None
    task.payload = "{}"
    task.result = "{}"

    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="u1")
    assert brief.result.get("av_drift_warning") is None

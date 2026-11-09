"""Task 3: result 含 storyboard.title 时质量告警不再被 NameError 吞掉（修前 :58 仅 else
分支赋值 payload，:80 在 if 路径使用 → NameError 被 except Exception 吞）。"""

from __future__ import annotations

import json
import types

from app.gateway.routers.video_gen import _task_with_thumbnail


def _fake_task(tmp_path, result: dict):
    # TaskDetail 必填字段（user_id/domain/task_type/status/progress）补齐，否则
    # model_validate 在到达被测分支前就 ValidationError。
    # status 用真实终态 "ready"（2026-08-19 前是臆造的 "completed"——告警通道
    # 统一终态门控后非终态不附加，本文件既有用例的意图均与 status 无关）。
    return types.SimpleNamespace(
        id="vg_warn1",
        user_id="default",
        domain="video_gen",
        task_type="ai_video",
        status="ready",
        progress=100,
        result=json.dumps(result, ensure_ascii=False),
        payload=json.dumps({"topic": "t", "target_duration": 60}, ensure_ascii=False),
    )


def test_coverage_warning_survives_storyboard_title(tmp_path):
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True)
    (task_dir / "visual_coverage.json").write_text(
        json.dumps({"coverage": 0.5, "succeeded": 1, "placeholder": 1, "total": 2}),
        encoding="utf-8",
    )
    # 关键：result 带 storyboard.title → 走 if 分支（修前 payload 未定义 → NameError）
    task = _fake_task(tmp_path, {"storyboard": {"title": "我的视频", "frames": []}})
    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="default")
    assert brief.result.get("ai_video_coverage_warning"), "coverage 告警被吞"
    assert brief.title == "我的视频"


def test_voice_mode_warning_merged_from_marker_file(tmp_path):
    """T16（2026-07-27）：engine 写 voice_mode_warning.txt → 任务详情 result 透出
    （ViMax 角色表解析失败/缺失回退单音色旁白，用户此前无感知）。"""
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True)
    (task_dir / "voice_mode_warning.txt").write_text("未生成角色表，本片为单音色旁白（如需多角色对话请检查编剧阶段）", encoding="utf-8")
    task = _fake_task(tmp_path, {"storyboard": {"title": "t", "frames": []}})
    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="default")
    assert "单音色旁白" in (brief.result.get("voice_mode_warning") or "")


def _ph_task(tmp_path, status: str):
    task = _fake_task(tmp_path, {"storyboard": {"title": "t", "frames": []}})
    task.status = status
    return task


def _write_ph(tmp_path, indices: list[int], total: int):
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "placeholder_frames.json").write_text(json.dumps({"indices": indices, "total": total}), encoding="utf-8")


def test_placeholder_warning_surfaces_on_ready(tmp_path):
    """2026-07-29：全帧黑场曾以 status=ready 静默交付——终态必须读出告警。"""
    _write_ph(tmp_path, [0, 2], total=3)
    brief = _task_with_thumbnail(_ph_task(tmp_path, "ready"), storage_dir=str(tmp_path), user_id="default")
    warn = brief.result.get("placeholder_warning") or ""
    assert "帧 1、3 " in warn, "帧号须 1-based，且用中文顿号连接而非 list repr"
    assert "[" not in warn, "用户文案里不该出现 Python list 的方括号"
    assert "2/3" in warn
    assert "重新生成" in warn


def test_placeholder_warning_not_read_on_hot_path(tmp_path):
    """非终态（processing）不读 sidecar——本函数是详情页 2 秒轮询的下游。"""
    _write_ph(tmp_path, [0], total=3)
    brief = _task_with_thumbnail(_ph_task(tmp_path, "processing"), storage_dir=str(tmp_path), user_id="default")
    assert brief.result.get("placeholder_warning") is None


def test_placeholder_warning_absent_without_sidecar(tmp_path):
    (tmp_path / "default" / "tasks" / "vg_warn1").mkdir(parents=True)
    brief = _task_with_thumbnail(_ph_task(tmp_path, "ready"), storage_dir=str(tmp_path), user_id="default")
    assert brief.result.get("placeholder_warning") is None


def test_placeholder_channel_is_isolated_from_other_warnings(tmp_path):
    """占位通道独立 try：sidecar 是坏 JSON 也不得吞掉既有 voice_mode_warning。"""
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True)
    (task_dir / "placeholder_frames.json").write_text("{not json", encoding="utf-8")
    (task_dir / "voice_mode_warning.txt").write_text("单音色旁白", encoding="utf-8")
    brief = _task_with_thumbnail(_ph_task(tmp_path, "ready"), storage_dir=str(tmp_path), user_id="default")
    assert "单音色旁白" in (brief.result.get("voice_mode_warning") or "")
    assert brief.result.get("placeholder_warning") is None


def test_coverage_warning_not_attached_while_running(tmp_path):
    """2026-08-19（反馈2-2 P1a）：全部告警通道统一终态门控——修前仅 placeholder/
    degradation 两通道有门控，coverage 等七通道任意状态从 sidecar 附加 → 自动重试
    running 态读上一轮残留 sidecar（vg_91899fe68c3b：7/27 成功率警告在 running 态
    透出，用户点「重新合成」→ 409）。半成品告警本就不该在非终态可见。"""
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True)
    (task_dir / "visual_coverage.json").write_text(
        json.dumps({"coverage": 0.26, "succeeded": 7, "placeholder": 20, "total": 27}),
        encoding="utf-8",
    )
    task = _fake_task(tmp_path, {"storyboard": {"title": "t", "frames": []}})
    task.status = "running"
    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="default")
    assert not brief.result.get("ai_video_coverage_warning"), "running 态不得附加 coverage 告警"


def test_coverage_warning_attached_on_ready(tmp_path):
    task_dir = tmp_path / "default" / "tasks" / "vg_warn1"
    task_dir.mkdir(parents=True)
    (task_dir / "visual_coverage.json").write_text(
        json.dumps({"coverage": 0.26, "succeeded": 7, "placeholder": 20, "total": 27}),
        encoding="utf-8",
    )
    task = _fake_task(tmp_path, {"storyboard": {"title": "t", "frames": []}})
    task.status = "ready"
    brief = _task_with_thumbnail(task, storage_dir=str(tmp_path), user_id="default")
    assert "7/27" in (brief.result.get("ai_video_coverage_warning") or "")

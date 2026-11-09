"""P2-6: 渲染前拦截占位/空旁白（忘改则 TTS 逐字念出"（新分镜，请编辑旁白）"）。

占位文本与 storyboard.tsx addFrame 的 narration 初值一致。
"""

import json
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.service import VideoGenService, _validate_narrations_renderable


def test_placeholder_narration_rejected():
    with pytest.raises(ValueError, match="占位"):
        _validate_narrations_renderable([{"index": 2, "narration": "（新分镜，请编辑旁白）"}])


def test_empty_narration_rejected():
    with pytest.raises(ValueError, match="旁白为空"):
        _validate_narrations_renderable([{"index": 1, "narration": "  "}])


def test_normal_narrations_pass():
    _validate_narrations_renderable([{"index": 0, "narration": "正常旁白"}])


def test_mixed_bad_frames_listed_in_message():
    """多个坏帧 → 错误信息列出全部帧号。"""
    frames = [
        {"index": 0, "narration": "正常"},
        {"index": 1, "narration": "  "},
        {"index": 2, "narration": "（新分镜，请编辑旁白）"},
    ]
    with pytest.raises(ValueError) as exc:
        _validate_narrations_renderable(frames)
    assert "1" in str(exc.value) and "2" in str(exc.value)


@pytest.mark.asyncio
async def test_resume_draft_blocked_by_placeholder(tmp_path):
    """resume_draft 在派发前校验——占位旁白 → ValueError（不进渲染）。"""
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"frames": [{"index": 0, "narration": "（新分镜，请编辑旁白）"}]}),
        encoding="utf-8",
    )
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)

    async def _async(v):
        return v

    svc.repo = SimpleNamespace(
        get=lambda *a, **k: _async(SimpleNamespace(status="draft", payload="{}", task_type="ai_image")),
        update=lambda *a, **k: _async(None),
    )

    with pytest.raises(ValueError, match="占位"):
        await svc.resume_draft("t1", "u1")


@pytest.mark.asyncio
async def test_re_render_task_blocked_by_placeholder(tmp_path):
    """re_render_task 也走 _render_from_draft→TTS，同样须拦占位旁白（P2-6 gap 闭环）。"""
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"frames": [{"index": 0, "narration": "（新分镜，请编辑旁白）"}]}),
        encoding="utf-8",
    )
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)

    async def _async(v):
        return v

    svc.repo = SimpleNamespace(
        get=lambda *a, **k: _async(SimpleNamespace(status="failed", payload="{}", task_type="ai_image")),
        update=lambda *a, **k: _async(None),
    )

    with pytest.raises(ValueError, match="占位"):
        await svc.re_render_task("t1", "u1")

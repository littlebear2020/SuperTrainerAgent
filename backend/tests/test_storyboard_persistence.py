"""Tests for Storyboard serialization (to_dict / from_dict)."""

import json
import tempfile
from pathlib import Path

from app.gateway.video_gen.storyboard import (
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
)


def _make_storyboard() -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    frames = [
        StoryboardFrame(index=0, narration="第一句话", search_term="ocean waves"),
        StoryboardFrame(index=1, narration="第二句话", search_term="sunset beach"),
    ]
    return Storyboard(title="测试视频", config=cfg, frames=frames, total_duration=0.0)


def test_to_dict_and_from_dict_roundtrip():
    sb = _make_storyboard()
    d = sb.to_dict()

    assert d["title"] == "测试视频"
    assert len(d["frames"]) == 2
    assert d["frames"][0]["narration"] == "第一句话"
    assert d["frames"][0]["search_term"] == "ocean waves"
    assert d["config"]["video_type"] == "ai_image"

    sb2 = Storyboard.from_dict(d)
    assert sb2.title == sb.title
    assert sb2.config.video_type == sb.config.video_type
    assert len(sb2.frames) == len(sb.frames)
    assert sb2.frames[0].narration == sb.frames[0].narration
    assert sb2.frames[0].search_term == sb.frames[0].search_term


def test_json_file_roundtrip():
    sb = _make_storyboard()
    d = sb.to_dict()

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        path = Path(f.name)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    sb2 = Storyboard.from_dict(loaded)
    assert sb2.title == "测试视频"
    assert len(sb2.frames) == 2


def test_modify_frame_and_rewrite():
    sb = _make_storyboard()
    d = sb.to_dict()

    # Modify frame 1's narration
    d["frames"][1]["narration"] = "修改后的旁白"

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        path = Path(f.name)

    sb2 = Storyboard.from_dict(json.loads(path.read_text(encoding="utf-8")))
    assert sb2.frames[1].narration == "修改后的旁白"
    # Frame 0 unchanged
    assert sb2.frames[0].narration == "第一句话"


def test_from_dict_missing_total_duration_defaults_to_zero():
    d = {
        "title": "test",
        "config": {"video_type": "ai_image", "width": 1920, "height": 1080},
        "frames": [{"index": 0, "narration": "hello"}],
    }
    sb = Storyboard.from_dict(d)
    assert sb.total_duration == 0.0
    assert len(sb.frames) == 1


# ── remotion_course 独立管线 T1（2026-09-10 计划）：layout_content 字段 ──


def test_layout_content_roundtrip():
    """layout_content 持久化往返：asdict 白名单自动序列化，from_dict 重建。"""
    sb = _make_storyboard()
    sb.frames[0].layout = "ConceptPage"
    sb.frames[0].layout_content = {"title": "核心概念", "bullets": ["要点一", "要点二"]}
    d = sb.to_dict()
    assert d["frames"][0]["layout_content"] == {"title": "核心概念", "bullets": ["要点一", "要点二"]}

    sb2 = Storyboard.from_dict(d)
    assert sb2.frames[0].layout_content == {"title": "核心概念", "bullets": ["要点一", "要点二"]}
    assert sb2.frames[1].layout_content is None


def test_layout_content_old_storyboard_defaults_to_none():
    """存量 storyboard.json 无该键 → from_dict 得 None（旧任务零迁移兼容）。"""
    d = {
        "title": "legacy",
        "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
        "frames": [{"index": 0, "narration": "旧任务帧", "layout": "ConceptPage"}],
    }
    sb = Storyboard.from_dict(d)
    assert sb.frames[0].layout_content is None
    assert sb.frames[0].layout == "ConceptPage"

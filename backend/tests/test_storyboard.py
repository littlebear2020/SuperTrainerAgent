from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_frame_defaults():
    f = StoryboardFrame(index=0, narration="你好世界")
    assert f.search_term is None
    assert f.visual_url is None
    assert f.duration == 0.0


def test_storyboard_total_duration_sum():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(title="测试", config=cfg)
    sb.frames.append(StoryboardFrame(index=0, narration="a", duration=3.0))
    sb.frames.append(StoryboardFrame(index=1, narration="b", duration=2.5))
    sb.recompute_duration()
    assert sb.total_duration == 5.5


def test_config_defaults():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    assert cfg.fps == 30
    assert cfg.voice_id == "preset:146"
    assert cfg.subtitle_enabled is True
    assert cfg.subtitle_style == "default"


def test_config_subtitle_style():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920, subtitle_style="bold")
    assert cfg.subtitle_style == "bold"


def test_frame_srt_default():
    f = StoryboardFrame(index=0, narration="hello")
    assert f.srt == ""


def test_config_transition_defaults():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    assert cfg.transition == "none"  # E（2026-08-31 转场清理）：默认硬切
    assert cfg.transition_duration == 0.5


def test_config_transition_custom():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920, transition="slide_left", transition_duration=1.0)
    assert cfg.transition == "slide_left"
    assert cfg.transition_duration == 1.0


def test_config_bgm_defaults():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    assert cfg.bgm_path is None
    assert cfg.bgm_volume == 0.15


def test_from_dict_roundtrip():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="测试",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="你好", duration=3.0)],
    )
    sb.recompute_duration()
    data = sb.to_dict()
    sb2 = Storyboard.from_dict(data)
    assert sb2.title == "测试"
    assert sb2.config.video_type == "ai_image"
    assert sb2.frames[0].narration == "你好"
    assert sb2.total_duration == 3.0


def test_from_dict_ignores_unknown_fields():
    """from_dict should not crash when data has extra fields (forward compatibility)."""
    data = {
        "title": "test",
        "config": {
            "video_type": "ai_image",
            "width": 1080,
            "height": 1920,
            "future_field": "ignored",
        },
        "frames": [
            {"index": 0, "narration": "a", "future_frame_field": "ignored"},
        ],
        "total_duration": 5.0,
        "extra_top_level": True,
    }
    sb = Storyboard.from_dict(data)
    assert sb.title == "test"
    assert sb.frames[0].narration == "a"
    assert sb.total_duration == 5.0


def test_global_anchor_roundtrip():
    """2026-08-19：global_anchor 持久化——to_dict 落盘、from_dict 读回（重生端点/
    兜底链风格守卫的消费方）。"""
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(title="t", config=cfg)
    anchor = {"anchor_card": "全片为科技公司真人实拍场景", "style_string": "写实商务实拍", "graphic_layout_card": "深蓝配色"}
    sb.global_anchor = anchor
    data = sb.to_dict()
    assert data["global_anchor"] == anchor, "to_dict 落盘锚定卡"
    sb2 = Storyboard.from_dict(data)
    assert sb2.global_anchor == anchor, "from_dict 读回锚定卡"


def test_global_anchor_backward_compat_missing_key():
    """旧 storyboard.json 无 global_anchor 键 → from_dict 默认 None（不炸）。"""
    data = {
        "title": "test",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "a"}],
    }
    sb = Storyboard.from_dict(data)
    assert sb.global_anchor is None


# ---------------------------------------------------------------------------
# SceneGroup
# ---------------------------------------------------------------------------


def test_scene_group_defaults():
    from app.gateway.video_gen.storyboard import SceneGroup

    g = SceneGroup(index=0, title="开场", frame_indices=[0, 1, 2])
    assert g.index == 0
    assert g.title == "开场"
    assert g.frame_indices == [0, 1, 2]
    assert g.group_duration == 0.0
    assert g.visual_source_override is None


def test_storyboard_with_scene_groups():
    from app.gateway.video_gen.storyboard import SceneGroup

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="课程",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", duration=3.0),
            StoryboardFrame(index=1, narration="b", duration=2.0),
        ],
        scene_groups=[
            SceneGroup(index=0, title="第一段", frame_indices=[0, 1]),
        ],
    )
    assert len(sb.scene_groups) == 1
    assert sb.scene_groups[0].title == "第一段"


def test_storyboard_scene_groups_default_empty():
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(title="t", config=cfg)
    assert sb.scene_groups == []


def test_storyboard_to_dict_includes_scene_groups():
    from app.gateway.video_gen.storyboard import SceneGroup

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="a")],
        scene_groups=[SceneGroup(index=0, title="g1", frame_indices=[0])],
    )
    data = sb.to_dict()
    assert "scene_groups" in data
    assert data["scene_groups"][0]["title"] == "g1"


def test_storyboard_from_dict_loads_scene_groups():
    data = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "a"}],
        "total_duration": 0.0,
        "scene_groups": [{"index": 0, "title": "g1", "frame_indices": [0]}],
    }
    sb = Storyboard.from_dict(data)
    assert len(sb.scene_groups) == 1
    assert sb.scene_groups[0].title == "g1"


def test_storyboard_from_dict_backward_compat_no_scene_groups():
    """Old storyboard.json without scene_groups should load fine."""
    data = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "a"}],
        "total_duration": 0.0,
    }
    sb = Storyboard.from_dict(data)
    assert sb.scene_groups == []


def test_scene_group_transition_defaults():
    from app.gateway.video_gen.storyboard import SceneGroup

    g = SceneGroup(index=0, title="段落", frame_indices=[0])
    # E（2026-08-31 转场清理）：dataclass 默认 fade → none（转场 UI 已删，默认硬切；
    # 显式 fade 仍可设置——W1 只对 ai_image/ai_video 强制压 none）
    assert g.transition_in == "none"
    assert g.transition_out == "none"
    assert g.transition_duration == 0.8


def test_scene_group_transition_custom():
    from app.gateway.video_gen.storyboard import SceneGroup

    g = SceneGroup(index=0, title="段落", frame_indices=[0], transition_in="dissolve", transition_out="zoom_in", transition_duration=1.2)
    assert g.transition_in == "dissolve"
    assert g.transition_out == "zoom_in"
    assert g.transition_duration == 1.2


def test_speech_duration_roundtrip():
    """speech_duration（真实 TTS 语音时长，字幕配比基准）随 to_dict/from_dict 完整往返。"""
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="x", duration=8.0, speech_duration=3.0)])
    d = sb.to_dict()
    assert d["frames"][0]["speech_duration"] == 3.0
    sb2 = Storyboard.from_dict(d)
    assert sb2.frames[0].speech_duration == 3.0


def test_speech_duration_defaults_none_for_legacy_storyboards():
    """旧草稿 JSON 无 speech_duration 键 → from_dict 得 None（字幕退回 duration 配比，无回归）。"""
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="x", duration=5.0)])
    d = sb.to_dict()
    d["frames"][0].pop("speech_duration", None)  # 模拟修复前的旧草稿
    sb2 = Storyboard.from_dict(d)
    assert sb2.frames[0].speech_duration is None

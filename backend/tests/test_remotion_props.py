"""Tests for Remotion props builder and StoryboardResult."""

from app.gateway.video_gen.service import _write_storyboard_srt
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_storyboard(n_frames=3, fps=30, width=1080, height=1920, style=None):
    cfg = StoryboardConfig(video_type="remotion", width=width, height=height, fps=fps, style=style)
    frames = [
        StoryboardFrame(
            index=i,
            narration=f"第{i + 1}段旁白",
            audio_path=f"/tmp/task/audio/frame_{i:03d}.mp3",
            duration=5.0,
        )
        for i in range(n_frames)
    ]
    sb = Storyboard(title="测试视频", config=cfg, frames=frames)
    sb.recompute_duration()
    return sb


def test_build_props_structure():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard()
    props = build_remotion_props(sb)

    assert "title" in props
    assert "fps" in props
    assert "width" in props
    assert "height" in props
    assert "totalDurationFrames" in props
    assert "style" in props
    assert "scenes" in props
    assert "subtitleSegments" in props
    assert props["title"] == "测试视频"
    assert props["fps"] == 30
    assert props["width"] == 1080
    assert props["height"] == 1920


def test_scene_duration_from_config_fps():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(fps=30)
    props = build_remotion_props(sb)
    assert props["scenes"][0]["durationInFrames"] == 150  # 5.0 * 30

    sb24 = _make_storyboard(fps=24)
    props24 = build_remotion_props(sb24)
    assert props24["scenes"][0]["durationInFrames"] == 120  # 5.0 * 24


def test_scene_audio_file_paths_posix():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard()
    props = build_remotion_props(sb)
    assert props["scenes"][0]["audioFile"] == "audio/frame_000.mp3"
    assert props["scenes"][1]["audioFile"] == "audio/frame_001.mp3"


def test_audio_file_paths_cross_platform():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    cfg = StoryboardConfig(video_type="remotion", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="test", duration=3.0, audio_path=r"C:\Users\test\audio\frame_000.mp3"),
        ],
    )
    props = build_remotion_props(sb)
    assert props["scenes"][0]["audioFile"] == "audio/frame_000.mp3"


def test_scene_audio_file_none_is_empty_string():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    cfg = StoryboardConfig(video_type="remotion", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="test", duration=3.0, audio_path=None),
        ],
    )
    props = build_remotion_props(sb)
    assert props["scenes"][0]["audioFile"] == ""


def test_subtitle_segments_parsed_from_srt(tmp_path):
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(n_frames=2, fps=30)
    srt_path = str(tmp_path / "test.srt")
    _write_storyboard_srt(sb, srt_path)

    props = build_remotion_props(sb, subtitle_path=srt_path)
    segs = props["subtitleSegments"]
    assert len(segs) == 2
    assert segs[0]["sceneIndex"] == 0
    assert segs[0]["startFrame"] == 0
    assert segs[0]["endFrame"] == 150  # 5.0s * 30fps
    # 2026-08-13 字幕引擎 D2 契约：CJK↔Latin 边界插空格（"第1段"→"第 1 段"）。
    # 钉死新契约精确输出——旧无空格格式在此断言下必红（评审 Minor #5）。
    assert "第 1 段旁白" in segs[0]["text"]
    assert segs[1]["sceneIndex"] == 1
    assert segs[1]["startFrame"] == 150
    assert segs[1]["endFrame"] == 300


def test_no_subtitle_returns_empty_segments():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard()
    props = build_remotion_props(sb, subtitle_path=None)
    assert props["subtitleSegments"] == []


def test_total_duration_frames():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(n_frames=3, fps=30)  # 3 frames * 5s = 15s
    props = build_remotion_props(sb)
    assert props["totalDurationFrames"] == 450  # 15.0 * 30


def test_style_passed_through():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(style="professional")
    props = build_remotion_props(sb)
    assert props["style"] == "professional"

    sb_default = _make_storyboard(style=None)
    props_default = build_remotion_props(sb_default)
    assert props_default["style"] is None


def test_transition_passed_through():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(fps=30)
    # Override transition in config
    sb.config.transition = "slide_left"
    sb.config.transition_duration = 0.8
    props = build_remotion_props(sb)
    assert props["transition"] == "slide_left"
    assert props["transitionDurationFrames"] == round(0.8 * 30)  # 24


def test_transition_default_in_props():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(fps=30)
    props = build_remotion_props(sb)
    # 前端已删转场控件并不再下发 transition 键——缺省即硬切（"none"），
    # 见 storyboard.py StoryboardConfig.transition 注释。旧断言 "fade" 是过期期望。
    assert props["transition"] == "none"
    assert props["transitionDurationFrames"] == round(0.5 * 30)  # 15


def test_bgm_disabled_when_no_path():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(fps=30)
    props = build_remotion_props(sb)
    assert props["bgmEnabled"] is False
    assert props["bgmVolume"] == 0.15


def test_bgm_enabled_when_path_set():
    from app.gateway.video_gen.pipelines.remotion_props import build_remotion_props

    sb = _make_storyboard(fps=30)
    sb.config.bgm_path = "/tmp/bgm.mp3"
    props = build_remotion_props(sb)
    assert props["bgmEnabled"] is True

"""Tests for ffmpeg helpers: per-transition duration + colorgrade."""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import (  # noqa: E402
    _escape_subtitle_filter_path,
    _ffmpeg_colorgrade,
    _ffmpeg_concat_with_transitions,
)


def test_concat_accepts_transition_durations():
    """_ffmpeg_concat_with_transitions should accept transition_durations param."""
    sig = inspect.signature(_ffmpeg_concat_with_transitions)
    assert "transition_durations" in sig.parameters
    assert sig.parameters["transition_durations"].default is None


def test_escape_subtitle_path_quotes_spaces():
    """P0-6: single-quote wrapping protects [ ] , and space in filtergraph paths.
    Space is actually harmless (libavfilter does not treat it as separator),
    but quoting is defensive best practice."""
    assert _escape_subtitle_filter_path("/Users/me/trainer ai/x.srt") == "'/Users/me/trainer ai/x.srt'"
    # 无空格路径 -> 也包裹（一致 + 防 :）
    assert _escape_subtitle_filter_path("/a/b/c.srt") == "'/a/b/c.srt'"
    # 路径含单引号 -> '\'' 转义（闭合-转义-重开）
    assert _escape_subtitle_filter_path("/a/b'c.srt") == "'/a/b'\\''c.srt'"


def test_ffmpeg_colorgrade_exists():
    """_ffmpeg_colorgrade function should exist and accept 3 args."""
    sig = inspect.signature(_ffmpeg_colorgrade)
    assert "color_palette" in sig.parameters


def test_ffmpeg_colorgrade_noop_without_palette(tmp_path):
    """_ffmpeg_colorgrade should copy the segment when color_palette is None."""
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    out = tmp_path / "out.mp4"
    _ffmpeg_colorgrade(src, out, None)
    assert out.exists()
    assert out.read_bytes() == b"fake"


def test_mix_audio_missing_input_raises_clear_error(tmp_path):
    """Missing input -> a clear error naming the missing file (not 'audio mix failed')."""
    import pytest

    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    missing_video = tmp_path / "nope.mp4"
    missing_audio = tmp_path / "nope.mp3"
    output = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="input video missing"):
        _ffmpeg_mix_audio(missing_video, missing_audio, output)


def test_mix_audio_missing_audio_raises_clear_error(tmp_path):
    """Video present but audio missing -> 'input audio missing'."""
    import pytest

    from pipelines._ffmpeg_helpers import _ffmpeg_mix_audio

    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    missing_audio = tmp_path / "nope.mp3"
    output = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="input audio missing"):
        _ffmpeg_mix_audio(video, missing_audio, output)


def test_colorgrade_vf_no_hue_rotation_for_saturated_primary():
    """B: 饱和主色不再触发破坏性 hue 旋转（旧 hue=h=210 全色平移、肤色变蓝紫）。仅留 eq 安全调色。"""
    from pipelines._ffmpeg_helpers import _colorgrade_vf
    vf = _colorgrade_vf({"primary": "#2C3E50", "saturation": 1.2, "contrast": 1.1, "brightness": 0.0})
    assert vf is not None
    assert "hue=" not in vf
    assert "eq=contrast=1.1:brightness=0.0:saturation=1.2" in vf


def test_colorgrade_vf_none_when_noop():
    """全默认值（contrast=1/brightness=0/saturation=1）-> None（不调色）。"""
    from pipelines._ffmpeg_helpers import _colorgrade_vf
    assert _colorgrade_vf(None) is None
    assert _colorgrade_vf({"primary": "#2C3E50", "saturation": 1.0, "contrast": 1.0, "brightness": 0.0}) is None


def test_colorgrade_vf_grayscale_primary_no_hue():
    """灰度主色本就不出 hue；确认仍只走 eq（hue colorgrade 已删，仅 eq）。"""
    from pipelines._ffmpeg_helpers import _colorgrade_vf
    vf = _colorgrade_vf({"primary": "#808080", "saturation": 1.3})
    assert vf is not None and "hue=" not in vf and "eq=" in vf


# ---------------------------------------------------------------------------
# Task 2: 共享 finalizer 原语（_build_fade_vf / _subtitle_force_style /
# _burn_subtitles / _apply_colorgrade_and_subtitles / _apply_intro_outro_fade）
# ---------------------------------------------------------------------------


def test_subtitle_force_style_default():
    from pipelines._ffmpeg_helpers import _subtitle_force_style
    assert "FontSize=24" in _subtitle_force_style("default")
    assert _subtitle_force_style("bold").startswith("FontSize=32")


def test_apply_colorgrade_and_subtitles_skipped_when_no_srt(tmp_path):
    """无 subtitle_path -> 写 subtitle_status=skipped，返回原视频。"""
    import json

    from pipelines._ffmpeg_helpers import _apply_colorgrade_and_subtitles
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    out = _apply_colorgrade_and_subtitles(src, task_dir=tmp_path, tmp_dir=tmp_path, color_palette=None, subtitle_path=None)
    assert out == src
    assert json.loads((tmp_path / "subtitle_status.json").read_text())["status"] == "skipped"


def test_apply_intro_outro_fade_short_video_passthrough(tmp_path):
    """≤1s 视频不 fade，返回原路径。"""
    from pipelines._ffmpeg_helpers import _apply_intro_outro_fade
    import pipelines._ffmpeg_helpers as h
    src = tmp_path / "short.mp4"
    src.write_bytes(b"x")
    # _probe_duration 会抛（假 mp4）-> except 分支返回原视频
    out = _apply_intro_outro_fade(src, tmp_dir=tmp_path)
    assert out == src


def test_subtitle_filter_vf_helper():
    """#2: 合并路径共用的 _subtitle_filter_vf 须构造正确 subtitles= 串（DRY）。"""
    from pipelines._ffmpeg_helpers import _subtitle_filter_vf
    vf = _subtitle_filter_vf(Path("/a/b.srt"))
    assert "subtitles=" in vf and "'/a/b.srt'" in vf  # 路径转义包裹（单引号包路径）
    vf2 = _subtitle_filter_vf(Path("/a/b.srt"), "FontSize=24")
    assert "force_style=" in vf2


def test_apply_intro_outro_fade_includes_audio_afade():
    """#1: fade 须含音频 afade（不只画面 -vf），不再 -c:a copy 致片尾静音。"""
    import inspect
    from pipelines._ffmpeg_helpers import _apply_intro_outro_fade, _build_fade_af
    assert "afade" in inspect.getsource(_apply_intro_outro_fade)
    assert "afade" in _build_fade_af(60.0)


def test_polish_timeout_scales_with_duration(monkeypatch):
    """#2: _polish_timeout 须按时长动态放大（长字节视频不固定 120s 超时）。"""
    import pipelines._ffmpeg_helpers as h
    monkeypatch.setattr(h, "_probe_duration", lambda p: 600.0)  # 10min 视频
    t = h._polish_timeout(Path("/x.mp4"))
    assert t > 120, "长视频须放大超时（旧固定 120 会超时）"


def test_apply_intro_outro_fade_logs_on_failure(tmp_path, monkeypatch):
    """B1: ffmpeg returncode!=0 须 log warning（修前静默返原视频，不可诊断）。"""
    import logging
    from pipelines._ffmpeg_helpers import _apply_intro_outro_fade
    import pipelines._ffmpeg_helpers as h
    src = tmp_path / "v.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(h, "_probe_duration", lambda p: 60.0)  # >1s 触发 fade

    class FakeProc:
        returncode = 1  # 无音轨跑 -af afade 失败
        stderr = b"no audio stream"

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: FakeProc())
    records = []

    class H(logging.Handler):
        def emit(self, r):
            records.append(r)

    handler = H()
    h.logger.addHandler(handler)
    try:
        out = _apply_intro_outro_fade(src, tmp_dir=tmp_path)
        assert out == src  # 失败返原视频（保 A/V）
        assert any("fade" in r.getMessage().lower() for r in records), "须 log fade 失败"
    finally:
        h.logger.removeHandler(handler)


def test_hex_to_hue_deg_removed():
    """B4: _hex_to_hue_deg 是删 hue colorgrade 后的死代码（无生产 caller），应已删。"""
    import pipelines._ffmpeg_helpers as h
    assert not hasattr(h, "_hex_to_hue_deg"), "_hex_to_hue_deg 死代码应已删（hue colorgrade 已移除）"


def test_write_av_drift_sidecar_writes_when_drifting(tmp_path, monkeypatch):
    """E4: 转场启用且音频比视频长 >0.5s -> 写 av_drift.json（含 drift_sec/transition_count）。"""
    import json

    import pipelines._ffmpeg_helpers as h
    from pipelines._ffmpeg_helpers import _write_av_drift_sidecar

    durations = {"video": 10.0, "audio": 11.5}  # drift = 1.5s

    def fake_probe(path):
        name = Path(path).name
        return durations["audio"] if name.startswith("audio") else durations["video"]

    monkeypatch.setattr(h, "_ffprobe_duration", fake_probe)

    video = tmp_path / "concat_video.mp4"
    video.write_bytes(b"mp4")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    data = _write_av_drift_sidecar(video, audio, tmp_path, transition_count=3)
    assert data is not None, "drift>0.5s 须写 sidecar"
    sidecar = json.loads((tmp_path / "av_drift.json").read_text())
    assert sidecar["drift_sec"] == 1.5
    assert sidecar["transition_count"] == 3
    assert sidecar["video_dur"] == 10.0
    assert sidecar["audio_dur"] == 11.5


def test_write_av_drift_sidecar_skips_when_below_threshold(tmp_path, monkeypatch):
    """E4: drift ≤ 0.5s（无转场或转场很短）-> 不写 sidecar。"""
    import pipelines._ffmpeg_helpers as h
    from pipelines._ffmpeg_helpers import _write_av_drift_sidecar

    def fake_probe(path):
        name = Path(path).name
        return 10.2 if name.startswith("audio") else 10.0  # drift 0.2s

    monkeypatch.setattr(h, "_ffprobe_duration", fake_probe)

    video = tmp_path / "concat_video.mp4"
    video.write_bytes(b"mp4")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    data = _write_av_drift_sidecar(video, audio, tmp_path, transition_count=2)
    assert data is None
    assert not (tmp_path / "av_drift.json").exists()


def test_write_av_drift_sidecar_skips_when_no_transitions(tmp_path, monkeypatch):
    """E4: transition_count=0（无转场）-> 不写 sidecar（即使 probe 不可靠也不误报）。"""
    import pipelines._ffmpeg_helpers as h
    from pipelines._ffmpeg_helpers import _write_av_drift_sidecar

    video = tmp_path / "concat_video.mp4"
    video.write_bytes(b"mp4")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    data = _write_av_drift_sidecar(video, audio, tmp_path, transition_count=0)
    assert data is None
    assert not (tmp_path / "av_drift.json").exists()

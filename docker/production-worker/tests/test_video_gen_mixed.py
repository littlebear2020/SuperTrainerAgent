"""Tests for video_gen_mixed scene_groups transition support."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_build_transition_durations():
    from pipelines.video_gen_mixed import _build_transition_durations

    groups = [
        {"index": 0, "frame_indices": [0, 1, 2], "transition_duration": 0.8},
        {"index": 1, "frame_indices": [3, 4, 5, 6], "transition_duration": 1.2},
        {"index": 2, "frame_indices": [7, 8, 9]},
    ]
    tds = _build_transition_durations(10, groups, default_td=0.5)
    assert len(tds) == 9
    assert tds[2] == 0.8  # group boundary (last transition of group 0)
    assert tds[6] == 1.2  # group boundary (last transition of group 1)
    assert tds[0] == 0.5  # intra-group


def test_build_transition_durations_no_groups():
    from pipelines.video_gen_mixed import _build_transition_durations

    tds = _build_transition_durations(5, [], default_td=0.5)
    assert len(tds) == 4
    assert all(td == 0.5 for td in tds)


# ---- _burn_subtitles: failure handling ----


def _fake_proc(returncode: int, stderr: bytes = b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stderr = stderr
    return proc


def test_burn_subtitles_failure_returns_false(tmp_path):
    """ffmpeg failure -> return False, no output, no exception."""
    from pipelines._ffmpeg_helpers import _burn_subtitles

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n")
    out = tmp_path / "out.mp4"

    with patch("pipelines._ffmpeg_helpers.subprocess.run", return_value=_fake_proc(1, b"filter err")), \
         patch("pipelines._ffmpeg_helpers._subtitles_filter_available", return_value=True):  # F1: 走 ffmpeg 路径
        ok, err = _burn_subtitles(video, srt, out)

    assert ok is False
    assert not out.exists()


def test_burn_subtitles_success_returns_true(tmp_path):
    """ffmpeg success (rc=0 + output exists) -> return True."""
    from pipelines._ffmpeg_helpers import _burn_subtitles

    video = tmp_path / "in.mp4"
    video.write_bytes(b"v")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n")
    out = tmp_path / "out.mp4"

    def fake_run(*a, **k):
        out.write_bytes(b"subtitled")
        return _fake_proc(0)

    with patch("pipelines._ffmpeg_helpers.subprocess.run", side_effect=fake_run), \
         patch("pipelines._ffmpeg_helpers._subtitles_filter_available", return_value=True):  # F1: 走 ffmpeg 路径
        ok, err = _burn_subtitles(video, srt, out)

    assert ok is True
    assert out.exists()


def test_compose_mixed_subtitle_burn_failure_skips_subs(tmp_path):
    """Burn failure -> compose continues from concat_video instead of crashing."""
    from pipelines.video_gen_mixed import compose_mixed

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(2):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"seg")

    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    payload = {
        "frames": [
            {"narration": "f0", "duration": 3.0, "visual_source": "remotion"},
            {"narration": "f1", "duration": 3.0, "visual_source": "remotion"},
        ],
        "scene_groups": [],
        "config": {"transition": "none", "width": 1080, "height": 1920},
        "quality": "standard",
        "output_format": "mp4",
        "subtitle_path": str(srt),
        "audio_concat_path": str(audio),
    }

    mix_video_arg = []

    def mock_concat(segments, output, **kwargs):
        output.write_bytes(b"concat")

    def mock_mix(video, audio_in, output, **kwargs):
        mix_video_arg.append(video)
        output.write_bytes(b"mixed")

    def mock_convert(video, output, **kwargs):
        output.write_bytes(b"final")
        return output

    with patch("pipelines.video_gen_mixed._ffmpeg_concat", side_effect=mock_concat), \
         patch("pipelines._ffmpeg_helpers._burn_subtitles", return_value=(False, '')), \
         patch("pipelines.video_gen_mixed._ffmpeg_mix_audio", side_effect=mock_mix), \
         patch("pipelines.video_gen_mixed._ffmpeg_convert_format", side_effect=mock_convert), \
         patch("pipelines.video_gen_mixed._ffmpeg_extract_thumbnail"):
        result = compose_mixed(tmp_path, payload, {}, lambda v: None)

    assert result.exists()
    # Burn failed -> mix_audio must use concat_video, not the non-existent subtitled.mp4
    assert mix_video_arg, "mix_audio should have been called"
    assert mix_video_arg[0].name == "concat_video.mp4"


def test_mixed_writes_subtitle_status(tmp_path, monkeypatch):
    """A: mixed 接入 finalizer 后写 subtitle_status.json（之前从不写）。

    预置 cached seg 跳过 _download_asset/_image_to_video；不桩 finalizer，
    让真实 _apply_colorgrade_and_subtitles(color_palette=None, subtitle_path=None)
    跑——它仅写 subtitle_status=skipped，不触 ffmpeg。"""
    import json
    import pipelines.video_gen_mixed as m
    segs = tmp_path / "segments"
    segs.mkdir()
    (segs / "seg_000.mp4").write_bytes(b"mp4")  # cached → 跳过帧准备
    # 注：dirty-detection（无 manifest→dirty）会删此 cached seg 再走帧准备分支；
    # 桩 _download_asset/_image_to_video 以兑现"跳过下载/转码"意图（不触网/ffmpeg），
    # 不桩 finalizer（让其真实跑）。
    monkeypatch.setattr(m, "_download_asset", lambda url, stem: stem)
    monkeypatch.setattr(m, "_image_to_video", lambda img, out, dur, w, h, frame_index=0: out.write_bytes(b"mp4"))  # Task 11: +frame_index
    monkeypatch.setattr(m, "_ffmpeg_concat", lambda segs_list, out, **k: out.write_bytes(b"mp4"))
    monkeypatch.setattr(m, "_ffmpeg_convert_format", lambda inp, out, **k: out)
    monkeypatch.setattr(m, "_finalize_output", lambda r, fr: r)
    monkeypatch.setattr(m, "_ffmpeg_extract_thumbnail", lambda *a, **k: True)
    monkeypatch.setattr(m, "_ffmpeg_mix_audio", lambda *a, **k: None)
    monkeypatch.setattr(m, "_ffmpeg_mix_bgm_with_ducking", lambda *a, **k: None)
    monkeypatch.setattr(m, "_save_manifest", lambda *a, **k: None)
    frames = [{"index": 0, "duration": 3.0, "visual_url": "http://x/x.jpg", "visual_type": "image"}]
    payload = {"frames": frames, "config": {"width": 1080, "height": 1920, "transition": "none"}, "subtitle_path": None}
    m.compose_mixed(tmp_path, payload, {}, lambda p: None)
    assert (tmp_path / "subtitle_status.json").exists()
    assert json.loads((tmp_path / "subtitle_status.json").read_text())["status"] == "skipped"

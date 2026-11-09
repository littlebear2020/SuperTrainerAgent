"""Phase E2: full render 下载失败 → 黑场+旁白占位（不挂、不静默黑帧）。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import video_gen_ai_visual as av  # noqa: E402


def test_download_failure_uses_placeholder_not_raise(tmp_path):
    """E2: full render 时某镜 _download_asset raise → 该镜生成黑场占位，compose 继续。"""
    payload = {
        "frames": [
            {"index": 0, "visual_url": "http://x/0.mp4", "visual_type": "video", "duration": 3.0, "narration": "镜0"},
            {"index": 1, "visual_url": "http://x/bad.mp4", "visual_type": "video", "duration": 3.0, "narration": "失败镜"},
        ],
        "config": {"width": 1080, "height": 1920, "video_type": "ai_video"},
        "subtitle_path": None,
        "audio_concat_path": None,
    }
    calls = {"placeholder": 0, "download_ok": 0}

    def _fake_download(url, dest):
        if "bad" in url:
            raise RuntimeError("download failed")
        Path(dest).write_bytes(b"x")
        calls["download_ok"] += 1

    def _fake_black(out, duration, text, w, h):
        Path(out).write_bytes(b"black")
        calls["placeholder"] += 1

    def _fake_extend(clip, target, out):
        Path(out).write_bytes(b"x")
        return out

    def _fake_normalize(clip, out, w, h, *, max_duration=None):
        Path(out).write_bytes(b"x")
        return out

    with patch.object(av, "_download_asset", side_effect=_fake_download), \
         patch.object(av, "_make_black_subtitle_clip", side_effect=_fake_black), \
         patch.object(av, "_ffmpeg_extend_clip_to_duration", side_effect=_fake_extend), \
         patch.object(av, "normalize_clip_resolution", side_effect=_fake_normalize), \
         patch.object(av, "_probe_duration", return_value=5.0), \
         patch.object(av, "_ffmpeg_concat"), \
         patch.object(av, "_apply_colorgrade_and_subtitles", side_effect=lambda c, **k: c), \
         patch.object(av, "_apply_intro_outro_fade", side_effect=lambda c, **k: c), \
         patch.object(av, "_ffmpeg_convert_format", side_effect=lambda c, out, **k: out), \
         patch.object(av, "_finalize_output", side_effect=lambda r, fr: fr):
        # 镜1 下载失败 → 占位；compose 不 raise
        av.compose_ai_visual(tmp_path, payload, {}, lambda n: None)

    assert calls["placeholder"] == 1, "失败镜应走黑场占位"
    assert calls["download_ok"] == 1, "成功镜应正常下载"


def test_all_frames_download_success_no_placeholder(tmp_path):
    """E2: 全部下载成功 → 不触发任何占位。"""
    payload = {
        "frames": [
            {"index": 0, "visual_url": "http://x/0.mp4", "visual_type": "video", "duration": 3.0, "narration": "n0"},
        ],
        "config": {"width": 1080, "height": 1920, "video_type": "ai_video"},
        "subtitle_path": None,
        "audio_concat_path": None,
    }
    placeholder_calls = {"n": 0}

    def _fake_download(url, dest):
        Path(dest).write_bytes(b"x")

    def _fake_black(out, duration, text, w, h):
        placeholder_calls["n"] += 1

    def _fake_extend(clip, target, out):
        Path(out).write_bytes(b"x")
        return out

    def _fake_normalize(clip, out, w, h, *, max_duration=None):
        Path(out).write_bytes(b"x")
        return out

    with patch.object(av, "_download_asset", side_effect=_fake_download), \
         patch.object(av, "_make_black_subtitle_clip", side_effect=_fake_black), \
         patch.object(av, "_ffmpeg_extend_clip_to_duration", side_effect=_fake_extend), \
         patch.object(av, "normalize_clip_resolution", side_effect=_fake_normalize), \
         patch.object(av, "_probe_duration", return_value=5.0), \
         patch.object(av, "_ffmpeg_concat"), \
         patch.object(av, "_apply_colorgrade_and_subtitles", side_effect=lambda c, **k: c), \
         patch.object(av, "_apply_intro_outro_fade", side_effect=lambda c, **k: c), \
         patch.object(av, "_ffmpeg_convert_format", side_effect=lambda c, out, **k: out), \
         patch.object(av, "_finalize_output", side_effect=lambda r, fr: fr):
        av.compose_ai_visual(tmp_path, payload, {}, lambda n: None)

    assert placeholder_calls["n"] == 0

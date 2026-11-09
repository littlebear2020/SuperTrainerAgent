"""Phase D2: local_path video asset 也走时长补齐 + 分辨率归一（D1 前置）。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import video_gen_ai_visual as av  # noqa: E402


def test_local_video_shorter_than_duration_gets_extended(tmp_path):
    """D2: local_path 的 8s video、frame.duration=18 → 应调用 extend（修 D1 后音画失同步）。"""
    task_dir = tmp_path
    (task_dir / "visuals").mkdir()
    local_rel = "visuals/shot_0000.mp4"
    (task_dir / local_rel).write_bytes(b"x" * 2048)

    payload = {
        "frames": [
            {"index": 0, "visual_url": None, "local_visual_path": local_rel,
             "visual_type": "video", "duration": 18.0, "narration": "n0"},
        ],
        "config": {"width": 1080, "height": 1920, "video_type": "ai_video"},
        "subtitle_path": None,
        "audio_concat_path": None,
    }
    calls = {"extend": 0, "normalize": 0}

    def _fake_extend(clip, target, out, **kwargs):
        calls["extend"] += 1
        Path(out).write_bytes(b"x")
        return Path(out)

    def _fake_normalize(clip, out, w, h, *, max_duration=None):
        calls["normalize"] += 1
        Path(out).write_bytes(b"x")
        return Path(out)

    with patch.object(av, "_ffmpeg_extend_clip_to_duration", side_effect=_fake_extend), \
         patch.object(av, "normalize_clip_resolution", side_effect=_fake_normalize), \
         patch.object(av, "_probe_duration", return_value=8.0), \
         patch.object(av, "_ffmpeg_concat"), \
         patch.object(av, "_apply_colorgrade_and_subtitles", side_effect=lambda c, **k: c), \
         patch.object(av, "_apply_intro_outro_fade", side_effect=lambda c, **k: c), \
         patch.object(av, "_ffmpeg_convert_format", side_effect=lambda c, out, **k: out), \
         patch.object(av, "_ffmpeg_extract_thumbnail", return_value=True), \
         patch.object(av, "_finalize_output", side_effect=lambda r, fr: fr):
        av.compose_ai_visual(task_dir, payload, {}, lambda n: None)

    assert calls["extend"] == 1, "local_path 短 video 应循环补齐到 frame.duration"

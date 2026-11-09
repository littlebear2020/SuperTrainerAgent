"""H6: v1 必须尊重 gateway W1 的 transition=none（xfade 重叠使视频<音频→音画漂移，
vg_93e83a7ee259 实证）；显式转场时写 av_drift sidecar（对齐 mixed/segmented）。"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipelines.video_gen_ai_visual as mod  # noqa: E402


def _payload(n=3, **over):
    frames = [
        {
            "index": i,
            "visual_url": f"http://cdn/{i}.png",
            "visual_type": "image",
            "duration": 2.0,
            "narration": f"n{i}",
        }
        for i in range(n)
    ]
    p = {
        "frames": frames,
        "config": {"width": 320, "height": 240, "transition": "none"},
        "subtitle_path": None,
        "audio_concat_path": None,
        "quality": "draft",
        "output_format": "mp4",
    }
    p.update(over)
    return p


def _patch_tail():
    def fake_passthrough(current, **k):
        return current

    def fake_convert(current, final, **k):
        Path(final).write_bytes(b"out")
        return Path(final)

    return (
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, **k: Path(out).write_bytes(b"concat")),
        patch.object(mod, "_ffmpeg_concat_with_transitions", side_effect=lambda segs, out, **k: Path(out).write_bytes(b"concat")),
        patch.object(mod, "_apply_colorgrade_and_subtitles", side_effect=fake_passthrough),
        patch.object(mod, "_apply_intro_outro_fade", side_effect=fake_passthrough),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=fake_convert),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: Path(f).write_bytes(b"out")),
        patch.object(mod, "_ffmpeg_extract_thumbnail"),
        patch.object(mod, "_probe_black_scenes", return_value=[]),
    )


def _fake_prepare(asset, source_dir, tmp_dir, width, height, quality):
    p = tmp_dir / f"prep_{asset['frame_index']:03d}.mp4"
    p.write_bytes(b"new")
    return p


# 全量分支 image 帧走 _download_asset + _image_to_video（不经 _download_and_prepare），
# 一并 patch 掉避免真实网络重试/真实 ffmpeg（同 test_incremental_recompose 全量回退测试）。
def _patch_full_render_image():
    return (
        patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")),
        patch.object(mod, "_image_to_video", side_effect=lambda src, duration, out, *a, **k: Path(out).write_bytes(b"seg")),
    )


def test_no_auto_fade_when_transition_none(tmp_path):
    """多 scene_group + transition=none → 不得走 xfade（修复前 auto-fade 强加）。"""
    patches = _patch_tail()
    fri = _patch_full_render_image()
    with patches[0] as m_flat, patches[1] as m_trans, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
         patch.object(mod, "_download_and_prepare", side_effect=_fake_prepare), \
         fri[0], fri[1]:
        mod.compose_ai_visual(
            tmp_path,
            _payload(scene_groups=[{"start_index": 0}, {"start_index": 2}]),
            {},
            lambda p: None,
        )
    m_trans.assert_not_called()   # 修复前：auto-fade 走 transitions
    m_flat.assert_called_once()


def test_explicit_transition_still_works(tmp_path):
    """显式 fade → 仍走 xfade（守护不过矫）。"""
    (tmp_path / "audio.mp3").write_bytes(b"a")
    payload = _payload(config={"width": 320, "height": 240, "transition": "fade"})
    patches = _patch_tail()
    fri = _patch_full_render_image()
    with patches[0], patches[1] as m_trans, patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
         patch.object(mod, "_download_and_prepare", side_effect=_fake_prepare), \
         fri[0], fri[1]:
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)
    m_trans.assert_called_once()


def test_av_drift_sidecar_written_with_transitions(tmp_path):
    """显式转场 + 有音频 → 写 av_drift sidecar（修复前 v1 无 sidecar，漂移不可见）。"""
    (tmp_path / "audio.mp3").write_bytes(b"a")
    payload = _payload(
        config={"width": 320, "height": 240, "transition": "fade"},
        audio_concat_path=str(tmp_path / "audio.mp3"),
    )
    patches = _patch_tail()
    fri = _patch_full_render_image()
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], \
         patch.object(mod, "_download_and_prepare", side_effect=_fake_prepare), \
         fri[0], fri[1], \
         patch.object(mod, "_write_av_drift_sidecar") as m_sidecar, \
         patch.object(mod, "_ffmpeg_mix_audio", side_effect=lambda v, a, o: Path(o).write_bytes(b"m")):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)
    m_sidecar.assert_called_once()

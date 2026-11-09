"""P1-1: 本地图片素材必须经 _image_to_video 转视频，不得直接 copy 改名 mp4。

修复前：compose_ai_visual local 分支对 visual_type="image" 直接 copy2 成
seg_NNN.mp4（JPEG 改名 mp4）→ concat demuxer 流不兼容 → 合成崩/坏片。
这就是「上传图片替换帧 → 重新合成」必然失败的根因（F3）。
"""
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipelines.video_gen_ai_visual as mod  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _payload_with_local_image(tmp_path: Path) -> dict:
    img = tmp_path / "frame_000.png"
    img.write_bytes(b"\x89PNG" + b"0" * 600)  # 存在即可（_image_to_video 被 patch）
    return {
        "frames": [
            {
                "index": 0,
                "local_visual_path": "frame_000.png",
                "visual_url": None,
                "visual_type": "image",
                "duration": 2.0,
                "narration": "测试旁白",
            }
        ],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "subtitle_path": None,
        "audio_concat_path": None,
        "quality": "draft",
        "output_format": "mp4",
    }


def test_local_image_goes_through_image_to_video(tmp_path):
    payload = _payload_with_local_image(tmp_path)

    def fake_i2v(image_path, duration, output, *a, **k):
        Path(output).write_bytes(b"seg-mp4")

    def fake_convert(current, final, **k):
        Path(final).write_bytes(b"out")
        return Path(final)

    def fake_finalize(result, final):
        Path(final).write_bytes(b"out")

    def fake_passthrough(current, **k):
        return current

    with (
        patch.object(mod, "_image_to_video", side_effect=fake_i2v) as m_i2v,
        patch.object(mod, "_ffmpeg_concat") as m_concat,
        patch.object(mod, "_apply_colorgrade_and_subtitles", side_effect=fake_passthrough),
        patch.object(mod, "_apply_intro_outro_fade", side_effect=fake_passthrough),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=fake_convert),
        patch.object(mod, "_finalize_output", side_effect=fake_finalize),
        patch.object(mod, "_ffmpeg_extract_thumbnail"),
        patch.object(mod, "_probe_black_scenes", return_value=[]),
    ):
        m_concat.side_effect = lambda segs, out, **k: Path(out).write_bytes(b"concat")
        out = mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    m_i2v.assert_called_once()  # 修复前 0 次（直接 copy2）
    # copy2 不应再出现在 local image 路径：segment 来自 _image_to_video 产物
    assert out.exists()


@pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="ffmpeg/ffprobe not installed")
def test_compose_with_local_image_produces_valid_mp4(tmp_path):
    """端到端真跑 ffmpeg：本地图片帧 → 成片时长 ≈ 音频时长（修复前 concat 崩或 ~0.04s 坏片）。"""
    from pipelines.video_gen_ai_visual import compose_ai_visual

    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1:r=1", "-frames:v", "1", str(tmp_path / "frame_000.png")],
        check=True, capture_output=True,
    )
    subprocess.run(
        [FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2", "-q:a", "9", str(tmp_path / "audio.mp3")],
        check=True, capture_output=True,
    )
    (tmp_path / "sub.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n测试\n\n", encoding="utf-8")

    payload = {
        "frames": [
            {
                "index": 0,
                "local_visual_path": "frame_000.png",
                "visual_url": None,
                "visual_type": "image",
                "duration": 2.0,
                "narration": "测试",
            }
        ],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "subtitle_path": str(tmp_path / "sub.srt"),
        "audio_concat_path": str(tmp_path / "audio.mp3"),
        "quality": "draft",
        "output_format": "mp4",
    }
    out = compose_ai_visual(tmp_path, payload, {}, lambda p: None)
    assert out.exists()
    # 关键断言：segment 本身必须是 ≈duration 的视频（修复前 = PNG 改名 mp4 ≈0.04s）。
    # 注意不能断言成片时长——mix_audio 容器级末帧定格（不加 -shortest）会把短段补成音频长度，掩盖问题。
    seg = tmp_path / "segments" / "seg_000.mp4"
    assert seg.exists()
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(seg)],
        capture_output=True, text=True,
    )
    assert float(r.stdout.strip()) >= 1.5  # 修复前：~0.04s（image2 单帧）


# ── R1: image_reuse 帧必须按 image 处理（评审问题1）─────────────────────────


def _payload_with_image_reuse(tmp_path: Path, *, with_local: bool) -> dict:
    if with_local:
        (tmp_path / "img_0000.png").write_bytes(b"\x89PNG" + b"0" * 600)
    frame0 = {
        "index": 0,
        "local_visual_path": "img_0000.png" if with_local else None,
        "visual_url": None if with_local else "http://cdn/0.png",
        "visual_type": "image",
        "duration": 2.0,
        "narration": "成功帧",
    }
    frame1 = {
        "index": 1,
        "local_visual_path": "img_0000.png" if with_local else None,  # 复用帧共享上帧本地文件
        "visual_url": None if with_local else "http://cdn/0.png",     # 复用帧共享上帧 URL
        "visual_type": "image_reuse",
        "duration": 3.0,
        "narration": "复用帧",
    }
    return {
        "frames": [frame0, frame1],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "subtitle_path": None,
        "audio_concat_path": None,
        "quality": "draft",
        "output_format": "mp4",
    }


def _tail_patches(segs_store):
    def fake_passthrough(current, **k):
        return current

    def fake_convert(current, final, **k):
        Path(final).write_bytes(b"out")
        return Path(final)

    return (
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, **k: (segs_store.append([str(s) for s in segs]), Path(out).write_bytes(b"concat"))[1]),
        patch.object(mod, "_apply_colorgrade_and_subtitles", side_effect=fake_passthrough),
        patch.object(mod, "_apply_intro_outro_fade", side_effect=fake_passthrough),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=fake_convert),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: Path(f).write_bytes(b"out")),
        patch.object(mod, "_ffmpeg_extract_thumbnail"),
        patch.object(mod, "_probe_black_scenes", return_value=[]),
    )


def test_image_reuse_with_local_path_goes_through_image_to_video(tmp_path):
    """R1：image_reuse + local_path 必须走 _image_to_video——修复前 fall-through
    到 `return Path(str(src))`，png 被 move 成 seg_NNN.mp4（concat 崩 + 源文件被移走）。"""
    payload = _payload_with_image_reuse(tmp_path, with_local=True)
    i2v_calls = []

    def fake_i2v(image_path, duration, output, *a, **k):
        i2v_calls.append((str(image_path), duration))
        Path(output).write_bytes(b"seg")

    segs = []
    patches = _tail_patches(segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    # 两帧都应走 _image_to_video（帧0 image + 帧1 image_reuse）
    assert len(i2v_calls) == 2
    assert (tmp_path / "img_0000.png").exists()  # 源文件不得被 move 销毁


def test_image_reuse_with_url_goes_through_download_image_branch(tmp_path):
    """R1：image_reuse + 仅 URL 必须走下载+image 分支——修复前落入 video 分支，
    png 被 probe/extend 后产出 0.04s 坏段。"""
    payload = _payload_with_image_reuse(tmp_path, with_local=False)
    i2v_calls = []

    def fake_i2v(image_path, duration, output, *a, **k):
        i2v_calls.append((str(image_path), duration))
        Path(output).write_bytes(b"seg")

    segs = []
    patches = _tail_patches(segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    assert len(i2v_calls) == 2  # 两帧都走 image 分支（而非 video 的 normalize 路径）


def test_prepare_local_segment_raises_on_unknown_type(tmp_path):
    """T1：未知 local 资产类型显式 raise（R1 防 fall-through 的兜底分支）。"""
    import pytest as _pytest

    (tmp_path / "x.gif").write_bytes(b"GIF89a" + b"0" * 600)
    asset = {
        "frame_index": 0,
        "local_path": "x.gif",
        "type": "gif",  # 非 image/video
        "duration": 2.0,
    }
    with _pytest.raises(ValueError, match="unsupported local asset type"):
        mod._prepare_local_segment(asset, tmp_path, tmp_path, 320, 240, "draft")

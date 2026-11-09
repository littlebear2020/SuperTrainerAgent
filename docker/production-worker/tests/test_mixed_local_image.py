"""M2: v2_mixed 本地图片帧必须走 _image_to_video（F3 只修了 v1）——
_prepare_segment_from_file 是视频专用（无 -loop 1），图片输入产 0.04s 坏段；
且其 ffmpeg rc 不查 → 静默缺段（后续帧音画错位）。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipelines.video_gen_mixed as m  # noqa: E402


def test_prepare_segment_from_file_raises_on_ffmpeg_failure(tmp_path, monkeypatch):
    """ffmpeg 失败 → RuntimeError（修复前 rc 不查，静默产坏段/缺段）。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"v")
    monkeypatch.setattr(m, "_ffprobe_duration", lambda p: 10.0)  # 10s ≥ 2s → 不走 extend
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: MagicMock(returncode=1, stderr=b"boom"))

    with pytest.raises(RuntimeError, match="segment-from-file"):
        m._prepare_segment_from_file(src, tmp_path / "out.mp4", 2.0, 320, 240)


def test_local_image_routed_to_image_to_video(tmp_path, monkeypatch):
    """local_visual_path 图片（visual_type=image）→ _image_to_video，
    不得进 _prepare_segment_from_file（视频专用，无 -loop 1 → 0.04s 坏段）。"""
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_000.jpg").write_bytes(b"\xff\xd8\xff\xe0img")
    payload = {
        "frames": [
            {
                "index": 0,
                "visual_url": None,
                "visual_type": "image",
                "local_visual_path": "visuals/frame_000.jpg",
                "duration": 2.0,
                "narration": "n0",
                "visual_source": "ai_image",
            },
        ],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "quality": "draft",
        "output_format": "mp4",
    }
    called = {"i2v": [], "psf": []}

    def fake_i2v(image_path, output, duration, width, height, frame_index=0):
        # Task 11（2026-08-03）：新增 frame_index —— Ken Burns 方向交替需要帧序号。
        # 顺带记录它，锁住"调用点真的传了"（不传则全片同向，交替静默失效）。
        called["i2v"].append(str(image_path))
        called.setdefault("frame_index", []).append(frame_index)
        output.write_bytes(b"seg")

    def fake_psf(src, output, duration, width, height):
        called["psf"].append(str(src))
        output.write_bytes(b"seg")

    monkeypatch.setattr(m, "_image_to_video", fake_i2v)
    monkeypatch.setattr(m, "_prepare_segment_from_file", fake_psf)
    # patch 下游（concat → 输出），无音频跳过 mix
    monkeypatch.setattr(m, "_ffmpeg_concat", lambda segs, out, **k: Path(out).write_bytes(b"c"))
    monkeypatch.setattr(m, "_apply_colorgrade_and_subtitles", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_apply_intro_outro_fade", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_ffmpeg_convert_format", lambda cur, out, **k: Path(out))
    monkeypatch.setattr(m, "_finalize_output", lambda r, f: Path(f).write_bytes(b"o"))
    monkeypatch.setattr(m, "_ffmpeg_extract_thumbnail", lambda *a, **k: None)

    m.compose_mixed(tmp_path, payload, {}, lambda p: None)

    assert called["i2v"] == [str(tmp_path / "visuals" / "frame_000.jpg")]
    assert called["psf"] == []  # 修复前：图片帧被送进 _prepare_segment_from_file
    # Task 11：帧序号必须真的传下去，否则 Ken Burns 方向交替全片同向
    assert called["frame_index"] == [0]


def test_local_image_reuse_also_routed_to_image_to_video(tmp_path, monkeypatch):
    """image_reuse（R1 同义）在 v2 也按图片路由——v1 的归一化没有覆盖 v2。"""
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "img_0000.png").write_bytes(b"\x89PNGimg")
    payload = {
        "frames": [
            {
                "index": 0,
                "visual_url": None,
                "visual_type": "image_reuse",
                "local_visual_path": "visuals/img_0000.png",
                "duration": 2.0,
                "narration": "n0",
                "visual_source": "ai_image",
            },
        ],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "quality": "draft",
        "output_format": "mp4",
    }
    called = {"i2v": [], "psf": []}

    def fake_i2v(image_path, output, duration, width, height, frame_index=0):
        # Task 11（2026-08-03）：新增 frame_index —— Ken Burns 方向交替需要帧序号。
        # 顺带记录它，锁住"调用点真的传了"（不传则全片同向，交替静默失效）。
        called["i2v"].append(str(image_path))
        called.setdefault("frame_index", []).append(frame_index)
        output.write_bytes(b"seg")

    def fake_psf(src, output, duration, width, height):
        called["psf"].append(str(src))
        output.write_bytes(b"seg")

    monkeypatch.setattr(m, "_image_to_video", fake_i2v)
    monkeypatch.setattr(m, "_prepare_segment_from_file", fake_psf)
    monkeypatch.setattr(m, "_ffmpeg_concat", lambda segs, out, **k: Path(out).write_bytes(b"c"))
    monkeypatch.setattr(m, "_apply_colorgrade_and_subtitles", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_apply_intro_outro_fade", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_ffmpeg_convert_format", lambda cur, out, **k: Path(out))
    monkeypatch.setattr(m, "_finalize_output", lambda r, f: Path(f).write_bytes(b"o"))
    monkeypatch.setattr(m, "_ffmpeg_extract_thumbnail", lambda *a, **k: None)

    m.compose_mixed(tmp_path, payload, {}, lambda p: None)

    assert called["i2v"] == [str(tmp_path / "visuals" / "img_0000.png")]
    assert called["psf"] == []


def test_remote_image_reuse_routed_to_image_to_video(tmp_path, monkeypatch):
    """image_reuse 帧只剩远程 URL（本地化失败，D1 兜底保留 URL）→ 必须走 _image_to_video。

    Task 13 只覆盖了 local 分支；URL 分支漏 image_reuse → 误入视频专用
    _prepare_segment_from_file（无 -loop 1）产 0.04s 坏段。
    """
    payload = {
        "frames": [
            {
                "index": 0,
                "visual_url": "http://cdn/reuse.png",
                "visual_type": "image_reuse",
                "local_visual_path": None,
                "duration": 2.0,
                "narration": "n0",
                "visual_source": "ai_image",
            },
        ],
        "config": {"width": 320, "height": 240, "transition": "none"},
        "quality": "draft",
        "output_format": "mp4",
    }
    called = {"i2v": [], "psf": []}

    def fake_download(url, dest_stem):
        p = Path(f"{dest_stem}.png")
        p.write_bytes(b"img")
        return p

    def fake_i2v(image_path, output, duration, width, height, frame_index=0):
        # Task 11（2026-08-03）：新增 frame_index —— Ken Burns 方向交替需要帧序号。
        # 顺带记录它，锁住"调用点真的传了"（不传则全片同向，交替静默失效）。
        called["i2v"].append(str(image_path))
        called.setdefault("frame_index", []).append(frame_index)
        output.write_bytes(b"seg")

    def fake_psf(src, output, duration, width, height):
        called["psf"].append(str(src))
        output.write_bytes(b"seg")

    monkeypatch.setattr(m, "_download_asset", fake_download)
    monkeypatch.setattr(m, "_image_to_video", fake_i2v)
    monkeypatch.setattr(m, "_prepare_segment_from_file", fake_psf)
    monkeypatch.setattr(m, "_ffmpeg_concat", lambda segs, out, **k: Path(out).write_bytes(b"c"))
    monkeypatch.setattr(m, "_apply_colorgrade_and_subtitles", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_apply_intro_outro_fade", lambda cur, **k: cur)
    monkeypatch.setattr(m, "_ffmpeg_convert_format", lambda cur, out, **k: Path(out))
    monkeypatch.setattr(m, "_finalize_output", lambda r, f: Path(f).write_bytes(b"o"))
    monkeypatch.setattr(m, "_ffmpeg_extract_thumbnail", lambda *a, **k: None)

    m.compose_mixed(tmp_path, payload, {}, lambda p: None)

    assert called["psf"] == []  # 修复前：image_reuse 误入视频路径
    assert len(called["i2v"]) == 1

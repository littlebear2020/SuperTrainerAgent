"""P0-2 latent: v3_segmented 本地图片必须走 _image_to_video（-loop 1 拉满时长）。

_prepare_segment_from_file 是视频专用（无 -loop 1）：PNG 输入 ffmpeg 只读 1 帧
→ 0.033s 坏段 → concat 不足 1s → tpad 定格整个音频（v2_mixed 同病已实测）。
v3 的 local 分支与 v2 修复前完全同构——_prepare_local_visual 统一分流。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipelines.video_gen_segmented as seg  # noqa: E402


def test_local_image_routed_to_image_to_video(tmp_path, monkeypatch):
    """visual_type=image 或图片扩展名 → _image_to_video，不得进 _prepare_segment_from_file。"""
    calls = []
    monkeypatch.setattr(seg, "_image_to_video", lambda *a, **k: calls.append(("i2v", a[0])))
    monkeypatch.setattr(seg, "_prepare_segment_from_file", lambda *a, **k: calls.append(("psf", a[0])))

    src = tmp_path / "img_0000.png"
    src.write_bytes(b"\x89PNG")
    seg._prepare_local_visual(src, "image", tmp_path / "seg_000.mp4", 3.0, 320, 240)
    # 扩展名兜底（visual_type 缺失时）
    seg._prepare_local_visual(src, None, tmp_path / "seg_001.mp4", 3.0, 320, 240)

    assert [c[0] for c in calls] == ["i2v", "i2v"], "本地图片必须走 _image_to_video"


def test_local_video_routed_to_prepare_segment(tmp_path, monkeypatch):
    """本地视频（上传 mp4）→ 仍走 _prepare_segment_from_file。"""
    calls = []
    monkeypatch.setattr(seg, "_image_to_video", lambda *a, **k: calls.append(("i2v", a[0])))
    monkeypatch.setattr(seg, "_prepare_segment_from_file", lambda *a, **k: calls.append(("psf", a[0])))

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"v")
    seg._prepare_local_visual(src, "video", tmp_path / "seg_000.mp4", 3.0, 320, 240)

    assert [c[0] for c in calls] == ["psf"], "本地视频仍走 _prepare_segment_from_file"


def test_local_image_jpg_also_routed(tmp_path, monkeypatch):
    """jpg/jpeg/webp 扩展名同样分流到 _image_to_video。"""
    calls = []
    monkeypatch.setattr(seg, "_image_to_video", lambda *a, **k: calls.append(("i2v", a[0])))
    monkeypatch.setattr(seg, "_prepare_segment_from_file", lambda *a, **k: calls.append(("psf", a[0])))

    for ext in (".jpg", ".jpeg", ".webp"):
        src = tmp_path / f"frame{ext}"
        src.write_bytes(b"x")
        seg._prepare_local_visual(src, None, tmp_path / f"seg{ext}.mp4", 3.0, 320, 240)

    assert [c[0] for c in calls] == ["i2v", "i2v", "i2v"]

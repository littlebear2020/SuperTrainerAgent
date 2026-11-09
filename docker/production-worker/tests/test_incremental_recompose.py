"""P1-3 worker 侧：增量分支只重处理 changed_frames；防护不满足时回退全量。"""
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


def _patch_tail(m_concat_store):
    """patch concat 及之后所有下游（与 test_local_image_segment 同款）。"""
    def fake_passthrough(current, **k):
        return current

    def fake_convert(current, final, **k):
        Path(final).write_bytes(b"out")
        return Path(final)

    return (
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, **k: (m_concat_store.append(list(segs)), Path(out).write_bytes(b"concat"))[1]),
        patch.object(mod, "_apply_colorgrade_and_subtitles", side_effect=fake_passthrough),
        patch.object(mod, "_apply_intro_outro_fade", side_effect=fake_passthrough),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=fake_convert),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: Path(f).write_bytes(b"out")),
        patch.object(mod, "_ffmpeg_extract_thumbnail"),
        patch.object(mod, "_probe_black_scenes", return_value=[]),
    )


def test_incremental_only_reprepares_changed(tmp_path):
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    concat_segs = []
    prepared = []

    def fake_prepare(asset, source_dir, tmp_dir, width, height, quality, **kw):
        prepared.append(asset["frame_index"])
        p = tmp_dir / f"prep_{asset['frame_index']:03d}.mp4"
        p.write_bytes(b"new")
        return p

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_and_prepare", side_effect=fake_prepare):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[1]),
            {},
            lambda p: None,
        )

    assert prepared == [1]  # 修复前（无增量接线）：全量 3 帧
    assert len(concat_segs[0]) == 3  # concat 仍全量（3 段）


def test_incremental_falls_back_to_full_on_count_mismatch(tmp_path):
    """缓存 segment 数 ≠ 帧数（增删帧/上次中断）→ 回退全量，防 stale 鬼片（F9 防护）。

    注意：全量分支的 image 帧走 `_download_asset` + `_image_to_video`（不经
    `_download_and_prepare`），所以跟踪这两个的调用。
    """
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(2):  # 只有 2 段缓存，但 payload 3 帧
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    concat_segs = []
    processed = []

    def fake_download(url, dest):
        dest.write_bytes(b"img")

    def fake_i2v(src, duration, out, *a, **k):
        # 从 source/shot_NNN.png 文件名解析帧号
        processed.append(int(Path(src).stem.split("_")[1]))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=fake_download), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[1]),
            {},
            lambda p: None,
        )

    assert sorted(processed) == [0, 1, 2]  # 全量路径逐帧处理


# ── B2/T4/T5: 增量分支补齐测试（评审轮3）──────────────────────────────────


def test_incremental_video_asset_gets_normalized(tmp_path):
    """B2：增量分支 video 资产在 _download_and_prepare 后必须归一分辨率——
    修复前直接 move，与全量分支（:474-475）不对称 → 拼接拉伸/流不兼容。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    payload = _payload(incremental=True, changed_frames=[1])
    payload["frames"][1]["visual_type"] = "video"
    payload["frames"][1]["visual_url"] = "http://cdn/1.mp4"

    concat_segs = []
    norm_calls = []

    def fake_prepare(asset, source_dir, tmp_dir, width, height, quality, **kw):
        p = tmp_dir / f"prep_{asset['frame_index']:03d}.mp4"
        p.write_bytes(b"new")
        return p

    def fake_norm(src, out, width, height, *, max_duration=None):
        norm_calls.append(str(src))
        Path(out).write_bytes(b"norm")
        return out

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_and_prepare", side_effect=fake_prepare), \
         patch.object(mod, "normalize_clip_resolution", side_effect=fake_norm):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    assert len(norm_calls) == 1  # 修复前 0 次：增量 video 未归一


def test_incremental_image_reuse_goes_through_image_to_video(tmp_path):
    """T4：增量路径的 image_reuse 帧同样归一为 image（R1 在增量分支生效）。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    payload = _payload(incremental=True, changed_frames=[1])
    payload["frames"][1]["visual_type"] = "image_reuse"

    concat_segs = []
    i2v_calls = []

    def fake_i2v(src, duration, out, *a, **k):
        i2v_calls.append(str(src))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    assert len(i2v_calls) == 1  # image_reuse 走 image 分支（修复前误入 video 分支）


def test_incremental_local_path_goes_through_prepare_local(tmp_path):
    """T5：增量路径的 local_path 帧走 _prepare_local_segment（图片经 _image_to_video）。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)
    (tmp_path / "frame_001.png").write_bytes(b"\x89PNG" + b"0" * 600)

    payload = _payload(incremental=True, changed_frames=[1])
    payload["frames"][1]["visual_url"] = None
    payload["frames"][1]["local_visual_path"] = "frame_001.png"

    concat_segs = []
    i2v_calls = []

    def fake_i2v(src, duration, out, *a, **k):
        i2v_calls.append(str(src))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    assert len(i2v_calls) == 1  # local 图片经 _image_to_video，非 copy2


def test_incremental_falls_back_to_full_when_changed_empty(tmp_path):
    """T2：changed_frames 为空 → 回退全量（防护一）。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    concat_segs = []
    processed = []

    def fake_i2v(src, duration, out, *a, **k):
        processed.append(int(Path(src).stem.split("_")[1]))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[]),
            {},
            lambda p: None,
        )

    assert sorted(processed) == [0, 1, 2]  # 全量


def test_incremental_falls_back_when_segment_zero_byte(tmp_path):
    """M5: 0 字节损坏段（磁盘满/进程被杀于写盘中途）→ 回退全量，不得喂 concat 崩任务。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)
    (segments_dir / "seg_001.mp4").write_bytes(b"")  # 损坏

    concat_segs = []
    processed = []

    def fake_i2v(src, duration, out, *a, **k):
        processed.append(int(Path(src).stem.split("_")[1]))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[1]),
            {},
            lambda p: None,
        )

    assert sorted(processed) == [0, 1, 2]  # 回退全量（修复前：数量校验通过 → 增量 → concat 崩）


def test_incremental_falls_back_when_segment_name_mismatch(tmp_path):
    """M5: 数量巧合一致但文件名错位（如 seg_000/001/003 缺 002 多 003）→ 回退全量。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for name in ("seg_000.mp4", "seg_001.mp4", "seg_003.mp4"):  # 3 个文件但集合错位
        (segments_dir / name).write_bytes(b"x" * 2048)

    concat_segs = []
    processed = []

    def fake_i2v(src, duration, out, *a, **k):
        processed.append(int(Path(src).stem.split("_")[1]))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[1]),
            {},
            lambda p: None,
        )

    assert sorted(processed) == [0, 1, 2]


def test_incremental_falls_back_to_full_when_index_out_of_range(tmp_path):
    """T3：changed_frames 索引越界（≥帧数）→ 过滤后为空 → 回退全量（防护三）。"""
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"x" * 2048)

    concat_segs = []
    processed = []

    def fake_i2v(src, duration, out, *a, **k):
        processed.append(int(Path(src).stem.split("_")[1]))
        Path(out).write_bytes(b"seg")

    patches = _patch_tail(concat_segs)
    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], \
         patch.object(mod, "_download_asset", side_effect=lambda url, dest: dest.write_bytes(b"img")), \
         patch.object(mod, "_image_to_video", side_effect=fake_i2v):
        mod.compose_ai_visual(
            tmp_path,
            _payload(incremental=True, changed_frames=[99]),
            {},
            lambda p: None,
        )

    assert sorted(processed) == [0, 1, 2]  # 全量

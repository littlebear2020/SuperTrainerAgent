"""HR-T1: Worker _collect_visual_assets 含 local_visual_path + 循环无索引错位。

修预存 bug：local-only 帧（visual_url=None + local_visual_path set）被
_collect_visual_assets 丢出 assets；全渲循环 `for i, asset in enumerate(assets)`
+ `frame = frames[i]` 用 assets 过滤后位置 i 索引原帧列表 frames → 错位，
local_path 查错位帧或丢失后续正常帧；增量循环 `asset = assets[idx]` 同错位。
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

WORKER_PIPE = Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines"


def _load_module():
    """加载 video_gen_ai_visual（含其依赖 _ffmpeg_helpers）。"""
    sys.modules.setdefault("pipelines", types.ModuleType("pipelines"))
    spec_h = importlib.util.spec_from_file_location("pipelines._ffmpeg_helpers", WORKER_PIPE / "_ffmpeg_helpers.py")
    mod_h = importlib.util.module_from_spec(spec_h)
    sys.modules["pipelines._ffmpeg_helpers"] = mod_h
    spec_h.loader.exec_module(mod_h)
    spec = importlib.util.spec_from_file_location("pipelines.video_gen_ai_visual", WORKER_PIPE / "video_gen_ai_visual.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pipelines.video_gen_ai_visual"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_collect_visual_assets_includes_local_only_frames():
    """local-only 帧（visual_url=None + local_visual_path）不应被丢，保留 frame_index。"""
    mod = _load_module()
    payload = {
        "frames": [
            {"visual_url": "http://a", "visual_type": "image", "duration": 5.0},
            {"visual_url": None, "local_visual_path": "visuals/f1.mp4", "visual_type": "video", "duration": 6.0},
            {"visual_url": "http://c", "visual_type": "image", "duration": 5.0},
        ]
    }
    assets = mod._collect_visual_assets(payload)
    assert len(assets) == 3  # 三帧都在，不丢中间 local-only 帧
    assert assets[0]["frame_index"] == 0
    assert assets[1]["frame_index"] == 1
    assert assets[1]["local_path"] == "visuals/f1.mp4"
    assert assets[1]["duration"] == 6.0
    assert assets[2]["frame_index"] == 2


def test_collect_visual_assets_drops_no_visual_frames():
    """P0-4: 既无 visual_url 也无 local_path 的失败帧不再丢——返回 placeholder 占位（保音画等长）。"""
    mod = _load_module()
    payload = {
        "frames": [
            {"visual_url": "http://a", "duration": 5.0},
            {"visual_url": None, "local_visual_path": None, "duration": 5.0},  # 失败帧
            {"visual_url": "http://c", "duration": 5.0},
        ]
    }
    assets = mod._collect_visual_assets(payload)
    assert len(assets) == 3  # P0-4: 不丢帧，失败帧作 placeholder
    assert assets[1]["placeholder"] is True
    assert assets[1]["url"] is None
    assert [a["frame_index"] for a in assets] == [0, 1, 2]


def test_full_render_uses_local_path_without_misalign(tmp_path):
    """混合帧全渲：local-only 帧用本地文件（不下载）、不读错位帧、segment 按原序拼接。"""
    mod = _load_module()
    # 准备 local 占位文件
    visuals = tmp_path / "visuals"
    visuals.mkdir()
    (visuals / "f1.mp4").write_bytes(b"placeholder-mp4")
    payload = {
        "frames": [
            {"visual_url": "http://a", "visual_type": "image", "duration": 5.0},
            {"visual_url": None, "local_visual_path": "visuals/f1.mp4", "visual_type": "video", "duration": 6.0},
            {"visual_url": "http://c", "visual_type": "image", "duration": 5.0},
        ],
        "config": {"width": 1280, "height": 720, "transition": "none", "subtitle_style": "default"},
        "quality": "standard",
    }
    captured = {"downloads": []}

    def fake_download(url, dest):
        Path(dest).write_bytes(b"dl-" + url.encode()[-4:])
        captured["downloads"].append(url)

    with (
        patch.object(mod, "_download_asset", side_effect=fake_download),
        patch.object(mod, "_image_to_video", side_effect=lambda img, dur, out, *a, **k: out.write_bytes(b"img-seg")),
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, *a, **k: out.write_bytes(b"concat")),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=lambda cur, out, *a, **k: (out.write_bytes(b"final"), out)),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: f),
        patch.object(mod, "_ffmpeg_extract_thumbnail", side_effect=lambda *a, **k: None),
    ):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)

    # 首尾帧下载远程，中间 local-only 帧不下载（用本地占位）
    assert "http://a" in captured["downloads"]
    assert "http://c" in captured["downloads"]
    assert all("f1" not in d for d in captured["downloads"]), "local-only 帧不应触发下载"

"""Task 12 (segmented N2/N3/N4): 分段渲染单帧失败不再崩全片。

``video_gen_segmented.py`` Step 1 里每帧渲染的四个调用**全裸**无 try/except：

    _prepare_local_visual(...)      # 本地素材
    _download_asset(...) + _image_to_video(...) / _prepare_segment_from_file(...)
    _create_text_scene(...)
    _create_fallback_segment(...)

一个坏素材（损坏 mp4 / 过期 URL / ffmpeg 崩）→ 异常穿出整个 for → 整个 v3 合成栈
崩掉，**已成功的其余帧全部白做**。而 v1（video_gen_ai_visual.py）有完整的逐帧降级。

**为什么必须早于 Task 10**：Task 10 修好 worker 探测后，v2/v3 会从「生产不可达的
死代码」变成活路径。顺序颠倒等于把一条未加固的管线直接推给用户。

修法：逐帧 try/except + 占位降级（复用已 import 的 ``_create_fallback_segment``），
对齐 v1；占位帧计入 sidecar 供前端告警。
"""

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import video_gen_segmented as seg  # noqa: E402


def test_per_frame_render_is_wrapped_in_try():
    """结构断言：Step 1 的逐帧渲染必须在 try/except 内。"""
    src = inspect.getsource(seg._prepare_frame_segment) if hasattr(seg, "_prepare_frame_segment") else inspect.getsource(seg)

    # 找到本地素材分支的调用点
    anchor = "_prepare_local_visual("
    assert anchor in src, "锚点失效：找不到 _prepare_local_visual 调用"
    at = src.index(anchor)
    preceding = src[:at]
    try_at = max(preceding.rfind("\ntry:"), preceding.rfind(" try:"))
    assert try_at != -1, (
        "逐帧渲染没有 try 包裹 —— 一个坏素材会崩掉整个 v3 合成栈，已成功的帧全部白做"
    )
    between = preceding[try_at:]
    assert "except" not in between and "finally" not in between, (
        "最近的 try 已被闭合，逐帧渲染其实裸在外面"
    )


def test_bad_frame_falls_back_to_placeholder(tmp_path, monkeypatch):
    """中间帧素材损坏 → 其余帧正常成片、坏帧走占位。"""
    calls = {"fallback": [], "prepared": []}

    def boom_local(src, visual_type, seg_path, duration, width, height):
        raise RuntimeError("corrupt asset")

    def fake_fallback(frame, seg_path, width, height, duration):
        calls["fallback"].append(frame.get("index"))
        Path(seg_path).write_bytes(b"placeholder-mp4")

    def fake_image_to_video(asset, seg_path, duration, width, height, **kw):
        calls["prepared"].append(str(seg_path))
        Path(seg_path).write_bytes(b"ok-mp4")

    monkeypatch.setattr(seg, "_prepare_local_visual", boom_local)
    monkeypatch.setattr(seg, "_create_fallback_segment", fake_fallback)
    monkeypatch.setattr(seg, "_image_to_video", fake_image_to_video)
    monkeypatch.setattr(seg, "_download_asset", lambda url, dest: Path(dest).write_bytes(b"a") or Path(dest))

    frames = [
        {"index": 0, "visual_url": "http://x/a.png", "visual_type": "image", "duration": 3.0},
        {"index": 1, "local_visual_path": "visuals/bad.mp4", "visual_type": "video", "duration": 3.0},
        {"index": 2, "visual_url": "http://x/c.png", "visual_type": "image", "duration": 3.0},
    ]
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    # 直接驱动 Step 1 的逐帧循环（通过 helper 若已抽出，否则用 compose 的最小 payload）
    assert hasattr(seg, "_prepare_frame_segment"), (
        "期望把逐帧渲染抽成 _prepare_frame_segment(frame, seg_path, ...) 便于隔离测试"
    )
    for i, frame in enumerate(frames):
        seg._prepare_frame_segment(
            frame,
            segments_dir / f"seg_{i:03d}.mp4",
            task_dir=tmp_path,
            default_type="ai_image",
            width=720,
            height=1280,
        )

    # 坏帧走了占位，其余两帧正常
    assert calls["fallback"] == [1], f"坏帧未走占位降级：{calls['fallback']}"
    for i in (0, 1, 2):
        assert (segments_dir / f"seg_{i:03d}.mp4").exists(), f"seg_{i:03d}.mp4 缺失——该帧被整体丢弃"


def test_placeholder_frames_recorded_for_sidecar(tmp_path, monkeypatch):
    """占位帧须可被上报（返回值或收集器），否则前端无从告警。"""
    monkeypatch.setattr(seg, "_prepare_local_visual", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr(
        seg, "_create_fallback_segment",
        lambda frame, seg_path, w, h, d: Path(seg_path).write_bytes(b"ph"),
    )

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    frame = {"index": 5, "local_visual_path": "visuals/bad.mp4", "visual_type": "video", "duration": 3.0}

    used_placeholder = seg._prepare_frame_segment(
        frame,
        segments_dir / "seg_005.mp4",
        task_dir=tmp_path,
        default_type="ai_image",
        width=720,
        height=1280,
    )
    assert used_placeholder is True, (
        "_prepare_frame_segment 须返回「是否用了占位」，供 sidecar 统计占位比例"
    )


def test_healthy_frame_returns_false(tmp_path, monkeypatch):
    """正常帧不得被标成占位。"""
    monkeypatch.setattr(seg, "_download_asset", lambda url, dest: Path(dest).write_bytes(b"a") or Path(dest))
    monkeypatch.setattr(
        seg, "_image_to_video",
        lambda asset, seg_path, d, w, h, **k: Path(seg_path).write_bytes(b"ok"),
    )

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    frame = {"index": 0, "visual_url": "http://x/a.png", "visual_type": "image", "duration": 3.0}

    used_placeholder = seg._prepare_frame_segment(
        frame,
        segments_dir / "seg_000.mp4",
        task_dir=tmp_path,
        default_type="ai_image",
        width=720,
        height=1280,
    )
    assert used_placeholder is False

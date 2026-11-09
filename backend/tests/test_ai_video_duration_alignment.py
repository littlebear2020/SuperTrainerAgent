"""HR-T2: ai_video 8s clip 补长到 frame.duration（音画等长）。

海螺单镜 capped 8s，TTS 旁白 >8s 时 video 早于 audio 结束（「没讲完就结束」）。
_ffmpeg_extend_clip_to_duration：clip ≥ target → trim；clip < target → tpad 定格末帧 + -t。

Task 4 起补长由 ``-stream_loop`` 循环改为 ``tpad=stop_mode=clone`` 定格末帧——循环
会让短片在长槽里重播多遍（1.2s 片 6s 槽重播 5 遍），观感呆滞跳帧。
"""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

WORKER_PIPE = Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines"


def _load_helpers():
    sys.modules.setdefault("pipelines", types.ModuleType("pipelines"))
    spec = importlib.util.spec_from_file_location("pipelines._ffmpeg_helpers", WORKER_PIPE / "_ffmpeg_helpers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pipelines._ffmpeg_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_compose_module(alias: str):
    """加载 video_gen_ai_visual（worker 合成器）到独立模块名。

    除 _ffmpeg_helpers 外还须预注册 _degradation_sidecar / _placeholder_sidecar
    （均为纯 stdlib 自包含副本）——裸 ``pipelines`` 占位模块不是包，不预注册会
    ModuleNotFoundError（test_video_branch_extends_short_clip 的 HEAD 预置红根因）。
    """
    _load_helpers()
    for sub in ("_degradation_sidecar", "_placeholder_sidecar"):
        spec = importlib.util.spec_from_file_location(f"pipelines.{sub}", WORKER_PIPE / f"{sub}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"pipelines.{sub}"] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location(f"pipelines.{alias}", WORKER_PIPE / "video_gen_ai_visual.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"pipelines.{alias}"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_extend_short_clip_pads_to_target(tmp_path):
    """8s clip 拉到 15s（frame.duration）→ tpad 定格末帧 + -t 15。"""
    h = _load_helpers()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"8s")
    out = tmp_path / "ext.mp4"
    out.write_bytes(b"out")  # 模拟 ffmpeg 已生成输出
    with patch.object(h.subprocess, "run") as mr:
        mr.return_value = type("P", (), {"returncode": 0})()
        h._ffmpeg_extend_clip_to_duration(clip, 15.0, out)
    cmd = " ".join(mr.call_args[0][0])
    assert "-t" in cmd and "15" in cmd
    assert "stream_loop" not in cmd  # Task 4：不再循环重播
    assert "tpad=stop_mode=clone" in cmd  # 短了→定格末帧


def test_extend_noop_when_long_enough(tmp_path):
    """clip 已 ≥ target（20s ≥ 15s）→ 不循环，trim 到 target。"""
    h = _load_helpers()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"20s")
    out = tmp_path / "ext.mp4"
    out.write_bytes(b"out")
    with (
        patch.object(h.subprocess, "run") as mr,
        patch.object(h, "_probe_duration", return_value=20.0),
    ):
        mr.return_value = type("P", (), {"returncode": 0})()
        h._ffmpeg_extend_clip_to_duration(clip, 15.0, out)
    cmd = " ".join(mr.call_args[0][0])
    assert "stream_loop" not in cmd  # 已够长→不补长
    assert "tpad" not in cmd  # 已够长→不定格
    assert "-t" in cmd and "15" in cmd


def test_video_branch_extends_short_clip(tmp_path):
    """video 分支：clip 短于 frame.duration → 调 _ffmpeg_extend_clip_to_duration。"""
    mod = _load_compose_module("video_gen_ai_visual_t2")

    payload = {
        "frames": [{"visual_url": "http://v", "visual_type": "video", "duration": 15.0}],
        "config": {"width": 1280, "height": 720, "transition": "none", "subtitle_style": "default"},
        "quality": "standard",
    }

    def fake_download(url, dest):
        Path(dest).write_bytes(b"dl")

    with (
        patch.object(mod, "_download_asset", side_effect=fake_download),
        patch.object(mod, "_probe_duration", return_value=5.0),  # clip 5s < duration 15s
        patch.object(mod, "_ffmpeg_extend_clip_to_duration", side_effect=lambda clip, dur, out, **kw: (out.write_bytes(b"ext"), out)[1]) as ext_mock,
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, *a, **k: out.write_bytes(b"concat")),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=lambda cur, out, *a, **k: (out.write_bytes(b"final"), out)[1]),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: f),
        patch.object(mod, "_ffmpeg_extract_thumbnail", side_effect=lambda *a, **k: None),
    ):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)
    ext_mock.assert_called_once()  # 短 clip → 触发 extend


# ── E9 超发裁剪（2026-08-25 补齐，计划测试②）：视频长于 TTS → 按 TTS 截断，无冻结 ──


def test_normalize_trims_to_tts_duration(tmp_path):
    """长 clip 按 TTS（帧）时长截断：normalize_clip_resolution 的 max_duration
    （H5，video_gen_ai_visual.py:284 恒传帧时长）→ ffmpeg -t 10.000，无 tpad。"""
    h = _load_helpers()
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"12.5s")
    out = tmp_path / "norm.mp4"
    out.write_bytes(b"out")  # 模拟 ffmpeg 已生成输出
    with (
        patch.object(h, "_probe_dimensions", return_value=(1280, 720)),
        patch.object(h.subprocess, "run") as mr,
    ):
        mr.return_value = type("P", (), {"returncode": 0})()
        h.normalize_clip_resolution(clip, out, 1280, 720, max_duration=10.0)
    cmd = " ".join(mr.call_args[0][0])
    assert "-t" in cmd and "10.000" in cmd, "长 clip 须按 TTS 时长精确截断"
    assert "tpad" not in cmd  # 裁剪替代冻结


def test_video_branch_trims_long_clip_without_padding(tmp_path):
    """compose 级：clip 12.5s > frame.duration 10s（=TTS）→ 不触发补长/定格链，
    直接按 TTS 时长截断（normalize_clip_resolution 实收 max_duration=10.0）。"""
    mod = _load_compose_module("video_gen_ai_visual_t3")

    payload = {
        "frames": [{"visual_url": "http://v", "visual_type": "video", "duration": 10.0}],
        "config": {"width": 1280, "height": 720, "transition": "none", "subtitle_style": "default"},
        "quality": "standard",
    }

    def fake_download(url, dest):
        Path(dest).write_bytes(b"dl")

    seen_max_duration = []

    def fake_normalize(clip, out, w, hgt, *, max_duration=None):
        seen_max_duration.append(max_duration)
        Path(out).write_bytes(b"norm")
        return Path(out)

    with (
        patch.object(mod, "_download_asset", side_effect=fake_download),
        patch.object(mod, "_probe_duration", return_value=12.5),  # clip 12.5s > duration 10s
        patch.object(mod, "_ffmpeg_extend_clip_to_duration") as ext_mock,
        patch.object(mod, "normalize_clip_resolution", side_effect=fake_normalize),
        patch.object(mod, "_ffmpeg_concat", side_effect=lambda segs, out, *a, **k: out.write_bytes(b"concat")),
        patch.object(mod, "_ffmpeg_convert_format", side_effect=lambda cur, out, *a, **k: (out.write_bytes(b"final"), out)[1]),
        patch.object(mod, "_finalize_output", side_effect=lambda r, f: f),
        patch.object(mod, "_ffmpeg_extract_thumbnail", side_effect=lambda *a, **k: None),
    ):
        mod.compose_ai_visual(tmp_path, payload, {}, lambda p: None)
    ext_mock.assert_not_called()  # 长 clip → 不得补长/定格
    assert seen_max_duration == [10.0], "须按 TTS（帧）时长截断"

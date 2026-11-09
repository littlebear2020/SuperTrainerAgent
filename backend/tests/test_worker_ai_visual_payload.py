import importlib.util
import sys
import types
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "video_gen_ai_visual",
    Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines/video_gen_ai_visual.py",
)
mod = importlib.util.module_from_spec(_SPEC)

guard = types.ModuleType("pipelines.net_guard")
guard.assert_public_http_url = lambda url: None
guard.max_asset_bytes = lambda: 512 * 1024 * 1024
guard.build_guarded_opener = lambda: types.SimpleNamespace(open=None)

helpers = types.ModuleType("pipelines._ffmpeg_helpers")
helpers._ffmpeg_concat = lambda *a, **k: None
helpers._ffmpeg_err = lambda *a, **k: ""
helpers._ffmpeg_mix_audio = lambda *a, **k: None
# Stubs for the remaining symbols imported by video_gen_ai_visual.py
helpers._dynamic_concat_timeout = lambda *a, **k: 60
helpers._ffmpeg_concat_with_transitions = lambda *a, **k: None
helpers._ffmpeg_convert_format = lambda *a, **k: a[0] if a else None
helpers._ffmpeg_extract_thumbnail = lambda *a, **k: True
helpers._ffmpeg_mix_bgm = lambda *a, **k: None
helpers._ffmpeg_mix_bgm_with_ducking = lambda *a, **k: None
# Task 3l: worker 接通 _ffmpeg_colorgrade（hue+eq 调色），测试 stub 须同步
helpers._ffmpeg_colorgrade = lambda *a, **k: a[0] if a else None
# Task 2-5: worker 改调共享 finalizer，mixed/segmented/ai_visual 均 import，stub 须同步
helpers._apply_colorgrade_and_subtitles = lambda video, *a, **k: video  # 返回输入视频
helpers._apply_intro_outro_fade = lambda video, *a, **k: video
# Task 3h: worker 归一化 clip 分辨率，stub 返回输入路径
helpers.normalize_clip_resolution = lambda *a, **k: a[0] if a else None
helpers._finalize_output = lambda result, final_raw, **k: final_raw
helpers._ffmpeg_extend_clip_to_duration = lambda *a, **k: a[2] if len(a) > 2 else None
helpers._probe_duration = lambda *a, **k: 0.0
# a42c6fdfa: worker 合成后探测黑场用于诊断，stub 须同步
helpers._probe_black_scenes = lambda *a, **k: []
helpers._write_av_drift_sidecar = lambda *a, **k: None


# 显式 stub 之外的新符号自动兜底：此前 worker 每加一个 helper import
# 就会让本文件整体 collection error，从而中断整个 backend 测试套件。
def _missing_helper(name):
    def _stub(*a, **k):
        return None

    _stub.__name__ = name
    return _stub


helpers.__getattr__ = _missing_helper

sys.modules.setdefault("pipelines", types.ModuleType("pipelines"))
# Task 10: v1/v2/v3 共用 pipelines._placeholder_sidecar；假 pipelines 不是真 package，
# 子模块 import 会 ModuleNotFoundError → 必须显式注册。
_ph_sidecar = types.ModuleType("pipelines._placeholder_sidecar")
_ph_sidecar.SIDECAR_NAME = "placeholder_frames.json"
_ph_sidecar.write_placeholder_sidecar = lambda *a, **k: None
sys.modules["pipelines._placeholder_sidecar"] = _ph_sidecar
# Task 23 / C11: 管线新增降级 sidecar import，同理需显式注册。
_deg_sidecar = types.ModuleType("pipelines._degradation_sidecar")
_deg_sidecar.DEGRADATION_SIDECAR_NAME = "degradations.json"
_deg_sidecar.KIND_BGM_FAILED = "bgm_failed"
_deg_sidecar.record_degradation = lambda *a, **k: None
_deg_sidecar.read_degradations = lambda *a, **k: []
sys.modules["pipelines._degradation_sidecar"] = _deg_sidecar
sys.modules["pipelines._ffmpeg_helpers"] = helpers
sys.modules["pipelines.net_guard"] = guard
_SPEC.loader.exec_module(mod)


def test_collect_visual_assets_from_frames():
    payload = {
        "frames": [
            {"visual_url": "http://x/a.png", "visual_type": "image", "duration": 5.0},
            {"visual_url": None, "visual_type": None, "duration": 3.0},
            {"visual_url": "http://x/b.mp4", "visual_type": "video", "duration": 8.0},
        ],
    }
    assets = mod._collect_visual_assets(payload)
    # P0-4 layer 3: 失败帧不再跳过——返回 placeholder 占位，保证音画等长（3 assets）
    assert len(assets) == 3
    assert assets[0]["url"] == "http://x/a.png"
    assert assets[0]["type"] == "image"
    assert assets[0]["duration"] == 5.0
    assert assets[1]["placeholder"] is True  # 失败帧占位（黑场+字幕由 concat 段生成）
    assert assets[1]["url"] is None
    assert assets[2]["url"] == "http://x/b.mp4"
    assert assets[2]["type"] == "video"


def test_collect_visual_assets_returns_empty_for_old_payload():
    payload = {"shots": []}
    assets = mod._collect_visual_assets(payload)
    assert assets == []


# ---------------------------------------------------------------------------
# P1-9 (HR-T2 incremental extend): _download_and_prepare must extend short
# ai_video clips so the incremental recompose path doesn't desync audio.
# ---------------------------------------------------------------------------


def test_download_and_prepare_extends_short_video(tmp_path, monkeypatch):
    """短于 duration 的 ai_video clip 必须被循环补齐（增量路径对称修复）。"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    asset = {
        "frame_index": 0,
        "url": "http://x/v.mp4",
        "local_path": None,
        "type": "video",
        "duration": 10.0,
    }

    monkeypatch.setattr(mod, "_download_asset", lambda url, dest: Path(dest).write_bytes(b"fake"))
    called = {"extend": 0}

    def fake_extend(src, dur, dst, **kw):
        called["extend"] += 1
        Path(dst).write_bytes(b"extended")
        return dst

    monkeypatch.setattr(mod, "_ffmpeg_extend_clip_to_duration", fake_extend)
    monkeypatch.setattr(mod, "_probe_duration", lambda p: 5.0)  # 5.0 + 0.2 < 10 → extend

    result = mod._download_and_prepare(asset, source_dir, tmp_dir, 1280, 720, "standard")
    assert called["extend"] == 1
    assert Path(result).exists()


def test_download_and_prepare_no_extend_when_long_enough(tmp_path, monkeypatch):
    """clip 时长 ≥ duration 时不应 extend（直接返回下载文件）。"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    asset = {
        "frame_index": 1,
        "url": "http://x/v.mp4",
        "local_path": None,
        "type": "video",
        "duration": 5.0,
    }

    monkeypatch.setattr(mod, "_download_asset", lambda url, dest: Path(dest).write_bytes(b"fake"))

    def boom(*a, **k):
        raise AssertionError("should not extend when clip long enough")

    monkeypatch.setattr(mod, "_ffmpeg_extend_clip_to_duration", boom)
    monkeypatch.setattr(mod, "_probe_duration", lambda p: 10.0)  # 10 > 5 → no extend

    result = mod._download_and_prepare(asset, source_dir, tmp_dir, 1280, 720, "standard")
    assert result == source_dir / "shot_001.mp4"


def test_download_and_prepare_image_calls_image_to_video(tmp_path, monkeypatch):
    """image asset → _image_to_video，返回 tmp 段 mp4。"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    asset = {
        "frame_index": 2,
        "url": "http://x/img.png",
        "local_path": None,
        "type": "image",
        "duration": 5.0,
    }

    monkeypatch.setattr(mod, "_download_asset", lambda url, dest: Path(dest).write_bytes(b"fake"))
    called = {"i2v": 0}

    def fake_i2v(img, dur, out, w, h, q, **kwargs):
        called["i2v"] += 1
        Path(out).write_bytes(b"mp4")

    monkeypatch.setattr(mod, "_image_to_video", fake_i2v)

    result = mod._download_and_prepare(asset, source_dir, tmp_dir, 1280, 720, "standard")
    assert called["i2v"] == 1
    assert result == tmp_dir / "shot_002.mp4"

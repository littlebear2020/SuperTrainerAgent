"""Tests for incremental manifest hash logic in worker pipelines."""

import importlib.util
import json
import sys
import types
from pathlib import Path

# ── Stub pipelines package + _ffmpeg_helpers so modules load without ffmpeg ─

pipelines_pkg = types.ModuleType("pipelines")
pipelines_pkg.__path__ = []
sys.modules["pipelines"] = pipelines_pkg

# C2/评审修复轮：worker 下载器统一经 net_guard——stub 其接口（真实行为由
# docker/production-worker/tests/test_outbound_guard.py 覆盖）
_net_guard = types.ModuleType("pipelines.net_guard")
_net_guard.assert_public_http_url = lambda url: None
_net_guard.max_asset_bytes = lambda: 512 * 1024 * 1024
_net_guard.build_guarded_opener = lambda: types.SimpleNamespace(open=None)
sys.modules["pipelines.net_guard"] = _net_guard

helpers = types.ModuleType("pipelines._ffmpeg_helpers")
helpers._dynamic_concat_timeout = lambda *a, **k: 60
helpers._ffmpeg_colorgrade = lambda *a, **k: None
# Task 2-5: worker 改调共享 finalizer（_apply_colorgrade_and_subtitles / _apply_intro_outro_fade），
# mixed/segmented import 之；stub 须同步，否则 ImportError at collection。返输入视频。
helpers._apply_colorgrade_and_subtitles = lambda video, *a, **k: video
helpers._apply_intro_outro_fade = lambda video, *a, **k: video
# mixed also imports _escape_subtitle_filter_path（字幕路径转义）；stub 返输入串。
helpers._escape_subtitle_filter_path = lambda p: p
helpers._ffmpeg_concat = lambda *a, **k: None
helpers._ffmpeg_concat_with_transitions = lambda *a, **k: None
helpers._ffmpeg_convert_format = lambda src, dst, **k: dst
helpers._ffmpeg_err = lambda *a, **k: ""
helpers._ffmpeg_extract_thumbnail = lambda *a, **k: None
helpers._ffmpeg_mix_audio = lambda *a, **k: None
helpers._ffmpeg_mix_bgm = lambda *a, **k: None
helpers._ffmpeg_mix_bgm_with_ducking = lambda *a, **k: None
helpers._finalize_output = lambda result, final_raw, **k: final_raw
helpers._ffprobe_duration = lambda *a, **k: 1.0
helpers._get_quality_args = lambda *a, **k: []
# B1: mixed._prepare_segment_from_file import _ffmpeg_extend_clip_to_duration；stub 返输入。
helpers._ffmpeg_extend_clip_to_duration = lambda clip, target, output, *a, **k: clip
# E4: mixed/segmented import _write_av_drift_sidecar（av_drift.json sidecar）；stub 返 None。
helpers._write_av_drift_sidecar = lambda *a, **k: None
# C3 (Task 8): 三条管线终混调 _normalize_loudness；stub 返输入视频（不改动）。
helpers._normalize_loudness = lambda video, output, **k: video


# 显式 stub 之外的新符号自动兜底：worker 每加一个 helper import，本文件若少一个
# stub 就会 collection error，从而中断整个 backend 测试套件。
def _missing_helper(name):
    def _stub(*a, **k):
        return None

    _stub.__name__ = name
    return _stub


helpers.__getattr__ = _missing_helper

sys.modules["pipelines._ffmpeg_helpers"] = helpers

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

# ── Load video_gen_mixed first (segmented imports from it) ──────────────────

_mixed_spec = importlib.util.spec_from_file_location(
    "pipelines.video_gen_mixed",
    Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines/video_gen_mixed.py",
)
mixed_mod = importlib.util.module_from_spec(_mixed_spec)
sys.modules["pipelines.video_gen_mixed"] = mixed_mod
_mixed_spec.loader.exec_module(mixed_mod)

# ── Load video_gen_segmented ────────────────────────────────────────────────

_seg_spec = importlib.util.spec_from_file_location(
    "pipelines.video_gen_segmented",
    Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines/video_gen_segmented.py",
)
seg_mod = importlib.util.module_from_spec(_seg_spec)
sys.modules["pipelines.video_gen_segmented"] = seg_mod
_seg_spec.loader.exec_module(seg_mod)


# ── _compute_frame_hash tests ──────────────────────────────────────────────


def test_compute_frame_hash_deterministic():
    frame = {"narration": "hello", "visual_url": "http://x/a.mp4", "visual_source": "ai_image", "duration": 3.0}
    assert seg_mod._compute_frame_hash(frame) == seg_mod._compute_frame_hash(frame)


def test_compute_frame_hash_changes_on_narration():
    f1 = {"narration": "hello", "visual_url": "", "visual_source": "ai_image", "duration": 3.0}
    f2 = {"narration": "world", "visual_url": "", "visual_source": "ai_image", "duration": 3.0}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)


def test_compute_frame_hash_changes_on_visual_url():
    f1 = {"narration": "hi", "visual_url": "http://x/a.mp4", "visual_source": "ai_image", "duration": 3.0}
    f2 = {"narration": "hi", "visual_url": "http://x/b.mp4", "visual_source": "ai_image", "duration": 3.0}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)


def test_compute_frame_hash_changes_on_visual_source():
    f1 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0}
    f2 = {"narration": "hi", "visual_url": "", "visual_source": "ai_video", "duration": 3.0}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)


def test_compute_frame_hash_changes_on_duration():
    f1 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0}
    f2 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 5.0}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)


def test_compute_frame_hash_changes_on_local_visual_path():
    f1 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0, "local_visual_path": ""}
    f2 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0, "local_visual_path": "uploads/x.jpg"}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)


def test_compute_frame_hash_changes_on_visual_type():
    """visual_type must be part of the hash: uploading a video where an image was
    (same path slot, different type) must invalidate the cached segment."""
    f1 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0, "visual_type": "image"}
    f2 = {"narration": "hi", "visual_url": "", "visual_source": "ai_image", "duration": 3.0, "visual_type": "video"}
    assert seg_mod._compute_frame_hash(f1) != seg_mod._compute_frame_hash(f2)
    # mixed pipeline must agree with segmented (parity invariant)
    assert mixed_mod._compute_frame_hash(f1) != mixed_mod._compute_frame_hash(f2)


def test_compute_frame_hash_defaults_for_missing_fields():
    minimal = {}
    full = {"narration": "", "visual_url": "", "visual_source": "", "local_visual_path": "", "duration": 3.0}
    assert seg_mod._compute_frame_hash(minimal) == seg_mod._compute_frame_hash(full)


def test_mixed_compute_frame_hash_matches_segmented():
    frame = {"narration": "hello", "visual_url": "http://x/a.mp4", "visual_source": "ai_video", "duration": 4.0}
    assert seg_mod._compute_frame_hash(frame) == mixed_mod._compute_frame_hash(frame)


# ── _load_manifest / _save_manifest tests ──────────────────────────────────


def test_load_manifest_returns_empty_when_missing(tmp_path: Path):
    assert seg_mod._load_manifest(tmp_path) == {}


def test_load_manifest_reads_existing(tmp_path: Path):
    manifest = {"frames": [{"index": 0, "segment_hash": "abc"}], "global_hash": "xyz"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert seg_mod._load_manifest(tmp_path) == manifest


def test_load_manifest_tolerates_corrupt_json(tmp_path: Path):
    (tmp_path / "manifest.json").write_text("{broken json")
    assert seg_mod._load_manifest(tmp_path) == {}


def test_save_manifest_writes_atomically(tmp_path: Path):
    manifest = {"frames": [{"index": 0, "segment_hash": "abc"}], "global_hash": "xyz"}
    seg_mod._save_manifest(tmp_path, manifest)
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest


def test_save_manifest_no_tmp_left_behind(tmp_path: Path):
    seg_mod._save_manifest(tmp_path, {"frames": [], "global_hash": ""})
    assert not (tmp_path / "manifest.json.tmp").exists()


# ── compose_segmented manifest integration tests ───────────────────────────


def _make_frame(index: int, narration="hello", visual_url="http://x/a.mp4", duration=3.0, visual_source="ai_image", local_visual_path=None):
    return {
        "index": index,
        "narration": narration,
        "visual_url": visual_url,
        "visual_type": "video",
        "visual_source": visual_source,
        "duration": duration,
        "local_visual_path": local_visual_path,
    }


def _make_progress_cb():
    calls = []
    return (lambda pct: calls.append(pct)), calls


def test_compose_segmented_saves_manifest(tmp_path: Path, monkeypatch):
    """compose_segmented should save a manifest after first render."""
    frames = [_make_frame(0), _make_frame(1, narration="world")]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
        "scene_groups": [{"frame_indices": [0, 1], "transition_duration": 0.5}],
    }

    # Stub out visual source helpers to create dummy segment files
    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(seg_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["global_hash"]
    assert len(manifest["frames"]) == 2
    assert all("segment_hash" in f for f in manifest["frames"])


def test_compose_segmented_dirty_frame_deleted(tmp_path: Path, monkeypatch):
    """On second run with changed frame 1, only seg_001 should be regenerated."""
    frames = [_make_frame(0), _make_frame(1, narration="original")]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
        "scene_groups": [{"frame_indices": [0, 1], "transition_duration": 0.5}],
    }

    # First run: create segments + manifest
    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(seg_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    seg0 = tmp_path / "segments" / "seg_000.mp4"
    seg1 = tmp_path / "segments" / "seg_001.mp4"
    assert seg0.exists()
    assert seg1.exists()

    # Second run: change frame 1 narration
    payload["frames"][1]["narration"] = "changed narration"

    # Track which segments get regenerated via fake_prepare
    regenerated: list[int] = []
    orig_prepare = fake_prepare

    def tracking_prepare(src, output, duration, w, h, **kw):
        orig_prepare(src, output, duration, w, h)
        # Extract index from seg_XXX.mp4
        idx = int(output.stem.split("_")[1])
        regenerated.append(idx)

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", tracking_prepare)

    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    # Frame 1 was dirty → deleted and regenerated
    assert 1 in regenerated
    # Frame 0 was unchanged → not regenerated
    assert 0 not in regenerated


def test_compose_segmented_global_change_invalidates_all(tmp_path: Path, monkeypatch):
    """Changing transition config should invalidate all segments."""
    frames = [_make_frame(0), _make_frame(1)]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
        "scene_groups": [{"frame_indices": [0, 1], "transition_duration": 0.5}],
    }

    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(seg_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    # Second run: change transition
    payload["config"]["transition"] = "dissolve"

    regenerated: list[int] = []
    orig_prepare = fake_prepare

    def tracking_prepare(src, output, duration, w, h, **kw):
        orig_prepare(src, output, duration, w, h)
        idx = int(output.stem.split("_")[1])
        regenerated.append(idx)

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", tracking_prepare)

    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    # All frames invalidated by global config change
    assert 0 in regenerated
    assert 1 in regenerated


def test_compose_segmented_group_output_invalidated(tmp_path: Path, monkeypatch):
    """Dirty frame should cause group output to be deleted for regeneration."""
    frames = [_make_frame(0), _make_frame(1), _make_frame(2)]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
        "scene_groups": [
            {"frame_indices": [0, 1], "transition_duration": 0.5},
            {"frame_indices": [2], "transition_duration": 0.5},
        ],
    }

    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(seg_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    group0 = tmp_path / "group_000.mp4"
    # Simulate group_000 exists (compose would create it, but we stubbed concat)
    group0.write_bytes(b"\x00")
    group1 = tmp_path / "group_001.mp4"
    group1.write_bytes(b"\x00")

    # Second run: change frame 0 (in group 0)
    payload["frames"][0]["narration"] = "changed"

    monkeypatch.setattr(seg_mod, "_prepare_segment_from_file", fake_prepare)
    seg_mod.compose_segmented(tmp_path, payload, {}, cb)

    # group_000 should have been deleted because dirty frame 0 is in group 0
    # (the stubbed concat doesn't recreate it)
    assert not (tmp_path / "group_000.mp4").exists(), "group_000 should be invalidated"
    # group_001 should still exist (no dirty frames in group 1)
    assert (tmp_path / "group_001.mp4").exists(), "group_001 should be cached"
    # Verify manifest was saved with updated hashes
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert len(manifest["frames"]) == 3


# ── compose_mixed integration tests ─────────────────────────────────────────


def test_compose_mixed_saves_manifest(tmp_path: Path, monkeypatch):
    """compose_mixed should save a manifest after first render."""
    frames = [_make_frame(0), _make_frame(1, narration="world")]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
    }

    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(mixed_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(mixed_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    mixed_mod.compose_mixed(tmp_path, payload, {}, cb)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["global_hash"]
    assert len(manifest["frames"]) == 2
    assert all("segment_hash" in f for f in manifest["frames"])


def test_compose_mixed_dirty_frame_regenerated(tmp_path: Path, monkeypatch):
    """On second run with changed frame 1, only seg_001 should be regenerated."""
    frames = [_make_frame(0), _make_frame(1, narration="original")]
    payload = {
        "frames": frames,
        "config": {"width": 1080, "height": 1920, "transition": "fade", "transition_duration": 0.5},
        "quality": "standard",
        "output_format": "mp4",
    }

    def fake_prepare(src, output, duration, w, h, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")

    monkeypatch.setattr(mixed_mod, "_prepare_segment_from_file", fake_prepare)
    monkeypatch.setattr(mixed_mod, "_download_asset", lambda url, stem: stem.with_suffix(".mp4"))

    cb, _ = _make_progress_cb()
    mixed_mod.compose_mixed(tmp_path, payload, {}, cb)

    seg0 = tmp_path / "segments" / "seg_000.mp4"
    seg1 = tmp_path / "segments" / "seg_001.mp4"
    assert seg0.exists()
    assert seg1.exists()

    # Second run: change frame 1 narration
    payload["frames"][1]["narration"] = "changed narration"

    regenerated: list[int] = []
    orig_prepare = fake_prepare

    def tracking_prepare(src, output, duration, w, h, **kw):
        orig_prepare(src, output, duration, w, h)
        idx = int(output.stem.split("_")[1])
        regenerated.append(idx)

    monkeypatch.setattr(mixed_mod, "_prepare_segment_from_file", tracking_prepare)

    mixed_mod.compose_mixed(tmp_path, payload, {}, cb)

    # Frame 1 was dirty → deleted and regenerated
    assert 1 in regenerated
    # Frame 0 was unchanged → not regenerated
    assert 0 not in regenerated

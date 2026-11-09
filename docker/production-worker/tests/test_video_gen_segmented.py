"""Tests for video_gen_segmented pipeline and registry routing."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_resolve_pipeline_v3_segmented():
    """Registry should route v3_segmented to compose_segmented."""
    from pipelines.registry import resolve_pipeline
    from pipelines.video_gen_segmented import compose_segmented

    fn = resolve_pipeline("video_gen", "ai_image", {"pipeline_version": "v3_segmented"})
    assert fn is compose_segmented


def test_resolve_pipeline_v2_mixed_still_works():
    """Registry should still route v2_mixed to compose_mixed."""
    from pipelines.registry import resolve_pipeline
    from pipelines.video_gen_mixed import compose_mixed

    fn = resolve_pipeline("video_gen", "ai_image", {"pipeline_version": "v2_mixed"})
    assert fn is compose_mixed


def test_resolve_pipeline_v1_default():
    """Registry should fall back to type-based lookup for v1 (no pipeline_version)."""
    from pipelines.registry import resolve_pipeline
    from pipelines.video_gen_ai_visual import compose_ai_visual

    fn = resolve_pipeline("video_gen", "ai_image", {})
    assert fn is compose_ai_visual


def test_compose_segmented_cache_reuse_segments(tmp_path):
    """compose_segmented should skip already-existing segment files."""
    from pipelines.video_gen_segmented import compose_segmented

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    # Pre-create cached segments
    for i in range(3):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"cached")

    progress_values = []

    def progress_cb(val):
        progress_values.append(val)

    payload = {
        "frames": [
            {"narration": f"frame {i}", "duration": 3.0, "visual_source": "remotion"}
            for i in range(3)
        ],
        "scene_groups": [
            {"index": 0, "title": "g0", "frame_indices": [0, 1]},
            {"index": 1, "title": "g1", "frame_indices": [2]},
        ],
        "config": {"transition": "none", "width": 1080, "height": 1920},
        "quality": "standard",
        "output_format": "mp4",
    }

    # Mock all ffmpeg calls to just copy the file
    def mock_concat(*args, **kwargs):
        out = args[1] if len(args) > 1 else kwargs.get("output")
        # Write the group output
        groups = [args[0]] if isinstance(args[0], Path) else args[0]
        first = groups[0]
        import shutil
        shutil.copy2(str(first), str(out))

    def mock_convert(src, dst, **kwargs):
        import shutil
        shutil.copy2(str(src), str(dst))
        return dst

    def mock_thumb(*args, **kwargs):
        pass

    with patch("pipelines.video_gen_segmented._ffmpeg_concat", side_effect=mock_concat), \
         patch("pipelines.video_gen_segmented._ffmpeg_convert_format", side_effect=mock_convert), \
         patch("pipelines.video_gen_segmented._ffmpeg_extract_thumbnail", side_effect=mock_thumb):
        result = compose_segmented(tmp_path, payload, {}, progress_cb)

    assert result.exists()
    assert result.name == "output.mp4"
    assert len(progress_values) > 0


def test_compose_segmented_empty_frames_raises(tmp_path):
    """compose_segmented should raise ValueError for empty frames."""
    from pipelines.video_gen_segmented import compose_segmented

    payload = {"frames": [], "scene_groups": []}
    try:
        compose_segmented(tmp_path, payload, {}, lambda v: None)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_compose_segmented_subtitle_burn_failure_skips_subs(tmp_path):
    """Burn failure -> compose_segmented continues from concat_video, no crash."""
    from pipelines.video_gen_segmented import compose_segmented

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    for i in range(2):
        (segments_dir / f"seg_{i:03d}.mp4").write_bytes(b"seg")

    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n")
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"au")

    payload = {
        "frames": [
            {"narration": "f0", "duration": 3.0, "visual_source": "remotion"},
            {"narration": "f1", "duration": 3.0, "visual_source": "remotion"},
        ],
        "scene_groups": [
            {"index": 0, "title": "g0", "frame_indices": [0, 1]},
        ],
        "config": {"transition": "none", "width": 1080, "height": 1920},
        "quality": "standard",
        "output_format": "mp4",
        "subtitle_path": str(srt),
        "audio_concat_path": str(audio),
    }

    mix_video_arg = []

    def mock_concat(*args, **kwargs):
        import shutil

        out = args[1] if len(args) > 1 else kwargs.get("output")
        groups = [args[0]] if isinstance(args[0], Path) else args[0]
        shutil.copy2(str(groups[0]), str(out))

    def mock_mix(video, audio_in, output, **kwargs):
        mix_video_arg.append(video)
        output.write_bytes(b"mixed")

    def mock_convert(video, output, **kwargs):
        output.write_bytes(b"final")
        return output

    with patch("pipelines.video_gen_segmented._ffmpeg_concat", side_effect=mock_concat), \
         patch("pipelines.video_gen_segmented._ffmpeg_mix_audio", side_effect=mock_mix), \
         patch("pipelines.video_gen_segmented._ffmpeg_convert_format", side_effect=mock_convert), \
         patch("pipelines.video_gen_segmented._ffmpeg_extract_thumbnail"), \
         patch("pipelines._ffmpeg_helpers._burn_subtitles", return_value=(False, '')):
        result = compose_segmented(tmp_path, payload, {}, lambda v: None)

    assert result.exists()
    assert mix_video_arg, "mix_audio should have been called"
    assert mix_video_arg[0].name == "concat_video.mp4"


def test_segmented_writes_subtitle_status(tmp_path, monkeypatch):
    """A: segmented（主流路径）接入 finalizer 后写 subtitle_status.json（之前从不写）。

    预置 cached seg 跳过 _download_asset/_image_to_video/_prepare_segment_from_file；
    不桩 finalizer，让真实 _apply_colorgrade_and_subtitles(color_palette=None, subtitle_path=None)
    跑——它仅写 skipped，不触 ffmpeg。_apply_intro_outro_fade 对假 mp4 探时长失败→返原视频（安全）。"""
    import json
    import pipelines.video_gen_segmented as s
    segs = tmp_path / "segments"
    segs.mkdir()
    (segs / "seg_000.mp4").write_bytes(b"mp4")  # cached
    monkeypatch.setattr(s, "_ffmpeg_concat", lambda segs_list, out, **k: out.write_bytes(b"mp4"))
    monkeypatch.setattr(s, "_ffmpeg_concat_with_transitions", lambda *a, **k: None)
    monkeypatch.setattr(s, "_ffmpeg_convert_format", lambda inp, out, **k: out)
    monkeypatch.setattr(s, "_ffmpeg_extract_thumbnail", lambda *a, **k: True)
    monkeypatch.setattr(s, "_ffmpeg_mix_audio", lambda *a, **k: None)
    monkeypatch.setattr(s, "_ffmpeg_mix_bgm_with_ducking", lambda *a, **k: None)
    monkeypatch.setattr(s, "_save_manifest", lambda *a, **k: None)
    frames = [{"index": 0, "duration": 3.0}]
    groups = [{"frame_indices": [0], "transition_duration": 0.5}]
    payload = {"frames": frames, "scene_groups": groups, "config": {"width": 1080, "height": 1920, "transition": "none"}, "video_type": "ai_image"}
    s.compose_segmented(tmp_path, payload, {}, lambda p: None)
    assert (tmp_path / "subtitle_status.json").exists()
    assert json.loads((tmp_path / "subtitle_status.json").read_text())["status"] == "skipped"


def test_compose_segmented_finalizes_output_on_convert_shortcircuit(tmp_path):
    """B3: _ffmpeg_convert_format 短路（返 input 不写 output）时，_finalize_output 须原子覆盖 output.mp4。

    修前 segmented 不调 _finalize_output → mp4→mp4 短路时 output.mp4 永不创建（latent）。
    不桩 _finalize_output，让真实原子 move 跑——这是本测要验的真实行为。
    """
    from pipelines.video_gen_segmented import compose_segmented
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    (segments_dir / "seg_000.mp4").write_bytes(b"cached")
    short_circuit = tmp_path / "short.mp4"
    short_circuit.write_bytes(b"finalized")
    payload = {
        "frames": [{"narration": "f0", "duration": 3.0, "visual_source": "remotion"}],
        "scene_groups": [],
        "config": {"transition": "none", "width": 1080, "height": 1920},
        "quality": "standard",
        "output_format": "mp4",
    }

    def mock_concat(*args, **kwargs):
        import shutil
        out = args[1] if len(args) > 1 else kwargs.get("output")
        groups = [args[0]] if isinstance(args[0], Path) else args[0]
        shutil.copy2(str(groups[0]), str(out))

    def mock_convert(src, dst, **kwargs):
        # 短路：返回别的路径，不写 dst（模拟 mp4→mp4 convert 返 input）
        return short_circuit

    with patch("pipelines.video_gen_segmented._ffmpeg_concat", side_effect=mock_concat), \
         patch("pipelines.video_gen_segmented._ffmpeg_convert_format", side_effect=mock_convert), \
         patch("pipelines.video_gen_segmented._ffmpeg_extract_thumbnail"), \
         patch("pipelines.video_gen_segmented._apply_intro_outro_fade", side_effect=lambda c, **k: c):
        result = compose_segmented(tmp_path, payload, {}, lambda v: None)
    output_path = tmp_path / "output.mp4"
    assert output_path.exists(), "短路时 _finalize_output 须把结果原子移到 output.mp4"
    assert output_path.read_bytes() == b"finalized"
    assert result == output_path


def test_compose_segmented_orders_groups_by_frame_index(tmp_path, monkeypatch):
    """E3: scene_groups 乱序时须按 min(frame_indices) 升序拼接。

    修前按声明顺序拼接 → 段落顺序与按帧序拼好的音频/SRT 错位 → 全片音画/字幕错乱。
    """
    from pathlib import Path as _P

    import pipelines.video_gen_segmented as s

    segs = tmp_path / "segments"
    segs.mkdir()
    for i in range(4):
        (segs / f"seg_{i:03d}.mp4").write_bytes(f"seg{i}".encode())

    # 记录：group_XXX.mp4 -> 该组拼入的帧序集合；以及 inter-group concat 的输入顺序
    group_frames: dict[str, set[int]] = {}
    inter_order: list[list[str]] = []

    def fake_concat(inputs, out, **k):
        import shutil

        inputs = list(inputs)
        first = inputs[0]
        if isinstance(first, _P) and first.exists():
            shutil.copy2(str(first), str(out))
        else:
            out.write_bytes(b"x")
        idxs = set()
        for p in inputs:
            n = _P(p).name
            if n.startswith("seg_"):
                idxs.add(int(n[4:7]))
        if idxs:
            group_frames[_P(out).name] = idxs
        if inputs and _P(inputs[0]).name.startswith("group_"):
            inter_order.append([_P(p).name for p in inputs])

    def fake_convert(src, out, **k):
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr(s, "_ffmpeg_concat", fake_concat)
    monkeypatch.setattr(s, "_ffmpeg_concat_with_transitions", lambda *a, **k: None)
    monkeypatch.setattr(s, "_ffmpeg_convert_format", fake_convert)
    monkeypatch.setattr(s, "_ffmpeg_extract_thumbnail", lambda *a, **k: True)
    monkeypatch.setattr(s, "_ffmpeg_mix_audio", lambda *a, **k: None)
    monkeypatch.setattr(s, "_ffmpeg_mix_bgm_with_ducking", lambda *a, **k: None)
    monkeypatch.setattr(s, "_save_manifest", lambda *a, **k: None)

    frames = [{"index": i, "duration": 3.0} for i in range(4)]
    # 故意乱序：声明在前的是帧 [2,3]，帧 [0,1] 在后
    groups = [
        {"frame_indices": [2, 3]},
        {"frame_indices": [0, 1]},
    ]
    payload = {
        "frames": frames,
        "scene_groups": groups,
        "config": {"width": 1080, "height": 1920, "transition": "none"},
        "video_type": "ai_image",
    }
    s.compose_segmented(tmp_path, payload, {}, lambda p: None)

    assert inter_order, "inter-group concat 应被调用"
    final_group_names = inter_order[-1]
    seq = [group_frames[n] for n in final_group_names]
    # 排序后：min 帧序升序 → [{0,1}, {2,3}]（修前为声明顺序 [{2,3}, {0,1}]）
    assert seq == [{0, 1}, {2, 3}], f"scene_groups 必须按 min(frame_indices) 排序拼接，得到 {seq}"

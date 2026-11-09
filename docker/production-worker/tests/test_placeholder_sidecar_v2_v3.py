"""Task 10（补 78fd24054 缺口）：占位上报必须覆盖 v2/v3，不只 v1。

``_write_placeholder_sidecar`` 全仓**唯一定义在** ``video_gen_ai_visual.py``（v1）。
v2_mixed / v3_segmented 无任何占位 sidecar —— 而 >1 scene_group 走 v3，即**课程视频
主路径永远不弹占位告警**。

这两件事必须同 Task 做完：修好 worker 探测（NEW-3）后 v3 立刻从死代码变活路径，
占位上报缺口同时暴露。

修法：把 writer 提到共享模块 ``_placeholder_sidecar.py``，三条管线复用。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_shared_module_exists():
    """writer 须提到共享模块，避免三条管线各写一份。"""
    from pipelines._placeholder_sidecar import write_placeholder_sidecar  # noqa: F401


def test_v1_still_uses_shared_writer():
    """v1 改为复用共享 writer（行为不变）。"""
    from pipelines import video_gen_ai_visual as av

    assert hasattr(av, "_write_placeholder_sidecar"), "v1 的调用点名字不应消失（兼容既有测试）"


def test_segmented_reports_placeholders(tmp_path, monkeypatch):
    """v3：坏帧走占位 → 必须写 placeholder_frames.json。"""
    from pipelines import video_gen_segmented as seg

    monkeypatch.setattr(
        seg, "_prepare_local_visual",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt")),
    )
    monkeypatch.setattr(
        seg, "_create_fallback_segment",
        lambda frame, seg_path, w, h, d: Path(seg_path).write_bytes(b"ph"),
    )

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    frames = [
        {"index": 0, "local_visual_path": "visuals/bad0.mp4", "visual_type": "video", "duration": 3.0},
        {"index": 1, "local_visual_path": "visuals/bad1.mp4", "visual_type": "video", "duration": 3.0},
    ]

    placeholders = []
    for i, frame in enumerate(frames):
        if seg._prepare_frame_segment(
            frame, segments_dir / f"seg_{i:03d}.mp4",
            task_dir=tmp_path, default_type="ai_image", width=720, height=1280,
        ):
            placeholders.append(frame["index"])

    from pipelines._placeholder_sidecar import write_placeholder_sidecar

    write_placeholder_sidecar(tmp_path, placeholders, total=len(frames))

    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [0, 1]
    assert data["total"] == 2


def test_v2_v3_pipelines_call_the_writer():
    """结构断言：mixed / segmented 必须真的调 writer（否则只是死代码）。"""
    pipelines = Path(__file__).parent.parent / "pipelines"
    for name in ("video_gen_mixed.py", "video_gen_segmented.py"):
        text = (pipelines / name).read_text(encoding="utf-8")
        assert "write_placeholder_sidecar" in text, (
            f"{name} 未上报占位 —— 课程视频主路径（>1 scene_group 走 v3）永远不弹告警"
        )


def test_no_placeholders_clears_stale_sidecar(tmp_path):
    """无占位时删除旧 sidecar（否则上次告警残留在本次成功成片上）。"""
    from pipelines._placeholder_sidecar import write_placeholder_sidecar

    write_placeholder_sidecar(tmp_path, [1, 2], total=4)
    assert (tmp_path / "placeholder_frames.json").exists()

    write_placeholder_sidecar(tmp_path, [], total=4)
    assert not (tmp_path / "placeholder_frames.json").exists()


# ── 审查发现 2：v3 的 touched 传了全部帧，recompose 会抹掉缓存黑场 ──────────
#
# v3 有段缓存（`if seg_path.exists(): continue`）：上一轮已是黑场的缓存帧
# ①被 continue 跳过 → 不进 placeholder_indices；②又在 touched 里 → 被
# _placeholder_sidecar 的合并逻辑当作「本次已复核为正常」剔除。
# 最坏：空 dirty 的 recompose → indices 空 + touched=all → merged 空 → sidecar
# 被 unlink，**所有历史黑场告警一笔抹掉**。
# v1 传的是 valid_changed（实际重渲帧），v3 应对齐。


def test_cached_placeholder_survives_noop_recompose(tmp_path):
    """空 dirty 的 recompose 不得抹掉上一轮的黑场记录。"""
    from pipelines._placeholder_sidecar import write_placeholder_sidecar

    # 首轮：3 帧全黑场
    write_placeholder_sidecar(tmp_path, [0, 1, 2], total=3, touched=[0, 1, 2])

    # recompose：全部段命中缓存 → 本轮 placeholder_indices 为空，
    # touched 只应包含**本次真正重渲**的帧（此处为空）
    write_placeholder_sidecar(tmp_path, [], total=3, touched=[])

    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [0, 1, 2], "历史黑场记录被抹掉了"


def test_only_rerendered_frame_gets_reevaluated(tmp_path):
    """只重渲帧 1 且这次成功 → 帧 1 从占位名单移除，帧 0/2 保留。"""
    from pipelines._placeholder_sidecar import write_placeholder_sidecar

    write_placeholder_sidecar(tmp_path, [0, 1, 2], total=3, touched=[0, 1, 2])
    write_placeholder_sidecar(tmp_path, [], total=3, touched=[1])

    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [0, 2], f"只应移除重渲成功的帧 1，实得 {data['indices']}"


def test_v3_passes_only_dirty_frames_as_touched():
    """结构断言：v3 不得再把全部帧当 touched 传入。"""
    src = (Path(__file__).parent.parent / "pipelines" / "video_gen_segmented.py").read_text(
        encoding="utf-8"
    )
    assert "touched=[f.get(\"index\", i) for i, f in enumerate(frames)]" not in src, (
        "v3 仍把全部帧作为 touched 传入 —— recompose 会抹掉缓存黑场记录"
    )
    assert "write_placeholder_sidecar(" in src

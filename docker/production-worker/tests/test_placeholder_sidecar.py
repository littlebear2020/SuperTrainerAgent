"""行为锁：占位（黑场）帧必须被统计并上报，不得静默成功。

线上实测（2026-07-29）：素材全失效时每帧降级黑场，worker 唯一守卫
`if not segments` 放行——占位帧也 append 进 segments——
任务以 status=ready / progress=100 结束，与完美渲染完全不可区分。
用户白等一次完整渲染才发现「这片子是黑的」。

现有 visual_coverage.json 覆盖不了：由 gateway 在 worker 前写、只有 ai_video 源写、
对 worker 侧下载失败失明，且 coverage>=1.0 时会被 unlink。
"""

import json

from pipelines.video_gen_ai_visual import _write_placeholder_sidecar


def test_sidecar_written_when_placeholders_exist(tmp_path):
    _write_placeholder_sidecar(tmp_path, [0, 3], total=5)

    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data == {"indices": [0, 3], "total": 5}


def test_sidecar_absent_when_no_placeholders(tmp_path):
    """无占位时不写文件——避免 gateway 每次都读到空告警。"""
    _write_placeholder_sidecar(tmp_path, [], total=5)
    assert not (tmp_path / "placeholder_frames.json").exists()


def test_sidecar_removed_when_rerender_is_clean(tmp_path):
    """上次有占位、这次全好 → 必须删掉旧 sidecar，否则告警永久残留。"""
    _write_placeholder_sidecar(tmp_path, [1], total=3)
    assert (tmp_path / "placeholder_frames.json").exists()

    _write_placeholder_sidecar(tmp_path, [], total=3)
    assert not (tmp_path / "placeholder_frames.json").exists()


def test_sidecar_write_failure_is_swallowed(tmp_path):
    """写 sidecar 失败不得挂渲染（对齐同管线其他 sidecar 的容错）。"""
    blocked = tmp_path / "placeholder_frames.json"
    blocked.mkdir()  # 目录占位 → write_text 必失败
    _write_placeholder_sidecar(tmp_path, [0], total=1)  # 不抛


def test_sidecar_indices_are_sorted(tmp_path):
    """乱序登记也要输出升序——前端按帧号展示。"""
    _write_placeholder_sidecar(tmp_path, [4, 1, 2], total=6)
    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [1, 2, 4]


def test_incremental_merge_keeps_untouched_placeholders(tmp_path):
    """增量渲染只碰改动帧——未触碰的旧占位帧结论必须保留。

    否则「95 帧黑场 + 只重渲第 3 帧」会写出「无占位」= 报全部正常，
    正是本 sidecar 要消灭的假成功。
    """
    _write_placeholder_sidecar(tmp_path, [0, 1, 2, 3], total=4)

    # 增量重渲第 3 帧并成功（本次无占位），只应移除帧 3 的结论
    _write_placeholder_sidecar(tmp_path, [], total=4, touched=[3])

    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [0, 1, 2], "未触碰的帧 0/1/2 仍是黑场，不得被抹掉"


def test_incremental_merge_adds_newly_failed_frame(tmp_path):
    """增量重渲又失败 → 该帧继续记为占位。"""
    _write_placeholder_sidecar(tmp_path, [1], total=3)
    _write_placeholder_sidecar(tmp_path, [2], total=3, touched=[2])
    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [1, 2]


def test_incremental_clears_sidecar_when_all_touched_and_clean(tmp_path):
    """增量覆盖了全部旧占位帧且都成功 → sidecar 应被删除（不留假告警）。"""
    _write_placeholder_sidecar(tmp_path, [0, 1], total=2)
    _write_placeholder_sidecar(tmp_path, [], total=2, touched=[0, 1])
    assert not (tmp_path / "placeholder_frames.json").exists()


def test_full_render_still_replaces_wholesale(tmp_path):
    """全量渲染（touched=None）重算所有帧，旧结论整份作废。"""
    _write_placeholder_sidecar(tmp_path, [0, 1, 2], total=3)
    _write_placeholder_sidecar(tmp_path, [], total=3)  # 全量且全好
    assert not (tmp_path / "placeholder_frames.json").exists()


def test_incremental_merge_survives_corrupt_previous_sidecar(tmp_path):
    """旧 sidecar 损坏时只用本次结论，不抛。"""
    (tmp_path / "placeholder_frames.json").write_text("{ not json", encoding="utf-8")
    _write_placeholder_sidecar(tmp_path, [1], total=3, touched=[1])
    data = json.loads((tmp_path / "placeholder_frames.json").read_text(encoding="utf-8"))
    assert data["indices"] == [1]


def test_placeholder_branches_register_indices():
    """源码锁：三个黑场分支都要登记，且成品前写出 sidecar。

    修前 worker 有三处 _make_black_subtitle_clip（全渲占位/全渲下载失败/增量下载失败）
    却零上报，全帧黑场照样 ready。
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "pipelines/video_gen_ai_visual.py").read_text(encoding="utf-8")
    assert src.count("placeholder_indices.append(") >= 3, "三个黑场分支（全渲占位/全渲失败/增量失败）都须登记占位帧"
    assert "_write_placeholder_sidecar(\n            task_dir,\n            placeholder_indices," in src, "成品前须写出 placeholder_frames.json"
    assert "touched=valid_changed if use_incremental else None" in src, "增量渲染须传 touched 做逐帧合并，否则重渲 1 帧会抹掉其余帧的黑场事实"

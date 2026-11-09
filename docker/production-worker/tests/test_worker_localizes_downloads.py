"""行为锁：worker 下载的素材必须留下来，并写回 storyboard.json。

线上坐实（2026-07-29）：worker 下载到 task_dir/source/shot_NNN.png，
写 sidecar 记该路径，然后在 finally 里 shutil.rmtree(source_dir)
——sidecar 出生即悬空。且 worker 全仓零 storyboard.json 写入点，
下载结果无处留存，每次重渲都得重新依赖远程 URL。

服务器盘面实测：ai_image 任务 sidecar 指向文件存在数全为 0；
ai_video 走 segments/（不删）故全部存活。24h 后 TOS 签名过期 → 全片黑场。

本测试锁住：下载落地在会存活的目录 + 路径写回 storyboard。
"""

import json

from pipelines.video_gen_ai_visual import _persist_local_visual


def _write_sb(task_dir, frames):
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {}, "frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_persist_writes_local_visual_path(tmp_path):
    _write_sb(tmp_path, [{"index": 0, "narration": "a", "visual_url": "https://cdn/x.png"}])

    _persist_local_visual(tmp_path, 0, "visuals/dl_0000.png")

    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["local_visual_path"] == "visuals/dl_0000.png"
    # 不得动其他字段
    assert data["frames"][0]["visual_url"] == "https://cdn/x.png"
    assert data["title"] == "t"


def test_persist_matches_by_index_not_position(tmp_path):
    """按 frame 的 index 字段匹配，不按数组位置（帧可能被增删过）。"""
    _write_sb(tmp_path, [{"index": 5, "narration": "a"}, {"index": 2, "narration": "b"}])

    _persist_local_visual(tmp_path, 2, "visuals/dl_0002.png")

    frames = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))["frames"]
    assert frames[1]["local_visual_path"] == "visuals/dl_0002.png"
    assert "local_visual_path" not in frames[0]


def test_persist_missing_storyboard_is_noop(tmp_path):
    """无 storyboard.json 时静默跳过——不得让渲染因写回失败而崩。"""
    _persist_local_visual(tmp_path, 0, "visuals/dl_0000.png")  # 不抛


def test_persist_unknown_index_is_noop(tmp_path):
    _write_sb(tmp_path, [{"index": 0, "narration": "a"}])
    _persist_local_visual(tmp_path, 99, "visuals/dl_0099.png")
    frames = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))["frames"]
    assert "local_visual_path" not in frames[0]


def test_persist_corrupt_storyboard_is_noop(tmp_path):
    task_dir = tmp_path
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text("{not json", encoding="utf-8")
    _persist_local_visual(task_dir, 0, "visuals/dl_0000.png")  # 不抛


def test_persist_is_atomic_no_tmp_left(tmp_path):
    """原子替换：不留 .tmp 残file。"""
    _write_sb(tmp_path, [{"index": 0, "narration": "a"}])
    _persist_local_visual(tmp_path, 0, "visuals/dl_0000.png")
    assert list(tmp_path.glob("*.tmp")) == []


def test_download_target_is_not_under_source_dir():
    """源码锁：image 下载目标不得再落在被 rmtree 的 source/ 下。

    重构时预期会红——确认新路径同样在 finally 里存活后更新断言即可。
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "pipelines/video_gen_ai_visual.py").read_text(encoding="utf-8")
    # 注意：不能全局禁 `source_dir / f"shot_..."`——_download_and_prepare 仍合法用
    # source_dir（增量分支不 rmtree 它）。判据取 full-render image 分支独有的 sidecar 值：
    # 它记 source/ 前缀就说明素材落在会被 rmtree 的目录里。
    assert 'local_sources[seg_idx] = f"source/shot_' not in src, "image sidecar 仍指向 source/，而 finally 会 rmtree(source_dir) —— 素材出生即被删"
    assert 'f"visuals/dl_{seg_idx:04d}.png"' in src, "image 下载应改落 visuals/dl_NNNN.png（与 segments/ 同存活性）"
    assert "_persist_local_visual" in src, "下载后须把路径写回 storyboard.json"


def test_mp4_path_never_written_back_for_image_frames():
    """源码锁：segments/*.mp4 只能写给 video 帧。

    image 帧的 visual_type 在 storyboard 里仍是 "image"，若把 mp4 记成它的
    local_visual_path，下次渲染 _prepare_local_segment 会走 type=="image" 分支
    调 _image_to_video → `ffmpeg -loop 1 -i seg_000.mp4` → "Option loop not found"
    直接抛。而该调用位于占位兜底 try 之外 → 整任务 failed，且 _cleanup_task_dir
    会 rmtree segments/ 把全部缓存段一并销毁。
    自毁式：写回的目的正是抵抗 URL 过期，却把要保护的帧变成永久坏帧。
    """
    from pathlib import Path as _P

    src = (_P(__file__).resolve().parents[1] / "pipelines/video_gen_ai_visual.py").read_text(encoding="utf-8")
    lines = src.split("\n")

    checked = 0
    for i, line in enumerate(lines):
        if "_persist_local_visual(" not in line or "def _persist_local_visual" in line:
            continue
        if "segments/seg_" not in line:
            continue  # visuals/dl_*.png 的写回（image 帧）本就正确
        checked += 1
        # 直接包住这行的那个 if 必须是 video 类型判断。按缩进向上找第一个
        # 缩进更浅的 if——用宽 window 搜 '== "video"' 会被上游无关的
        # `if asset["type"] == "video":`（归一分辨率那处）蒙过去。
        indent = len(line) - len(line.lstrip())
        guard = None
        for prev in reversed(lines[:i]):
            if not prev.strip():
                continue
            prev_indent = len(prev) - len(prev.lstrip())
            if prev_indent < indent and prev.lstrip().startswith(("if ", "elif ")):
                guard = prev.strip()
                break
            if prev_indent < indent:
                break  # 遇到更浅的非 if 语句 → 这行没有直接包裹的条件
        assert guard is not None and '== "video"' in guard, f'line {i + 1}: segments/*.mp4 写回缺直接的 video 类型守卫（实际包裹条件: {guard!r}）——image 帧会被写成 mp4 本地路径，下次渲染必崩'

    assert checked >= 2, f"预期至少 2 处 segments/*.mp4 写回（全渲 video 分支 + 增量分支），实际 {checked}"


def test_image_frame_with_mp4_local_path_would_break_next_render():
    """反向证明上一条为何重要：image 帧 + mp4 本地路径 → 下次渲染走错分支。"""
    from pipelines.video_gen_ai_visual import _collect_visual_assets

    asset = _collect_visual_assets(
        {"frames": [{"index": 0, "narration": "a", "visual_type": "image", "visual_url": "https://cdn/x.png", "local_visual_path": "segments/seg_000.mp4"}]}
    )[0]
    # local 优先于 url，且类型是 image → _prepare_local_segment 必走 _image_to_video
    assert asset["type"] == "image"
    assert asset["local_path"] == "segments/seg_000.mp4"

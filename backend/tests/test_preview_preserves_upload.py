"""行为锁：自动/批量预览不得覆盖用户上传的画面。

复核确认（2026-07-29）：渲染路径有守卫（_frames_to_regenerate 内 P0-1），
预览路径没有——
  - upload_frame_visual 写 local_visual_path 并把 visual_url 显式置 None
  - preview_all_frames 选帧条件 `not f.visual_url` → 恰好精准命中上传帧
  - _generate_frame_preview 的 _updater 无条件覆写 visual_url/visual_type，
    本地化成功还覆写 local_visual_path

线上未复现是因为自动触发仅 draft 态且一次性，用户测的是 ready 态旧任务；
手动点「一键生成全部预览」仍会踩。
"""

import pytest

from app.gateway.video_gen.service import frame_has_user_visual


def test_uploaded_frame_is_user_visual():
    """上传帧：local_visual_path 有值 + visual_url=None。"""
    assert frame_has_user_visual({"index": 0, "local_visual_path": "visuals/frame_000.png", "visual_url": None}) is True


def test_ai_generated_frame_is_not_user_visual():
    assert frame_has_user_visual({"index": 0, "visual_url": "https://cdn/x.png"}) is False


def test_empty_frame_is_not_user_visual():
    assert frame_has_user_visual({"index": 0}) is False


def test_localized_ai_frame_is_not_user_visual():
    """gateway 本地化的 AI 图（img_ 前缀）不算用户素材——它可以被重新预览覆盖。"""
    assert frame_has_user_visual({"index": 0, "local_visual_path": "visuals/img_0000.png", "visual_url": "https://cdn/x.png"}) is False


def test_localized_ai_frame_without_url_still_not_user_visual():
    """img_ 前缀 + 无 url（本地化后 url 被清）也不得误判为用户素材。

    判据是前缀而非「有 local 无 url」——否则重新预览对本地化 AI 图永久失效。
    """
    assert frame_has_user_visual({"index": 0, "local_visual_path": "visuals/img_0000.png", "visual_url": None}) is False


def test_dataclass_frame_supported():
    from app.gateway.video_gen.storyboard import StoryboardFrame

    f = StoryboardFrame(index=0, narration="a", local_visual_path="visuals/frame_000.png")
    assert frame_has_user_visual(f) is True


def test_pending_selection_excludes_uploads():
    """复现 preview_all_frames 的选帧条件——加守卫后上传帧不得入选。"""
    frames = [
        {"index": 0, "local_visual_path": "visuals/frame_000.png", "visual_url": None},  # 上传
        {"index": 1, "visual_url": None},  # 待预览
        {"index": 2, "visual_url": "https://cdn/x.png"},  # 已有
    ]
    pending = [f["index"] for f in frames if not f.get("visual_url") and not frame_has_user_visual(f)]
    assert pending == [1]


def test_generate_preview_refuses_uploaded_frame(tmp_path):
    """_generate_frame_preview 入口守卫：上传帧直接拒绝，不烧生图配额。"""
    import json

    from app.gateway.video_gen.service import assert_frame_previewable_not_upload

    (tmp_path / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [{"index": 0, "narration": "a", "local_visual_path": "visuals/frame_000.png", "visual_url": None}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="上传"):
        assert_frame_previewable_not_upload(tmp_path, 0)


def test_generate_preview_allows_normal_frame(tmp_path):
    """普通待预览帧不得被守卫拦下——守卫只针对上传帧。"""
    import json

    from app.gateway.video_gen.service import assert_frame_previewable_not_upload

    (tmp_path / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {}, "frames": [{"index": 0, "narration": "a"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert_frame_previewable_not_upload(tmp_path, 0)  # 不抛即通过


def test_guard_is_silent_when_storyboard_unreadable(tmp_path):
    """无 storyboard.json / 坏 JSON 时守卫放行，不把预览入口连带堵死。"""
    from app.gateway.video_gen.service import assert_frame_previewable_not_upload

    assert_frame_previewable_not_upload(tmp_path, 0)  # 文件不存在
    (tmp_path / "storyboard.json").write_text("{ not json", encoding="utf-8")
    assert_frame_previewable_not_upload(tmp_path, 0)  # 坏 JSON


def test_upload_guard_runs_synchronously_on_the_preview_route():
    """源码锁：上传守卫必须在路由里同步跑，不能只留在后台任务里。

    单帧预览路由调度后台任务后立刻返回 202；若守卫只在
    _generate_frame_preview（后台）里，拒绝会被 _write_preview_error 写成
    preview_error → 前端显示红色「预览失败」。但这不是失败，是按设计保护了
    用户上传的图。validate_frame_previewable 的 docstring 已经写明这个同步
    检查点的用意（「保证参数类错误仍即时 400 而非异步静默」），守卫要并列在那里。
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/gateway/routers/video_gen.py").read_text(encoding="utf-8")
    # 找到同步校验块：validate_frame_previewable 与上传守卫必须在同一个 try 内
    block = src.split("validate_frame_previewable(task_dir, frame_index)")[1].split("except ValueError")[0]
    assert "assert_frame_previewable_not_upload(task_dir, frame_index)" in block, "上传守卫须与 validate_frame_previewable 同步并列（同一 try），否则拒绝会伪装成预览失败"

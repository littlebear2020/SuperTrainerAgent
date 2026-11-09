"""Task 14: 帧操作抽出后行为不变（守护测试）。"""

from app.gateway.video_gen.frame_ops import frames_to_regenerate
from app.gateway.video_gen.storyboard import StoryboardFrame


def _f(i: int, url: str | None, vtype: str | None, vsource: str | None = None, local: str | None = None) -> StoryboardFrame:
    return StoryboardFrame(index=i, narration="n", visual_url=url, visual_type=vtype, visual_source=vsource, local_visual_path=local)


def test_missing_visual_regenerates() -> None:
    assert frames_to_regenerate([_f(0, None, None)], "ai_video") == [0]


def test_uploaded_local_asset_preserved() -> None:
    """P0-1：用户上传的素材不得被当「无视觉」重抽覆盖。"""
    assert frames_to_regenerate([_f(0, None, None, local="visuals/frame_0000.png")], "ai_video") == []


def test_preview_image_regenerates_for_ai_video() -> None:
    """G2：ai_video 任务里的预览图（image）必须重生成 video。"""
    assert frames_to_regenerate([_f(0, "http://x", "image")], "ai_video") == [0]


def test_existing_video_skipped_for_ai_video() -> None:
    assert frames_to_regenerate([_f(0, "http://x", "video")], "ai_video") == []


def test_per_frame_visual_source_wins_over_task_type() -> None:
    """mixed 任务：逐帧 visual_source 决定期望类型，非单一 task_type。"""
    frames = [_f(0, "http://x", "image", vsource="ai_image"), _f(1, "http://y", "image", vsource="ai_video")]
    assert frames_to_regenerate(frames, "ai_video") == [1]


def test_service_still_exposes_old_private_name() -> None:
    """service.py 的 5 处调用点走私名别名，搬迁必须零行为变化。"""
    from app.gateway.video_gen import service

    assert service._frames_to_regenerate is frames_to_regenerate

"""W1 组级扩展：ai_image/ai_video 的 scene_groups 转场也必须强制 none——
SceneGroup.transition_in="fade"/0.8s dataclass 默认经 asdict 绕过 config 级 W1，
v3_segmented 组内 xfade 重叠使视频<音频（与 W1 同型漂移）。"""

from app.gateway.video_gen.service import build_worker_payload
from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame


def _sb(video_type, with_groups=True):
    groups = []
    if with_groups:
        groups = [
            SceneGroup(index=0, title="g0", frame_indices=[0, 1]),  # dataclass 默认 transition_in="fade", transition_duration=0.8
            SceneGroup(index=1, title="g1", frame_indices=[2]),
        ]
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1080, height=1920),
        frames=[StoryboardFrame(index=i, narration=f"n{i}", duration=3.0) for i in range(3)],
        scene_groups=groups,
    )


def test_ai_image_groups_forced_transition_none():
    p = build_worker_payload(_sb("ai_image"), subtitle_path=None, audio_concat_path=None)
    assert p["config"]["transition"] == "none"  # W1 既有行为不回归
    for g in p["scene_groups"]:
        assert g["transition_in"] == "none", "组级 transition_in 必须一并强制 none（绕过 W1 的洞）"
        assert g["transition_out"] == "none"


def test_ai_video_groups_forced_transition_none():
    p = build_worker_payload(_sb("ai_video"), subtitle_path=None, audio_concat_path=None)
    for g in p["scene_groups"]:
        assert g["transition_in"] == "none"
        assert g["transition_out"] == "none"


def test_vimax_groups_untouched():
    """E（2026-08-31 转场清理）后语义：vimax 路径默认随 dataclass 新默认 none
    （转场 UI 已删、不再下发 fade）；**显式** fade 仍原样放行——W1 只压
    ai_image/ai_video，显式能力保留给存量草稿与 API 调用方。"""
    p = build_worker_payload(_sb("vimax"), subtitle_path=None, audio_concat_path=None)
    assert p["scene_groups"][0]["transition_in"] == "none"  # dataclass 默认已改 none

    groups = [SceneGroup(index=0, title="g0", frame_indices=[0], transition_in="fade", transition_out="fade")]
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="vimax", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="n0", duration=3.0)],
        scene_groups=groups,
    )
    p2 = build_worker_payload(sb, subtitle_path=None, audio_concat_path=None)
    assert p2["scene_groups"][0]["transition_in"] == "fade", "vimax 显式 fade 不被压制（W1 边界不变）"

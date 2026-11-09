"""W1: build_worker_payload 对 ai_image/ai_video 强制 transition=none（消转场漂移）。

根因：默认 transition=fade 的 xfade 重叠使视频<音频→讲解对不上。ai_image/ai_video
强制 none（build_worker_payload 处，不改 schema 默认）；vimax/remotion 不受影响。
"""

from app.gateway.video_gen.service import build_worker_payload
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(video_type, transition="fade"):
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1080, height=1920, transition=transition),
        frames=[StoryboardFrame(index=0, narration="n")],
    )


def test_ai_image_forces_transition_none():
    """ai_image 即使 config 传 fade，payload 须 none。"""
    payload = build_worker_payload(_sb("ai_image", transition="fade"), subtitle_path=None, audio_concat_path=None)
    assert payload["config"]["transition"] == "none", "ai_image 须强制 transition=none"


def test_ai_video_forces_transition_none():
    payload = build_worker_payload(_sb("ai_video", transition="fade"), subtitle_path=None, audio_concat_path=None)
    assert payload["config"]["transition"] == "none", "ai_video 须强制 transition=none"


def test_vimax_keeps_transition():
    """vimax 不受影响（走各自端点，保留 fade）。"""
    payload = build_worker_payload(_sb("vimax", transition="fade"), subtitle_path=None, audio_concat_path=None)
    assert payload["config"]["transition"] == "fade"

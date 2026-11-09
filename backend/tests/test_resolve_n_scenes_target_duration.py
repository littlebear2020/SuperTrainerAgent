"""一.4 根因：用户未显式设 n_scenes 时，target_duration 必须决定帧数。

修前 CreateVideoRequest.n_scenes 默认 5，create_video 用 body.model_dump()
把默认 5 漏进 payload，_resolve_n_scenes 优先用显式 n_scenes → 恒为 5，
完全无视 target_duration/seconds_per_frame。
"""

from app.gateway.video_gen.schemas import CreateVideoRequest
from app.gateway.video_gen.service import _resolve_n_scenes


def test_target_duration_decides_frame_count_when_n_scenes_unset():
    """只设 target_duration、不设 n_scenes → 帧数按 target 算（>=8），不是默认 5。"""
    req = CreateVideoRequest(video_type="ai_image", topic="x", target_duration=60.0)
    payload = req.model_dump()
    n = _resolve_n_scenes(payload)
    # 60s / 8s 下限 = 至少 8 帧（修前因 n_scenes 默认 5 漏进而恒为 5）
    assert n >= 8, f"target_duration=60 应给出 >=8 帧（修前恒为 5）: n={n}, payload.n_scenes={payload.get('n_scenes')!r}"


def test_explicit_n_scenes_still_wins():
    """用户显式设 n_scenes 时仍优先（不回归）。"""
    req = CreateVideoRequest(video_type="ai_image", topic="x", target_duration=60.0, n_scenes=12)
    payload = req.model_dump()
    assert _resolve_n_scenes(payload) == 12


def test_seconds_per_frame_with_target_decides():
    """设了 seconds_per_frame + target_duration → ceil(target/spf)，且 spf 不得在 schema 边界被丢。"""
    req = CreateVideoRequest(video_type="ai_image", topic="x", target_duration=30.0, seconds_per_frame=5)
    payload = req.model_dump()
    # ceil(30/5) = 6（修前 spf 被 schema 丢弃 → 走 target-only max(8,30/8)=8 → 与 6 区分）
    assert _resolve_n_scenes(payload) == 6
    # spf 必须真正进 payload（修前 schema 没这个字段，前端值被丢）
    assert payload.get("seconds_per_frame") == 5

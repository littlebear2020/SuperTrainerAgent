"""P1-E11: spf 草稿路径透传 + fixed 模式显式 n_scenes 优先。

修复前：_target_scenes 仅当 spf+target_duration 同时存在才调 _resolve_n_scenes
→ 用户显式 n_scenes 被文长推断覆盖；_generate_draft_audio 与 _render_from_draft
不传 spf → 主路径单镜时长设置无效（死代码）。
"""

import json
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.service import VideoGenService, _target_scenes_for_fixed
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_explicit_n_scenes_wins_over_text_length():
    """fixed 模式只传 n_scenes（无 spf/target）→ 用显式值（修复前被文长推断覆盖）。"""
    assert _target_scenes_for_fixed({"n_scenes": 12}, "一句话。两句话。") == 12


def test_spf_target_derivation_still_works():
    """spf+target 仍走 _resolve_n_scenes（ceil(50/5)=10，下限逻辑保底）。"""
    n = _target_scenes_for_fixed({"seconds_per_frame": 5, "target_duration": 50}, "任意文本")
    assert n >= 10


def test_text_length_fallback_when_nothing_explicit():
    """无任何显式参数 → 走 _target_scenes_from_len。"""
    n = _target_scenes_for_fixed({}, "短文本。")
    assert n >= 1


@pytest.mark.asyncio
async def test_draft_audio_passes_seconds_per_frame(tmp_path):
    """_generate_draft_audio 必须把 payload.seconds_per_frame 透传给 _synthesize_audio。"""
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1152, height=2048))
    sb.frames.append(StoryboardFrame(index=0, narration="n0"))
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict()), encoding="utf-8")

    svc = VideoGenService.__new__(VideoGenService)
    captured: dict = {}

    async def fake_synth(storyboard, task_dir, voice_id, seconds_per_frame=None, task_id=None, **kwargs):
        # **kwargs 收计划 A（2026-10-18）新增的 stage_hook 等可选参数——stub 签名跟随生产契约
        captured["spf"] = seconds_per_frame
        return ("audio.mp3", "sub.srt")

    svc._synthesize_audio = fake_synth

    async def fake_merge(storyboard, task_dir, **kwargs):
        return 1, []  # 全部帧合并成功（2026-10-06 漂移批：返回 (merged_count, drifted)，**kwargs 收 verify_drift）

    svc._merge_audio_into_storyboard = fake_merge

    class _Repo:
        async def get(self, task_id, user_id=None):
            return SimpleNamespace(payload=json.dumps({"seconds_per_frame": 6}), status="draft")

        async def update(self, task_id, **kw):
            pass

    svc.repo = _Repo()

    await svc._generate_draft_audio("t1", task_dir, user_id="u1")

    assert captured.get("spf") == 6, f"_synthesize_audio 必须收到 seconds_per_frame=6，实际 {captured.get('spf')!r}"


@pytest.mark.asyncio
async def test_render_from_draft_passes_seconds_per_frame(tmp_path, monkeypatch):
    """_render_from_draft 同样透传 payload.seconds_per_frame。"""
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1152, height=2048))
    sb.frames.append(StoryboardFrame(index=0, narration="n0"))
    task_dir = tmp_path / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict()), encoding="utf-8")

    svc = VideoGenService.__new__(VideoGenService)
    captured: dict = {}

    async def fake_synth(storyboard, task_dir, voice_id, seconds_per_frame=None, task_id=None, **kwargs):
        # **kwargs 收计划 A（2026-10-18）新增的 stage_hook 等可选参数——stub 签名跟随生产契约
        captured["spf"] = seconds_per_frame
        return ("audio.mp3", "sub.srt")

    svc._synthesize_audio = fake_synth
    # 短路后续渲染步骤——本测试只断言 spf 透传
    svc._merge_audio_into_storyboard = lambda *a, **k: _async_zero()
    svc._update_storyboard = lambda *a, **k: _async_none()

    async def _async_zero():
        return 0

    async def _async_none():
        return None

    async def progress(**kw):
        pass

    svc.progress = progress
    # 假 _config 供 _render_from_draft 后续步骤引用（本测试会因后续步骤缺失而早退，
    # 但 spf 在第一个 _synthesize_audio 调用已捕获）
    svc._config = SimpleNamespace()

    try:
        await svc._render_from_draft("t1", "ai_image", task_dir, progress, {"seconds_per_frame": 7}, user_id="u1")
    except Exception:
        pass  # 后续视觉/compose 步骤允许失败——本测试只断言 spf 透传

    assert captured.get("spf") == 7, f"_render_from_draft 必须透传 seconds_per_frame=7，实际 {captured.get('spf')!r}"

"""出 worker payload 前：frame.duration 必须与音频文件实测一致（漂移兜底，根因环 3）。"""

import json
from unittest.mock import AsyncMock

import pytest

# 2026-08-03 Task 15：reconcile/probe 已搬到 audio_flow.py。probe 必须 patch **源模块**
# ——reconcile 在 audio_flow 内解析该名字，patch service 上的 re-export 别名不生效
# （会静默变成「桩没装上、真跑 ffprobe」，两处实测到）。
from app.gateway.video_gen import audio_flow
from app.gateway.video_gen import service as svc_mod
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb_with_audio(durations):
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=i, narration=f"句{i}", duration=d, audio_path=f"/a/{i}.mp3", speech_duration=d) for i, d in enumerate(durations)],
    )


def test_stale_durations_corrected(monkeypatch):
    """记录时长 < 音频实测（跨代覆盖态）→ 重推为实测 + recompute + True。"""
    probes = {"/a/0.mp3": 9.6, "/a/1.mp3": 10.2}
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: probes.get(p, 0.0))
    sb = _sb_with_audio([8.55, 7.2])
    assert svc_mod._reconcile_durations_with_audio(sb, None) is True
    assert sb.frames[0].duration == 9.6
    assert sb.frames[0].speech_duration == 9.6
    assert sb.frames[1].duration == 10.2
    assert sb.total_duration == pytest.approx(19.8)


def test_matching_durations_untouched(monkeypatch):
    """记录≈实测（容差 0.3s 内）→ False 且不改。"""
    probes = {"/a/0.mp3": 8.6, "/a/1.mp3": 7.2}
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: probes.get(p, 0.0))
    sb = _sb_with_audio([8.55, 7.2])
    assert svc_mod._reconcile_durations_with_audio(sb, None) is False
    assert sb.frames[0].duration == 8.55


def test_missing_audio_path_and_probe_failure_skipped(monkeypatch):
    """无 audio_path 的帧跳过；探测失败(0.0)跳过不误改。"""
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 0.0)
    sb = _sb_with_audio([8.55, 7.2])
    sb.frames[0].audio_path = None
    assert svc_mod._reconcile_durations_with_audio(sb, None) is False
    assert sb.frames[1].duration == 7.2


def test_spf_padded_files_not_touched(monkeypatch):
    """spf 场景不误伤（评审⑥）：音频文件已在单帧层补静音到 spf
    （service.py `_pad_audio_to_duration` 就地 replace）→ 文件实测 = spf = 记录
    → reconcile 不触发。"""
    probes = {"/a/0.mp3": 8.0, "/a/1.mp3": 8.0}
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: probes.get(p, 0.0))
    sb = _sb_with_audio([8.0, 8.0])  # spf=8：duration=spf=8，音频文件已 pad 到 8s
    assert svc_mod._reconcile_durations_with_audio(sb, None) is False
    assert sb.frames[0].duration == 8.0


@pytest.mark.asyncio
async def test_recompose_branch_reconciles_before_payload(tmp_path, monkeypatch):
    """_run_pipeline recompose_only 分支：读盘 storyboard 时长陈旧 → 出 payload 前修正
    + 重建 SRT + 落盘（vg_93e83a7ee259 的复现路径）。"""
    from app.gateway.video_gen.service import VideoGenService

    sb = _sb_with_audio([8.55, 7.2])
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "audio.mp3").write_bytes(b"concat")
    (tmp_path / "subtitle.srt").write_text("old", encoding="utf-8")

    probes = {"/a/0.mp3": 9.6, "/a/1.mp3": 10.2}
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: probes.get(p, 0.0))
    srt_calls = []
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: srt_calls.append(1))

    svc = VideoGenService.__new__(VideoGenService)
    svc._sb_write_lock = __import__("asyncio").Lock()
    result = await svc._run_pipeline("vg_x", "u", "ai_image", {"recompose_only": True}, tmp_path, AsyncMock())

    assert result.payload["frames"][0]["duration"] == 9.6, "recompose 出 payload 前未校验时长"
    assert result.payload["frames"][1]["duration"] == 10.2
    assert srt_calls, "时长被修正后必须重建 SRT（否则字幕仍按旧轴）"
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["duration"] == 9.6, "修正结果未落盘"

"""单帧重生模式分离（2026-07-27 音视频解耦）：audio_only / visual_only / all。"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _setup_ready_task(tmp_path, svc, narration="原始旁白"):
    """磁盘 storyboard + ready task + 预置 audio.mp3/subtitle.srt，返回 task_id。"""
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration=narration,
                image_prompt="a cat",
                visual_url="https://cdn.example.com/original.jpg",
                visual_type="image",
                audio_path=str(tmp_path / "audio" / "f0.mp3"),
                duration=2.0,
            ),
        ],
        total_duration=2.0,
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "audio").mkdir(exist_ok=True)
    (tmp_path / "audio" / "f0.mp3").write_bytes(b"fake-mp3")
    (tmp_path / "audio.mp3").write_bytes(b"concat-mp3")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n原始旁白\n", encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({"engine": "auto"})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.add_dirty_frame = AsyncMock()
    return "vg_test"


def _fake_tts(calls):
    from app.gateway.video_gen.orchestration.tts import TTSResult

    async def _synth(text, *, voice_id, speed, output_path, fallback_voice=None):
        calls.append({"text": text, "voice_id": voice_id, "speed": speed})
        Path(output_path).write_bytes(b"new-tts-mp3")
        return TTSResult(audio_path=output_path, srt="", duration=2.5, actual_voice_id=voice_id)

    return _synth


def _mutate_narration_on_disk(tmp_path, text):
    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    data["frames"][0]["narration"] = text
    (tmp_path / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.asyncio
async def test_audio_only_does_not_touch_visual(tmp_path, monkeypatch):
    """audio_only：visual.render 不得调用，visual_url 保持原值，TTS 真跑。

    同时回归 2026-07-27 复核发现的硬崩：若 mode 守卫未把 effective_type 等
    跨段绑定提到守卫外，函数末尾 `if effective_type == "remotion"` 求值即 NameError
    （本用例 await 直接抛、而非走断言分支）。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)
    # 改旁白（磁盘）→ CAS miss → TTS 必须重跑
    _mutate_narration_on_disk(tmp_path, "全新的旁白内容")

    tts_calls = []
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _fake_tts(tts_calls))
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", AsyncMock())
    visual_factory = MagicMock()
    monkeypatch.setattr(svc, "_make_visual_source", visual_factory)

    await svc._regenerate_frame(task_id, "u1", 0, tmp_path, mode="audio_only")

    visual_factory.assert_not_called()
    assert len(tts_calls) == 1
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][0]["visual_url"] == "https://cdn.example.com/original.jpg", "画面被清空/覆盖"


@pytest.mark.asyncio
async def test_visual_only_does_not_touch_audio(tmp_path, monkeypatch):
    """visual_only：tts.synthesize 不得调用，audio.mp3/subtitle.srt 字节不变。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)

    tts_calls = []
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _fake_tts(tts_calls))
    fake_visual = AsyncMock()
    monkeypatch.setattr(svc, "_make_visual_source", MagicMock(return_value=fake_visual))

    await svc._regenerate_frame(task_id, "u1", 0, tmp_path, mode="visual_only")

    fake_visual.render.assert_awaited_once()
    assert tts_calls == [], "visual_only 不得触发 TTS"
    assert (tmp_path / "audio.mp3").read_bytes() == b"concat-mp3"
    assert (tmp_path / "subtitle.srt").read_text(encoding="utf-8") == "1\n00:00:00,000 --> 00:00:02,000\n原始旁白\n"


@pytest.mark.asyncio
async def test_default_mode_all_regenerates_both(tmp_path, monkeypatch):
    """缺省 mode=all 保持现行为：画面+音频都重生（向后兼容）。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)
    _mutate_narration_on_disk(tmp_path, "改了旁白触发 CAS miss")

    tts_calls = []
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _fake_tts(tts_calls))
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", AsyncMock())
    fake_visual = AsyncMock()
    monkeypatch.setattr(svc, "_make_visual_source", MagicMock(return_value=fake_visual))

    await svc._regenerate_frame(task_id, "u1", 0, tmp_path)

    fake_visual.render.assert_awaited_once()
    assert len(tts_calls) == 1, "all 模式必须画面+音频都重生"


def test_regenerate_mode_rejects_bad_value():
    """mode 白名单外的值 → pydantic 校验拒绝（端点层 422）。"""
    from app.gateway.routers.video_gen import RegenerateFrameRequest

    with pytest.raises(Exception):
        RegenerateFrameRequest(mode="banana")
    assert RegenerateFrameRequest().mode == "all"
    assert RegenerateFrameRequest(mode="audio_only").mode == "audio_only"


@pytest.mark.asyncio
async def test_audio_only_cas_miss_regenerates_srt_when_locked(tmp_path, monkeypatch):
    """2026-07-27 评审 D-F1（T13×T14×T15 交叉回归）：
    用户编辑字幕（locked=True）→ T14 端点改音色（不清锁）→ T13 重生配音
    CAS miss（音色进 CAS 键）。_audio_updater 只清磁盘 dict 的锁，内存
    storyboard.subtitle_locked 仍 True → 修前 _write_storyboard_srt 被守卫跳过，
    旧用户字幕残留进 recompose 成片（且磁盘已标解锁，状态自相矛盾）。
    修复后：磁盘/内存一致清锁，SRT 按新音频重新生成。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)

    # ① 用户手改字幕 → 置锁 + 写入用户版 SRT
    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    data["subtitle_locked"] = True
    # ② T14 改音色（update_storyboard_config 只 update config，不清锁）
    data["config"]["voice_id"] = "preset:147"
    (tmp_path / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n用户手改字幕\n", encoding="utf-8")

    tts_calls = []
    monkeypatch.setattr("app.gateway.video_gen.orchestration.tts.synthesize", _fake_tts(tts_calls))
    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", AsyncMock())

    # ③ T13 重生配音（audio_only）——CAS miss（新音色 → 新哈希）
    await svc._regenerate_frame(task_id, "u1", 0, tmp_path, mode="audio_only")

    assert tts_calls and tts_calls[0]["voice_id"] == "preset:147", "T14 的新音色必须生效"
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated.get("subtitle_locked") is False, "CAS miss 后磁盘锁必须清除（清锁点②）"
    srt = (tmp_path / "subtitle.srt").read_text(encoding="utf-8")
    assert "用户手改字幕" not in srt, "CAS miss 后旧用户字幕不得残留（内存/磁盘锁状态不一致）"
    assert "原始旁白" in srt, "SRT 必须按当前旁白重新生成"


@pytest.mark.asyncio
async def test_render_from_draft_narration_edits_clear_subtitle_lock(tmp_path, monkeypatch):
    """2026-07-27 评审 D-F2：_render_from_draft 的 _edits_updater 接收含 narration 的
    frame_edits（resume_draft ready+frame_edits 直达，绕过帧 PUT 清锁点①）时不清
    subtitle_locked → _synthesize_audio 产新 TTS 但 SRT 被锁守卫压制，旧字幕残留。
    修复后：narration 变更与清锁点①同源——磁盘+内存同步清锁。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)

    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    data["subtitle_locked"] = True
    (tmp_path / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    captured: dict = {}

    async def _fake_synth(storyboard, task_dir, voice_id, seconds_per_frame=None, task_id=None):
        captured["subtitle_locked"] = storyboard.subtitle_locked
        return (str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt"))

    monkeypatch.setattr(svc, "_synthesize_audio", _fake_synth)
    monkeypatch.setattr(svc, "_merge_audio_into_storyboard", AsyncMock(return_value=1))
    fake_visual = AsyncMock()
    monkeypatch.setattr(svc, "_make_visual_source", MagicMock(return_value=fake_visual))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", AsyncMock(return_value={}))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", MagicMock(return_value="v1"))
    monkeypatch.setattr("app.gateway.video_gen.service._reconcile_durations_with_audio", lambda *a, **k: False)

    payload = {"frame_edits": {"0": {"narration": "绕过帧 PUT 直改旁白"}}, "engine": "auto"}
    await svc._render_from_draft(task_id, "ai_image", tmp_path, AsyncMock(), payload)

    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][0]["narration"] == "绕过帧 PUT 直改旁白"
    assert updated.get("subtitle_locked") is False, "narration 变更必须清磁盘锁（与清锁点①同源）"
    assert captured.get("subtitle_locked") is False, "内存 storyboard 必须同步清锁，否则 SRT 重写被守卫压制"


@pytest.mark.asyncio
async def test_render_from_draft_null_narration_keeps_lock(tmp_path, monkeypatch):
    """评审二轮 FU（D-F2 边界）：frame_edits 含 {"narration": null} 时 key 存在
    但 val is None → 写入路径（val is not None 守卫）跳过 → narration 实际没变，
    锁不得清除（否则用户手改的精确 cue 时戳被无谓重写为整帧时长版）。
    清锁条件必须与写入条件对齐（key 存在 AND 值非 None）。"""
    svc = _make_service(tmp_path)
    task_id = _setup_ready_task(tmp_path, svc)

    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    data["subtitle_locked"] = True
    (tmp_path / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    captured: dict = {}

    async def _fake_synth(storyboard, task_dir, voice_id, seconds_per_frame=None, task_id=None):
        captured["subtitle_locked"] = storyboard.subtitle_locked
        return (str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt"))

    monkeypatch.setattr(svc, "_synthesize_audio", _fake_synth)
    monkeypatch.setattr(svc, "_merge_audio_into_storyboard", AsyncMock(return_value=1))
    monkeypatch.setattr(svc, "_make_visual_source", MagicMock(return_value=AsyncMock()))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", AsyncMock(return_value={}))
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", MagicMock(return_value="v1"))
    monkeypatch.setattr("app.gateway.video_gen.service._reconcile_durations_with_audio", lambda *a, **k: False)

    payload = {"frame_edits": {"0": {"narration": None}}, "engine": "auto"}
    await svc._render_from_draft(task_id, "ai_image", tmp_path, AsyncMock(), payload)

    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][0]["narration"] == "原始旁白", "null 不得覆盖原旁白"
    assert updated.get("subtitle_locked") is True, "narration 未实际变更时锁必须保持"
    assert captured.get("subtitle_locked") is True, "内存锁同样不得清除"

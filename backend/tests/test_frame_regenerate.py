"""Tests for frame regeneration endpoint and service method."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_regenerate_frame_success(tmp_path, hermetic_tts):
    """Successfully regenerate a single frame's visual."""
    svc = _make_service(tmp_path)

    # Create storyboard.json
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="first", search_term="ocean", visual_url="http://old/0.mp4"),
            StoryboardFrame(index=1, narration="second", search_term="beach", visual_url="http://old/1.mp4"),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    # Mock task
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({"engine": "auto"})
    svc.repo.get = AsyncMock(return_value=task)

    # Create audio dir for _synthesize_audio
    (tmp_path / "audio").mkdir()

    with patch.object(svc, "_synthesize_audio", new_callable=AsyncMock, return_value=(str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt"))), patch.object(svc, "_make_visual_source") as mock_vis:
        mock_vis.return_value = AsyncMock()

        result = await svc._regenerate_frame("vg_test", "u1", 1, tmp_path)

    assert result["frame_index"] == 1
    # Verify storyboard was updated (visual_url cleared then re-rendered)
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    # visual_url was cleared then visual source ran
    assert "frames" in updated


@pytest.mark.asyncio
async def test_regenerate_frame_invalid_index(tmp_path):
    """Invalid frame_index raises ValueError."""
    svc = _make_service(tmp_path)

    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="only one")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="out of range"):
        await svc._regenerate_frame("vg_test", "u1", 5, tmp_path)


@pytest.mark.asyncio
async def test_regenerate_frame_wrong_status(tmp_path):
    """Non-ready task raises ValueError."""
    svc = _make_service(tmp_path)

    task = MagicMock()
    task.status = "running"
    task.task_type = "ai_image"
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="must be ready"):
        await svc._regenerate_frame("vg_test", "u1", 0, tmp_path)


# ── Task 1 (P0-A1): regenerate 清除 stale local_visual_path ────────────────

from pathlib import Path  # noqa: E402

from app.gateway.video_gen.orchestration.tts import TTSResult  # noqa: E402


@pytest.mark.asyncio
async def test_regenerate_frame_clears_stale_local_visual(tmp_path):
    """先上传后重生：local_visual_path 必须随新视觉落盘被清除。

    修复前：_regenerate_frame 只清 visual_url/visual_type，on_frame_done 也只回写
    这两个字段 → storyboard.json 里 local_visual_path 仍指旧上传文件；worker 合成
    local 优先于 url → 新画面静默无效（白花一次生成费）。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", visual_url="http://cdn/0.png"),
            StoryboardFrame(
                index=1,
                narration="第二句旁白",
                image_prompt="p1",
                local_visual_path="visuals/frame_001.jpg",  # 模拟用户此前上传过
                visual_url=None,
            ),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_001.jpg").write_bytes(b"old-upload")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    async def fake_tts(text, *, voice_id, speed, output_path, fallback_voice=None):
        Path(output_path).write_bytes(b"mp3-bytes")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=fake_tts),
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock),
    ):
        await svc._regenerate_frame("vg_t1", "u1", 1, tmp_path)

    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    frame1 = updated["frames"][1]
    assert frame1["visual_url"] == "http://cdn/new-1.png"
    assert frame1["local_visual_path"] is None  # 修复前：仍为 "visuals/frame_001.jpg"


@pytest.mark.asyncio
async def test_on_frame_done_persists_local_visual_path(tmp_path):
    """on_frame_done 必须把内存中的 local_visual_path 落盘。

    ai_video D1 本地化（以及 Task 8 的 ai_image 本地化）依赖该回调持久化；
    修复前 updater 只写 visual_url/visual_type，本地化路径根本进不了 storyboard.json。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    storyboard = Storyboard.from_dict(json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8")))
    storyboard.frames[0].visual_url = "http://cdn/v0.mp4"
    storyboard.frames[0].visual_type = "video"
    storyboard.frames[0].local_visual_path = "visuals/shot_0000.mp4"

    cb = svc._make_on_frame_done(storyboard, tmp_path)
    await cb(0)

    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][0]["visual_url"] == "http://cdn/v0.mp4"
    assert updated["frames"][0]["local_visual_path"] == "visuals/shot_0000.mp4"  # 修复前缺此字段


# ── Task 2 (P0-A2): 单帧重生 TTS 按 CAS 命中跳过 ───────────────────────────


from app.gateway.video_gen.service import _audio_cache_key  # noqa: E402


@pytest.mark.asyncio
async def test_regenerate_frame_skips_tts_when_narration_unchanged(tmp_path):
    """narration 未变（CAS 命中）→ 跳过 TTS API、跳过重 concat、时长不变。

    修复前：每次单帧重生都无条件重跑 TTS——浪费调用 + 慢 2-10s，且 TTS 故障
    会让已落盘的新视觉与 dirty 标记脱节（用户困死）。
    """
    svc = _make_service(tmp_path)
    narration = "第二句旁白"
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", duration=2.0, audio_path=str(tmp_path / "audio" / "a0.mp3")),
            StoryboardFrame(index=1, narration=narration, image_prompt="p1", duration=3.5, audio_path=str(tmp_path / "audio" / "a1.mp3")),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    content_hash = _audio_cache_key(narration, "preset:146", 1.2)
    (audio_dir / f"{content_hash}.mp3").write_bytes(b"existing-audio")  # CAS 命中
    (tmp_path / "audio.mp3").write_bytes(b"concat")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", new_callable=AsyncMock) as mock_tts,
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock) as mock_ff,
    ):
        await svc._regenerate_frame("vg_t2", "u1", 1, tmp_path)

    mock_tts.assert_not_called()  # 修复前：被调 1 次
    mock_ff.assert_not_called()  # ffprobe 对假字节返 0.0 → 按"信任现值"处理（注意：非"探测到无漂移"，是探测失败的兜底分支）→ 不重 concat
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][1]["duration"] == 3.5  # 时长未被重算
    assert updated["frames"][1]["visual_url"] == "http://cdn/new-1.png"
    # 评审点②：CAS 命中也要落 audio_path 纠偏（"改旁白又改回"后 storyboard 里可能是旧 hash 路径）
    assert updated["frames"][1]["audio_path"] == str(tmp_path / "audio" / f"{content_hash}.mp3")


@pytest.mark.asyncio
async def test_regenerate_frame_cas_hit_probe_ok_no_drift_persists_audio_path(tmp_path):
    """探测成功且无漂移（probed ≈ storyboard duration）→ 不重 concat，但 audio_path 仍落盘纠偏。

    CAS 命中三个子路径的最后一个（probe失败兜底 / probe成功+漂移 / probe成功+无漂移），
    钉住"无漂移时 _cas_updater 依然执行"的行为。
    """
    svc = _make_service(tmp_path)
    narration = "第二句旁白"
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", duration=2.0, audio_path=str(tmp_path / "audio" / "a0.mp3")),
            StoryboardFrame(index=1, narration=narration, image_prompt="p1", duration=3.5, audio_path=str(tmp_path / "audio" / "a1.mp3")),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    content_hash = _audio_cache_key(narration, "preset:146", 1.2)
    (audio_dir / f"{content_hash}.mp3").write_bytes(b"existing-audio")
    (tmp_path / "audio.mp3").write_bytes(b"concat")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", new_callable=AsyncMock) as mock_tts,
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock) as mock_ff,
        patch("app.gateway.video_gen.service._get_audio_duration", return_value=3.5),  # 探测成功且与现值一致
    ):
        await svc._regenerate_frame("vg_t2c", "u1", 1, tmp_path)

    mock_tts.assert_not_called()
    mock_ff.assert_not_called()  # 无漂移 → 不重 concat
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    f1 = updated["frames"][1]
    assert f1["duration"] == 3.5  # 现值保留
    # 关键断言：audio_path 从初始的 a1.mp3 被 _cas_updater 纠偏为 CAS 路径（证明落盘执行了）
    assert f1["audio_path"] == str(tmp_path / "audio" / f"{content_hash}.mp3")


@pytest.mark.asyncio
async def test_regenerate_frame_cas_hit_with_duration_drift_recalibrates(tmp_path):
    """A→B→A 旁白往返：CAS 命中但 storyboard 里的 duration/srt 属于 B 时代 →
    用探测时长校准 + 清 srt + 重 concat，全程不调 TTS API。

    场景：原旁白 A（CAS 文件在）→ PUT 改 B → 重生（TTS B，duration_B/srt_B 落盘）
    → PUT 改回 A → 再重生：CAS 命中（A 的音频在），但 storyboard 元数据是 B 的。
    """
    svc = _make_service(tmp_path)
    narration_a = "原始旁白"
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", duration=2.0, audio_path=str(tmp_path / "audio" / "a0.mp3")),
            StoryboardFrame(
                index=1,
                narration=narration_a,
                image_prompt="p1",
                duration=4.2,  # B 时代的时长（与 A 的真实音频 2.0s 不符）
                srt="1\n00:00:00,000 --> 00:00:04,200\nB的台词\n",
                audio_path=str(tmp_path / "audio" / "hash_b.mp3"),  # B 时代的音频路径
            ),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    content_hash = _audio_cache_key(narration_a, "preset:146", 1.2)
    (audio_dir / f"{content_hash}.mp3").write_bytes(b"audio-of-A")
    (tmp_path / "audio.mp3").write_bytes(b"concat")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", new_callable=AsyncMock) as mock_tts,
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock) as mock_ff,
        patch("app.gateway.video_gen.service._get_audio_duration", return_value=2.0),  # A 的真实时长
    ):
        await svc._regenerate_frame("vg_t2b", "u1", 1, tmp_path)

    mock_tts.assert_not_called()  # 仍不调 TTS
    assert mock_ff.call_count == 1  # 但漂移 → 重 concat
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    f1 = updated["frames"][1]
    assert f1["duration"] == 2.0  # 校准为 A 的真实时长（修复设计前：留 4.2 致音画错位）
    assert f1["speech_duration"] == 2.0
    assert f1["srt"] == ""  # B 的 srt 被清，交由 _write_storyboard_srt 按 A 合成
    assert f1["audio_path"] == str(tmp_path / "audio" / f"{content_hash}.mp3")


@pytest.mark.asyncio
async def test_regenerate_frame_reruns_tts_when_narration_changed(tmp_path):
    """narration 已改（CAS miss）→ 仍走完整 TTS + 重 concat + 重写 SRT 路径。

    这是 Task 4（ready 态改旁白）的生效链路，必须保住。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", duration=2.0, audio_path=str(tmp_path / "audio" / "a0.mp3")),
            StoryboardFrame(index=1, narration="用户改过的新旁白", image_prompt="p1", duration=3.5, audio_path=str(tmp_path / "audio" / "a1.mp3")),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "audio").mkdir()  # 注意：不放任何 CAS 文件 → miss

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    async def fake_tts(text, *, voice_id, speed, output_path, fallback_voice=None):
        assert text == "用户改过的新旁白"
        Path(output_path).write_bytes(b"new-audio")
        return TTSResult(audio_path=output_path, srt="1\n00:00:00,000 --> 00:00:02,000\n新\n", duration=4.2)

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=fake_tts) as mock_tts,
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock) as mock_ff,
    ):
        await svc._regenerate_frame("vg_t3", "u1", 1, tmp_path)

    assert mock_tts.call_count == 1
    assert mock_ff.call_count == 1  # 重 concat audio.mp3
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][1]["duration"] == 4.2
    assert updated["frames"][1]["srt"].startswith("1\n")


@pytest.mark.asyncio
async def test_regenerate_frame_marks_dirty_when_tts_fails_after_visual_success(tmp_path):
    """可选项（评审轮3 B4 残留）：视觉已重生落盘 + TTS 失败 → 仍标 dirty 再 re-raise。

    修复前：视觉变了但 dirty 未标 → 「重新合成」按钮不亮，新画面被静默吞掉（用户困死）。
    场景仅当 narration 已改（CAS miss）时 TTS 才会跑，所以这是"改旁白+TTS 故障"边界。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句旁白", image_prompt="p0", duration=2.0, audio_path=str(tmp_path / "audio" / "a0.mp3")),
            StoryboardFrame(index=1, narration="用户改过的新旁白", image_prompt="p1", duration=3.5, audio_path=str(tmp_path / "audio" / "a1.mp3")),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "audio").mkdir()  # 无 CAS 文件 → TTS 必跑

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.add_dirty_frame = AsyncMock(return_value=None)

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[1]
        f.visual_url = "http://cdn/new-1.png"
        f.visual_type = "image"
        if on_frame_done:
            await on_frame_done(1)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=RuntimeError("doubao TTS failed")),
        pytest.raises(RuntimeError, match="doubao TTS failed"),
    ):
        await svc._regenerate_frame("vg_t4", "u1", 1, tmp_path)

    # 关键断言：视觉已重生（visual_url 落盘）→ dirty 必须标，让用户能重试或先合成
    svc.repo.add_dirty_frame.assert_called_once_with("vg_t4", 1)
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][1]["visual_url"] == "http://cdn/new-1.png"


@pytest.mark.asyncio
async def test_preview_localizes_image_and_marks_dirty_in_ready(tmp_path):
    """P1-E7（H3 协调 v2§2）: ready 任务单帧预览必须本地化写 local_visual_path + 标 dirty。

    修前（加固 H3）：预览清 local_visual_path=None（让 recompose 重下载新 visual_url）。
    现改为「写本地化路径」——预览即新图，local 优先直接复用，免 TOS 临时 URL 过期。
    dirty 标记语义两案一致（ready/failed 态预览 = 换图，需增量带入成片）。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration="旁白",
                image_prompt="p0",
                visual_url="http://cdn/old.png",
                local_visual_path="visuals/img_0000.png",
            ),
        ],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_pv"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.add_dirty_frame = AsyncMock()
    image_ref = MagicMock()
    # data: URI → _localize_image 直接解码落盘（真实 http URL 在测试里无法抓取）
    b64 = base64.b64encode(b"\x89PNG" + b"0" * 600).decode()
    preview_data_uri = f"data:image/png;base64,{b64}"
    image_ref.url = preview_data_uri
    svc._providers.ai_image.generate_image = AsyncMock(return_value=image_ref)

    url = await svc._generate_frame_preview("vg_pv", 0, "u1")

    assert url == preview_data_uri
    updated = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    frame0 = updated["frames"][0]
    assert frame0["visual_url"] == preview_data_uri
    # P1-E7: 预览图本地化落盘 → local_visual_path 指向新图（修前 H3 清成 None）
    assert frame0["local_visual_path"] == "visuals/img_0000.png"
    assert (task_dir / frame0["local_visual_path"]).exists(), "本地化文件须真实落盘"
    svc.repo.add_dirty_frame.assert_awaited_once_with("vg_pv", 0)  # ready 态标 dirty


@pytest.mark.asyncio
async def test_preview_in_draft_does_not_mark_dirty(tmp_path):
    """draft 态预览不标 dirty（首渲走全量，无 segment 可增量；标了只泄漏成无谓增量）。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_pv2"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "draft"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.add_dirty_frame = AsyncMock()
    image_ref = MagicMock()
    image_ref.url = "http://cdn/preview.png"
    svc._providers.ai_image.generate_image = AsyncMock(return_value=image_ref)

    await svc._generate_frame_preview("vg_pv2", 0, "u1")

    svc.repo.add_dirty_frame.assert_not_awaited()


# ── Task 4 (H4): 单帧重生异步化 + 合成锁 + regen_state ─────────────────────


@pytest.mark.asyncio
async def test_regen_bg_success_marks_done_and_dirty(tmp_path):
    """异步重生成功：regen_state=done 落盘 + 标 dirty + inflight 清理。"""
    from app.gateway.routers.video_gen import _regen_inflight, _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_bg"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(return_value={"frame_index": 0})

    _regen_inflight[("vg_bg", 0)] = asyncio.current_task()  # 模拟路由登记（bg finally 清理）
    await _regenerate_frame_bg(svc, "vg_bg", 0, "u1")

    svc._regenerate_frame.assert_awaited_once()
    svc.repo.add_dirty_frame.assert_awaited_once_with("vg_bg", 0)
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["regen_state"] == "done"
    assert ("vg_bg", 0) not in _regen_inflight


@pytest.mark.asyncio
async def test_regen_bg_failure_writes_error_state(tmp_path):
    """异步重生失败：regen_state=error + regen_error 落盘（不再 400/500）。"""
    from app.gateway.routers.video_gen import _regen_inflight, _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_bg2"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(side_effect=RuntimeError("doubao TTS failed"))

    _regen_inflight[("vg_bg2", 0)] = asyncio.current_task()
    await _regenerate_frame_bg(svc, "vg_bg2", 0, "u1")

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["regen_state"] == "error"
    assert "doubao TTS failed" in data["frames"][0]["regen_error"]
    svc.repo.add_dirty_frame.assert_not_awaited()
    assert ("vg_bg2", 0) not in _regen_inflight


@pytest.mark.asyncio
async def test_regen_bg_serializes_per_task_via_synth_lock(tmp_path):
    """H4 核心：同一任务两帧并发重生必须经 _synth_lock_for 串行（防 audio.mp3 串代）。"""
    from app.gateway.routers.video_gen import _regenerate_frame_bg
    from app.gateway.video_gen.service import _synth_lock_for

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=i, narration=f"旁白{i}", image_prompt=f"p{i}") for i in range(2)],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_bg3"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()

    lock = _synth_lock_for(str(task_dir))
    hold = asyncio.Event()
    overlap = []

    async def fake_regen(task_id, user_id, frame_index, td, mode="all"):
        # 注意（对 brief 的最小偏离）：brief 原版用 lock.locked() 判定"帧 0 仍持锁"，
        # 但 asyncio.Lock 无 owner API——bg 正确持锁调用本函数时 lock.locked() 恒为 True
        # （锁被帧 1 自己的 bg 持有），原探针只会误报正确实现、放过无锁实现。
        # 改用显式临界区标志 hold（brief 中本就声明但未接线的事件），语义不变。
        if hold.is_set() and frame_index == 1:
            overlap.append(frame_index)  # 帧 1 进入时帧 0 仍在临界区 → 串行失败
        return {"frame_index": frame_index}

    svc._regenerate_frame = fake_regen
    hold.set()  # 帧 0 进入临界区（测试体代持锁）
    async with lock:  # 模拟帧 0 持锁中
        t = asyncio.create_task(_regenerate_frame_bg(svc, "vg_bg3", 1, "u1"))
        await asyncio.sleep(0.05)  # 给帧 1 抢锁的机会
        assert not t.done() or not overlap  # 帧 1 未完成或未进入临界区
    hold.clear()  # 帧 0 退出临界区
    await t
    assert overlap == []  # 帧 1 在锁释放后才进入
    svc.repo.add_dirty_frame.assert_awaited_once_with("vg_bg3", 1)  # 帧 1 确实走完成功路径


def _make_route_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


@pytest.mark.asyncio
async def test_regenerate_route_202_and_dedupe(tmp_path):
    """路由：202 started → 重复点击 already_running；同步校验即时。"""
    from app.gateway.routers.video_gen import _regen_inflight, regenerate_frame

    svc = _make_service(tmp_path)
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_rt"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {}, "frames": [{"index": 0, "narration": "x"}]}))
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    svc.get_task = AsyncMock(return_value=task)
    svc._active_tasks = {}
    svc._regenerate_frame = AsyncMock(return_value={"frame_index": 0})
    svc.repo.add_dirty_frame = AsyncMock()

    try:
        r1 = await regenerate_frame(_make_route_request(svc), "vg_rt", 0)
        assert r1 == {"status": "started", "mode": "all"}  # mode 缺省 all（2026-07-27 模式分离）
        r2 = await regenerate_frame(_make_route_request(svc), "vg_rt", 0)
        assert r2 == {"status": "already_running"}
        pending = _regen_inflight.get(("vg_rt", 0))
        if pending is not None and pending is not asyncio.current_task():
            await pending  # 等在途 bg 收尾，防跨测试泄漏
    finally:
        _regen_inflight.pop(("vg_rt", 0), None)


@pytest.mark.asyncio
async def test_regenerate_route_rejects_vimax(tmp_path):
    """M7 类型门：vimax 任务 → 422（修复前走到 service 抛误导性 engine_arch 错误）。"""
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import regenerate_frame

    svc = _make_service(tmp_path)
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_vx"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {}, "frames": [{"index": 0, "narration": "x"}]}))
    task = MagicMock()
    task.status = "ready"
    task.task_type = "vimax"
    svc.get_task = AsyncMock(return_value=task)

    with pytest.raises(HTTPException) as exc:
        await regenerate_frame(_make_route_request(svc), "vg_vx", 0)
    assert exc.value.status_code == 422


# ── Task 6 (H7): image_reuse 复用帧跟随源帧重生 ────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_frame_updates_reuse_frames(tmp_path):
    """H7: image_reuse 复用帧必须跟随源帧重生——否则共享文件被同路径覆盖（或扩展名
    变化成孤儿）后，复用帧的缓存 segment 仍旧图，增量 recompose 出「帧 N 新图、帧 N+1 旧图」。
    """
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="第一句", image_prompt="p0", local_visual_path="visuals/img_0000.png", visual_type="image"),
            # 帧 1 是帧 0 的复用帧（生成失败时共享帧 0 的本地文件）
            StoryboardFrame(index=1, narration="第二句", image_prompt="p1", local_visual_path="visuals/img_0000.png", visual_type="image_reuse"),
        ],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_ru"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.add_dirty_frame = AsyncMock()

    async def fake_render(storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):
        f = storyboard.frames[0]
        f.visual_url = "http://cdn/new-0.png"
        f.visual_type = "image"
        f.local_visual_path = "visuals/img_0000.png"  # 同路径覆盖（F4 本地化命名 img_{index:04d}）
        if on_frame_done:
            await on_frame_done(0)

    mock_visual = AsyncMock()
    mock_visual.render = fake_render

    async def fake_tts(text, *, voice_id, speed, output_path, fallback_voice=None):
        from pathlib import Path as _P

        from app.gateway.video_gen.orchestration.tts import TTSResult

        _P(output_path).write_bytes(b"mp3")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=fake_tts),
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock),
    ):
        await svc._regenerate_frame("vg_ru", "u1", 0, task_dir)

    updated = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    # 复用帧的 local_visual_path 必须仍指向源帧的新 local（同路径场景=不变，但 dirty 必须标）
    assert updated["frames"][1]["local_visual_path"] == "visuals/img_0000.png"
    dirty_marked = {c.args[1] for c in svc.repo.add_dirty_frame.await_args_list}
    assert 1 in dirty_marked  # 修复前：只有路由标帧 0，复用帧 1 不标 → 缓存 segment 仍旧图


# ── Task 7 (M7): 帧级 visual_source 分派 ────────────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_frame_dispatches_per_frame_visual_source(tmp_path):
    """M7: 混合源 storyboard——帧级 visual_source override 优先（与主渲染 _has_mixed_sources
    分派一致）；修复前用 task 级类型，ai_video override 帧被重生成为图片。
    """
    svc = _make_service(tmp_path)
    # visual_source 是 StoryboardFrame 声明字段（storyboard.py:18），to_dict/from_dict 往返保留
    frame1 = StoryboardFrame(index=1, narration="第二句", image_prompt="p1", visual_source="ai_video")
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="第一句", image_prompt="p0"), frame1],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_mx"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    svc.repo.get = AsyncMock(return_value=task)

    mock_visual = AsyncMock()

    async def fake_tts(text, *, voice_id, speed, output_path, fallback_voice=None):
        from pathlib import Path as _P

        from app.gateway.video_gen.orchestration.tts import TTSResult

        _P(output_path).write_bytes(b"mp3")
        return TTSResult(audio_path=output_path, srt="", duration=2.0)

    with (
        patch.object(svc, "_make_visual_source", return_value=mock_visual) as m_make,
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=fake_tts),
        patch("app.gateway.video_gen.service.run_ffmpeg", new_callable=AsyncMock),
    ):
        await svc._regenerate_frame("vg_mx", "u1", 1, task_dir)

    assert m_make.call_args.args[0] == "ai_video"  # 修复前：传 "ai_image"（task 级）


# ── Task 9 (M1): pending_regen_frames 清除 + 路由互斥补齐 ────────────────────


@pytest.mark.asyncio
async def test_regen_bg_success_clears_pending_regen(tmp_path):
    """M1: 重生成功 → 该帧从 pending_regen_frames 清除（recompose 守卫随之放行）。"""
    from app.gateway.routers.video_gen import _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb_dict = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="新旁白", image_prompt="p0")],
    ).to_dict()
    sb_dict["pending_regen_frames"] = [0]
    task_dir = tmp_path / "u1" / "tasks" / "vg_pc"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb_dict, ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(return_value={"frame_index": 0})

    await _regenerate_frame_bg(svc, "vg_pc", 0, "u1")

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data.get("pending_regen_frames", []) == []


@pytest.mark.asyncio
async def test_regen_bg_failure_keeps_pending_regen(tmp_path):
    """M1 边界：重生失败 → pending 保留（守卫继续 409，用户需重试重生或改回旁白）。"""
    from app.gateway.routers.video_gen import _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb_dict = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="新旁白", image_prompt="p0")],
    ).to_dict()
    sb_dict["pending_regen_frames"] = [0]
    task_dir = tmp_path / "u1" / "tasks" / "vg_pf"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb_dict, ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(side_effect=RuntimeError("doubao TTS failed"))

    await _regenerate_frame_bg(svc, "vg_pf", 0, "u1")

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data.get("pending_regen_frames") == [0]  # 失败不清——守卫仍应拦截


@pytest.mark.asyncio
async def test_regenerate_route_409_when_pipeline_active(tmp_path):
    """G2①/H4 互斥①：任务级 pipeline（recompose/re-render/render）在跑 → 单帧重生 409。

    Task 4 评审 Minor1 补测：互斥代码已在（routers :1137-1139），缺直接覆盖。
    """
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import _regen_inflight, regenerate_frame

    svc = _make_service(tmp_path)
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_pa"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {}, "frames": [{"index": 0, "narration": "x"}]}))
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    svc.get_task = AsyncMock(return_value=task)

    blocker = asyncio.create_task(asyncio.sleep(30))
    svc._active_tasks = {"vg_pa": blocker}  # 模拟 recompose/render 在跑
    try:
        with pytest.raises(HTTPException) as exc:
            await regenerate_frame(_make_route_request(svc), "vg_pa", 0)
        assert exc.value.status_code == 409
        assert "合成中" in exc.value.detail
        assert ("vg_pa", 0) not in _regen_inflight  # 未登记在途
    finally:
        blocker.cancel()
        svc._active_tasks.pop("vg_pa", None)


@pytest.mark.asyncio
async def test_recompose_route_409_when_regen_inflight(tmp_path):
    """G2②/H4 互斥②：有帧正在重新生成 → recompose 409（防 payload 读到半更新状态）。

    Task 4 评审 Minor1 补测：互斥代码已在（routers :938-939），缺直接覆盖。
    """
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import _regen_inflight, recompose_task

    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    svc.get_task = AsyncMock(return_value=task)
    svc.recompose_task = AsyncMock()

    _regen_inflight[("vg_rc", 2)] = asyncio.current_task()  # 模拟帧 2 重生在途
    try:
        with pytest.raises(HTTPException) as exc:
            await recompose_task(_make_route_request(svc), "vg_rc")
        assert exc.value.status_code == 409
        assert "重新生成" in exc.value.detail
        svc.recompose_task.assert_not_called()
    finally:
        _regen_inflight.pop(("vg_rc", 2), None)


@pytest.mark.asyncio
async def test_re_render_route_409_when_regen_inflight(tmp_path):
    """G1：re-render 与 recompose 同款互斥——有帧正在重新生成 → 409。

    修复前：re_render 路由无 inflight 检查，帧重生后台改写 storyboard/audio 的同时
    re-render 拾取半更新分镜重跑全视觉。
    """
    from fastapi import HTTPException

    from app.gateway.routers.video_gen import _regen_inflight, re_render_task

    svc = _make_service(tmp_path)
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    svc.get_task = AsyncMock(return_value=task)
    svc.re_render_task = AsyncMock()

    _regen_inflight[("vg_rr", 1)] = asyncio.current_task()  # 模拟帧 1 重生在途
    try:
        with pytest.raises(HTTPException) as exc:
            await re_render_task(_make_route_request(svc), "vg_rr")
        assert exc.value.status_code == 409
        assert "重新生成" in exc.value.detail
        svc.re_render_task.assert_not_called()
    finally:
        _regen_inflight.pop(("vg_rr", 1), None)

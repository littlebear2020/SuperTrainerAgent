from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.pipeline_result import PipelineDraft
from app.gateway.video_gen.pipelines.remotion_props import StoryboardResult
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService, _aspect_to_size, build_worker_payload
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_aspect_to_size():
    # A6（03bde5164）：Ark 原生尺寸，避免 1080x1920 被 Ark fallback 成 16:9
    assert _aspect_to_size("9:16") == (1152, 2048)
    assert _aspect_to_size("16:9") == (2048, 1152)
    assert _aspect_to_size("1:1") == (1152, 1152)


def test_build_worker_payload_shape(tmp_path):
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="a", visual_url="http://x/a.mp4", visual_type="video", audio_path=str(tmp_path / "a.mp3"), duration=3.0),
        ],
    )
    sb.recompute_duration()
    payload = build_worker_payload(sb, subtitle_path=str(tmp_path / "s.srt"), audio_concat_path=str(tmp_path / "all.mp3"))
    assert payload["video_type"] == "ai_image"
    assert payload["config"]["width"] == 1080
    assert payload["frames"][0]["visual_url"] == "http://x/a.mp4"
    assert payload["frames"][0]["duration"] == 3.0
    assert payload["subtitle_path"].endswith("s.srt")
    assert payload["audio_concat_path"].endswith("all.mp3")


def test_write_storyboard_srt(tmp_path):
    from app.gateway.video_gen.service import _write_storyboard_srt

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="第一句", duration=2.5),
            StoryboardFrame(index=1, narration="第二句", duration=3.0),
        ],
    )
    out = str(tmp_path / "test.srt")
    _write_storyboard_srt(sb, out)
    content = Path(out).read_text()
    assert "第一句" in content
    assert "第二句" in content
    assert "-->" in content
    # SRT format: each entry is "seq\nstart --> end\ntext\n"
    # Split by blank lines to get entries
    entries = [e.strip() for e in content.strip().split("\n\n") if e.strip()]
    assert len(entries) == 2
    assert entries[0].startswith("1\n")
    assert entries[1].startswith("2\n")


def test_write_storyboard_srt_uses_word_level_when_available(tmp_path):
    """Word-level SRT from TTS drives the cues (2026-07-31 T2; 2026-08-13 engine R4 修订).

    旧契约是「词级 cue 逐条透传」（断言 4 条）；T2 起相邻词合并以消除逐词闪烁。
    2026-08-13 字幕引擎（subtitle_engine.segment）把合并上限放宽为两行总宽
    （portrait 13/行 → ≤26 全角当量），17 字旁白整帧合并为 1 条 cue 并两行折行。
    故断言改为：词级时间戳驱动 cue 边界（末词 end 收尾，非宽度均分）、内容零
    丢失（R2）、帧偏移按 duration 推进、超单行宽 cue 折两行。
    """
    from app.gateway.video_gen.service import _write_storyboard_srt

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    # Simulate word-level SRT as returned by edge-tts SubMaker.
    # 每帧 3 个词级 cue（合计 17 字 ≤ 两行总宽 26）→ 引擎合并为每帧 1 条 cue。
    word_srt_1 = "1\n00:00:00,000 --> 00:00:00,800\n你好世界啊呀\n\n2\n00:00:00,800 --> 00:00:01,600\n第一句台词\n\n3\n00:00:01,600 --> 00:00:02,500\n继续说下去哦"
    word_srt_2 = "1\n00:00:00,000 --> 00:00:01,000\n接下来说啊呀\n\n2\n00:00:01,000 --> 00:00:02,000\n第二句话呢\n\n3\n00:00:02,000 --> 00:00:03,000\n最后收尾了吧"
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="你好世界啊呀第一句台词继续说下去哦", duration=2.5, srt=word_srt_1),
            StoryboardFrame(index=1, narration="接下来说啊呀第二句话呢最后收尾了吧", duration=3.0, srt=word_srt_2),
        ],
    )
    out = str(tmp_path / "test_word.srt")
    _write_storyboard_srt(sb, out)
    content = Path(out).read_text()
    entries = [e.strip() for e in content.strip().split("\n\n") if e.strip()]
    # 每帧合并为 1 条 cue（两行总宽 26 ≥ 17 字），不再逐词一条
    assert len(entries) == 2, f"每帧应合并为 1 条 cue，实得 {len(entries)} 条"
    # 词级时间戳驱动 cue 边界：帧0 起 0.000、止 2.500（末词 end），非宽度均分
    assert entries[0].startswith("1\n00:00:00,000 --> 00:00:02,500")
    # 帧偏移按 duration 推进：帧1 起 2.500、止 2.500+3.000=5.500
    assert entries[1].startswith("2\n00:00:02,500 --> 00:00:05,500")
    # 内容零丢失 + 超单行宽折两行（portrait 单行 13 < 17）
    for entry, narration in zip(entries, ["你好世界啊呀第一句台词继续说下去哦", "接下来说啊呀第二句话呢最后收尾了吧"]):
        text = "\n".join(entry.split("\n")[2:])
        assert text.replace("\n", "") == narration, f"内容丢失或改动：{text!r}"
        assert "\n" in text, f"17 字超 portrait 单行宽 13，应折两行：{text!r}"
    assert "你好世界" in content
    assert "接下来" in content


def test_write_storyboard_srt_mixed_word_level_and_fallback(tmp_path):
    """If some frames have word-level SRT and some don't, mix both approaches."""
    from app.gateway.video_gen.service import _write_storyboard_srt

    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    word_srt = "1\n00:00:00,000 --> 00:00:01,500\n你好"
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="你好", duration=2.0, srt=word_srt),
            StoryboardFrame(index=1, narration="世界", duration=3.0),  # no srt
        ],
    )
    out = str(tmp_path / "test_mixed.srt")
    _write_storyboard_srt(sb, out)
    content = Path(out).read_text()
    entries = [e.strip() for e in content.strip().split("\n\n") if e.strip()]
    # 1 word-level entry + 1 fallback entry
    assert len(entries) == 2
    assert "你好" in content
    # Fallback entry should be offset by 2.0s
    assert "00:00:02,000 -->" in content


def test_make_visual_source_ai_image():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="test-key")
    providers = ProviderRegistry(cfg)

    # Create a minimal repo mock
    class _FakeRepo:
        pass

    svc = VideoGenService(repo=_FakeRepo(), config=cfg, providers=providers, storage_dir="/tmp/vg")
    src = svc._make_visual_source("ai_image")
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    assert isinstance(src, AIImageVisualSource)


def test_make_visual_source_ai_video():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")  # E6: script_model 显式化后不再回落 models[0]
    providers = ProviderRegistry(cfg)
    svc = VideoGenService(repo=type("", (), {})(), config=cfg, providers=providers, storage_dir="/tmp/vg")
    src = svc._make_visual_source("ai_video")
    assert isinstance(src, AIVideoVisualSource)


def _make_service(tmp_path):
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")  # E6: script_model 显式化后不再回落 models[0]
    providers = ProviderRegistry(cfg)
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_run_pipeline_remotion_data_driven_calls_storyboard_then_render(tmp_path):
    """task_type='remotion' goes through storyboard preparation then rendering."""
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    sb = Storyboard(title="test", config=StoryboardConfig(video_type="remotion", width=1080, height=1920))
    sb_result = StoryboardResult(
        storyboard=sb,
        subtitle_path=str(tmp_path / "sub.srt"),
        audio_dir=str(tmp_path / "audio"),
    )
    progress = AsyncMock()
    progress.set_result = AsyncMock()

    with patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock, return_value=sb_result) as mock_prepare, patch.object(svc, "_render_remotion_storyboard", new_callable=AsyncMock, return_value=None) as mock_render:
        result = await svc._run_pipeline(
            task_id="test-1",
            user_id="u1",
            task_type="remotion",
            payload={"script": "hello", "mode": "fixed"},
            task_dir=tmp_path,
            progress=progress,
        )

    mock_prepare.assert_called_once()
    mock_render.assert_called_once_with("test-1", sb_result, tmp_path, progress)
    assert result is None


@pytest.mark.asyncio
async def test_run_pipeline_remotion_legacy_calls_run_remotion(tmp_path):
    """task_type='remotion_legacy' calls the legacy _run_remotion."""
    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()
    payload = {"composition": "MyComp", "props": {}}

    from unittest.mock import patch

    with patch.object(svc, "_run_remotion", new_callable=AsyncMock, return_value=None) as mock_legacy:
        result = await svc._run_pipeline(
            task_id="test-2",
            user_id="u1",
            task_type="remotion_legacy",
            payload=payload,
            task_dir=tmp_path,
            progress=progress,
        )

    mock_legacy.assert_called_once_with("test-2", payload, tmp_path, progress)
    assert result is None


def test_should_handoff_to_worker_excludes_remotion():
    """Neither 'remotion' nor 'remotion_legacy' should hand off to worker."""
    svc = _make_service(Path("/tmp/vg"))
    assert not svc._should_handoff_to_worker("remotion")
    assert not svc._should_handoff_to_worker("remotion_legacy")


def test_resolve_bgm_returns_none_for_unknown_id():
    """_resolve_bgm with an ID absent from index.json → None (not an exception)."""
    from app.gateway.video_gen.service import _resolve_bgm

    result = _resolve_bgm("nonexistent_track_xyz")
    assert result is None


# ---------------------------------------------------------------------------
# Two-stage (draft → render) flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pipeline_auto_render_false_generates_draft(tmp_path):
    """auto_render=False should call _generate_draft."""
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()
    draft_result = PipelineDraft(storyboard={"title": "test", "frames": []})

    with patch.object(svc, "_generate_draft", new_callable=AsyncMock, return_value=draft_result) as mock_draft:
        result = await svc._run_pipeline(
            task_id="vg_draft",
            user_id="u1",
            task_type="ai_image",
            payload={"script": "hello", "mode": "fixed", "auto_render": False},
            task_dir=tmp_path,
            progress=progress,
        )

    mock_draft.assert_called_once()
    assert isinstance(result, PipelineDraft)


@pytest.mark.asyncio
async def test_run_pipeline_frame_edits_calls_render_from_draft(tmp_path):
    """payload with frame_edits + auto_render should call _render_from_draft."""
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()

    with patch.object(svc, "_render_from_draft", new_callable=AsyncMock, return_value=None) as mock_render:
        result = await svc._run_pipeline(
            task_id="vg_render",
            user_id="u1",
            task_type="ai_image",
            payload={"frame_edits": {"0": {"narration": "edited"}}, "auto_render": True},
            task_dir=tmp_path,
            progress=progress,
        )

    mock_render.assert_called_once()
    assert result is None


@pytest.mark.asyncio
async def test_generate_draft_saves_storyboard_json(tmp_path):
    """_generate_draft should persist storyboard.json."""
    from unittest.mock import MagicMock, patch

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()

    sb = Storyboard(
        title="测试",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="第一句", search_term="ocean")],
    )

    with (
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch("app.gateway.video_gen.orchestration.script.split_fixed_script", return_value=["第一句"]),
        patch(
            "app.gateway.video_gen.orchestration.storyboard_planner.plan_course",
            new_callable=AsyncMock,
            # S5（2026-08-26 调度表批）：service 现传 shot_table_enabled（配置默认开）——
            # 桩加 **kw 容差（本桩只关心 frames/title 替换，不锁 plan_course 签名）。
            side_effect=lambda llm, *, storyboard, style_context=None, task_dir=None, chunk_size=None, frame_concurrency=None, **_kw: (setattr(storyboard, "frames", sb.frames), setattr(storyboard, "title", sb.title)),
        ),
    ):
        result = await svc._generate_draft(
            task_type="ai_image",
            payload={"script": "第一句", "mode": "fixed", "aspect": "9:16"},
            task_dir=tmp_path,
            progress=progress,
        )

    assert isinstance(result, PipelineDraft)
    assert result.storyboard["title"] == "测试"
    assert (tmp_path / "storyboard.json").exists()


@pytest.mark.asyncio
async def test_render_from_draft_applies_frame_edits(tmp_path):
    """_render_from_draft reads storyboard.json and applies frame_edits."""
    import json
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()

    # Write initial storyboard
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="原始旁白", search_term="ocean")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    # Create dummy audio for _synthesize_audio
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()

    with patch.object(svc, "_synthesize_audio", new_callable=AsyncMock, return_value=(str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt"))), patch.object(svc, "_make_visual_source") as mock_vis:
        mock_vis.return_value = AsyncMock()

        await svc._render_from_draft(
            task_id="vg_test",
            task_type="ai_image",
            task_dir=tmp_path,
            progress=progress,
            payload={"frame_edits": {"0": {"narration": "修改后的旁白"}}, "engine": "auto"},
        )

    # Verify the storyboard.json was updated
    updated = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert updated["frames"][0]["narration"] == "修改后的旁白"


@pytest.mark.asyncio
async def test_resume_draft_validates_status(tmp_path):
    """resume_draft should reject tasks that are not in a confirmable status.

    （P1/BUG-008 批：文案已中文化——detail 经 render_from_draft 路由透传用户可见。）"""
    svc = _make_service(tmp_path)

    # composing 是真正不可确认的状态（ready 已被 P1-U2 有意纳入：详情页
    # 「正式渲染」按钮把 preview 任务推进到标准渲染，修前对 ready 100% 409）
    task = MagicMock()
    task.status = "composing"
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="当前状态不可渲染"):
        await svc.resume_draft("vg_123", "u1")


@pytest.mark.asyncio
async def test_resume_draft_accepts_story_ready(tmp_path):
    """M3.1: idea2video story-confirm resumes from STORY_READY (not just DRAFT).
    The user confirmed the expanded story → render should proceed (fire-and-forget)."""
    from unittest.mock import AsyncMock, patch

    svc = _make_service(tmp_path)
    svc._wrap_pipeline = AsyncMock()

    task = MagicMock()
    task.status = "story_ready"
    task.payload = '{"pipeline": "idea2video", "topic": "x"}'
    task.id = "vg_123"
    task.user_id = "u1"
    task.task_type = "vimax"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()  # resume_draft 持久化 payload 已 async 化

    # resume_draft fire-and-forgets via asyncio.create_task; capture + await the coro
    captured_coro = []

    class _FakeTask:
        def add_done_callback(self, fn):
            pass

    def fake_create_task(coro):
        captured_coro.append(coro)
        return _FakeTask()

    with patch("asyncio.create_task", side_effect=fake_create_task):
        await svc.resume_draft("vg_123", "u1")

    assert len(captured_coro) == 1
    await captured_coro[0]  # actually run it so _wrap_pipeline is awaited
    svc._wrap_pipeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_draft_adds_done_callback(tmp_path):
    """resume_draft should add a done callback to the created task for error visibility."""
    from unittest.mock import MagicMock, patch

    svc = _make_service(tmp_path)

    task = MagicMock()
    task.status = DRAFT
    task.task_type = "ai_image"
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()  # 同上：repo.update 已 async 化

    mock_async_task = MagicMock()
    mock_async_task.add_done_callback = MagicMock()
    with patch("asyncio.create_task", return_value=mock_async_task):
        await svc.resume_draft("vg_test", "u1")

    mock_async_task.add_done_callback.assert_called_once()


@pytest.mark.asyncio
async def test_auto_render_true_goes_full_pipeline(tmp_path):
    """auto_render=True (default) should call _prepare_storyboard_video."""
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    progress.set_result = AsyncMock()

    with patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock, return_value=None) as mock_prepare:
        await svc._run_pipeline(
            task_id="vg_full",
            user_id="u1",
            task_type="ai_image",
            payload={"script": "hello", "mode": "fixed", "auto_render": True},
            task_dir=tmp_path,
            progress=progress,
        )

    mock_prepare.assert_called_once()


@pytest.mark.asyncio
async def test_regenerate_frame_only_reruns_tts_for_changed_frame(tmp_path):
    """_regenerate_frame should only synthesize TTS for the changed frame, not all frames."""
    import json
    from unittest.mock import AsyncMock, patch

    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)

    # Build a 3-frame storyboard with existing audio
    frames = []
    for i in range(3):
        audio_path = tmp_path / "audio" / f"frame_{i:03d}.mp3"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake mp3")
        frames.append(
            StoryboardFrame(
                index=i,
                narration=f"narration_{i}",
                duration=3.0,
                audio_path=str(audio_path),
                visual_url="http://old/vid.mp4",
                visual_type="video",
            )
        )
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=frames,
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    # Mock task
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({"engine": "auto"})
    svc.repo.get = AsyncMock(return_value=task)

    # Track TTS calls
    tts_calls: list[str] = []

    async def _fake_synth(text, **kwargs):
        tts_calls.append(text)
        out = tmp_path / "audio" / "frame_001.mp3"
        return TTSResult(audio_path=str(out), srt="1\n00:00:00,000 --> 00:00:02,000\ntest\n", duration=2.0)

    with (
        patch.object(svc, "_make_visual_source") as mock_vis,
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
    ):
        mock_vis.return_value = AsyncMock()
        await svc._regenerate_frame("vg_test", "u1", 1, tmp_path)

    # Only frame 1's narration should have been synthesized
    assert len(tts_calls) == 1
    assert tts_calls[0] == "narration_1"


@pytest.mark.asyncio
async def test_regenerate_frame_persists_audio_metadata_to_storyboard(tmp_path):
    """_regenerate_frame must persist regenerated audio_path/duration/srt to
    storyboard.json (read-merge-write). Without it, the /audio?frame=N endpoint
    and the timeline serve the pre-regeneration audio/duration after a regen.
    """
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)

    # 3-frame storyboard; frame 1 carries OLD audio metadata on disk.
    frames = []
    for i in range(3):
        audio_path = tmp_path / "audio" / f"frame_{i:03d}.mp3"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"old mp3")
        frames.append(
            StoryboardFrame(
                index=i,
                narration=f"narration_{i}",
                duration=3.0,
                audio_path=str(audio_path),
                visual_url="http://old/vid.mp4",
                visual_type="video",
            )
        )
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=frames,
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({"engine": "auto"})
    svc.repo.get = AsyncMock(return_value=task)

    # Fake TTS writes a NEW audio file at a distinct path and returns fresh metadata.
    new_audio = tmp_path / "audio" / "regenerated.mp3"
    new_srt = "1\n00:00:00,000 --> 00:00:02,000\nfreshly regenerated\n"

    async def _fake_synth(text, **kwargs):
        new_audio.parent.mkdir(parents=True, exist_ok=True)
        new_audio.write_bytes(b"new mp3")
        return TTSResult(audio_path=str(new_audio), srt=new_srt, duration=2.0)

    with (
        patch.object(svc, "_make_visual_source") as mock_vis,
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
    ):
        mock_vis.return_value = AsyncMock()
        await svc._regenerate_frame("vg_test", "u1", 1, tmp_path)

    # The on-disk storyboard must reflect the regenerated audio metadata for frame 1.
    on_disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    frame1 = next(f for f in on_disk["frames"] if f["index"] == 1)
    assert frame1["audio_path"] == str(new_audio), "regenerated audio_path not persisted to storyboard.json"
    assert frame1["duration"] == 2.0, "regenerated duration not persisted to storyboard.json"
    assert frame1.get("srt") == new_srt, "regenerated srt not persisted to storyboard.json"
    # Frame 0 must be untouched by the regen of frame 1.
    frame0 = next(f for f in on_disk["frames"] if f["index"] == 0)
    assert frame0["audio_path"] == str(tmp_path / "audio" / "frame_000.mp3")
    assert frame0["duration"] == 3.0


@pytest.mark.asyncio
async def test_synthesize_audio_falls_back_when_tts_duration_is_zero(tmp_path):
    """_synthesize_audio should ensure duration is never 0 (avoids 0-frame render)."""
    from unittest.mock import patch

    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="test")],
    )

    async def _fake_synth(text, **kwargs):
        out = tmp_path / "audio" / "frame_000.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=0.0)

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth), patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()), patch("app.gateway.video_gen.service._write_storyboard_srt"):
        await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural")

    # Duration should have been bumped from 0 to a positive minimum
    assert sb.frames[0].duration > 0


# ---------------------------------------------------------------------------
# _update_storyboard unified safe updater
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_storyboard_atomic_write(tmp_path):
    """_update_storyboard should read-merge-write atomically."""
    import json

    svc = _make_service(tmp_path)

    # Write initial storyboard
    sb_data = {
        "title": "test",
        "frames": [
            {"index": 0, "narration": "hello", "visual_url": None},
            {"index": 1, "narration": "world", "visual_url": None},
        ],
    }
    (tmp_path / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    # Update frame 0's visual_url
    def updater(data):
        for f in data["frames"]:
            if f["index"] == 0:
                f["visual_url"] = "http://example.com/img.png"
                f["visual_type"] = "image"

    await svc._update_storyboard(tmp_path, updater)

    result = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert result["frames"][0]["visual_url"] == "http://example.com/img.png"
    assert result["frames"][0]["visual_type"] == "image"
    # Frame 1 should be untouched
    assert result["frames"][1]["visual_url"] is None


@pytest.mark.asyncio
async def test_update_storyboard_merges_sequential_writes(tmp_path):
    """_update_storyboard read-merge-write：两次顺序写互不覆盖（各自字段都在）。

    注意：这是顺序双写，不是真并发——真并发下的丢写防护（LLM 重生 vs 后台 TTS
    同时落盘）由 tests/test_video_gen_storyboard_writes.py 的
    test_regenerate_frame_prompt_preserves_concurrent_write 覆盖。
    """
    import json

    svc = _make_service(tmp_path)

    sb_data = {
        "title": "test",
        "frames": [
            {"index": 0, "narration": "hello", "visual_url": None, "audio_path": None},
        ],
    }
    (tmp_path / "storyboard.json").write_text(json.dumps(sb_data), encoding="utf-8")

    # Writer A adds audio_path, writer B adds visual_url — both must survive.
    def writer_a(data):
        for f in data["frames"]:
            if f["index"] == 0:
                f["audio_path"] = "/audio/a.mp3"

    def writer_b(data):
        for f in data["frames"]:
            if f["index"] == 0:
                f["visual_url"] = "http://img.png"

    await svc._update_storyboard(tmp_path, writer_a)
    await svc._update_storyboard(tmp_path, writer_b)

    result = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert result["frames"][0]["audio_path"] == "/audio/a.mp3"
    assert result["frames"][0]["visual_url"] == "http://img.png"


@pytest.mark.asyncio
async def test_update_storyboard_noop_if_file_missing(tmp_path):
    """_update_storyboard should be a noop if storyboard.json doesn't exist."""
    svc = _make_service(tmp_path)

    def updater(data):
        data["title"] = "changed"

    # Should not raise
    await svc._update_storyboard(tmp_path, updater)
    assert not (tmp_path / "storyboard.json").exists()


# ---------------------------------------------------------------------------
# _make_on_frame_done callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_on_frame_done_persists_visual_url(tmp_path):
    """_make_on_frame_done should incrementally persist visual_url to storyboard.json."""
    import json

    svc = _make_service(tmp_path)

    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="hello"),
            StoryboardFrame(index=1, narration="world"),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict()), encoding="utf-8")

    callback = svc._make_on_frame_done(sb, tmp_path)

    # Simulate frame 0 completing
    sb.frames[0].visual_url = "http://example.com/frame0.png"
    sb.frames[0].visual_type = "image"
    await callback(0)

    result = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert result["frames"][0]["visual_url"] == "http://example.com/frame0.png"
    assert result["frames"][0]["visual_type"] == "image"
    # Frame 1 should be untouched
    assert result["frames"][1]["visual_url"] is None


@pytest.mark.asyncio
async def test_make_on_frame_done_skips_if_no_visual_url(tmp_path):
    """_make_on_frame_done should be a noop if frame has no visual_url."""
    import json

    svc = _make_service(tmp_path)

    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="hello")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict()), encoding="utf-8")

    callback = svc._make_on_frame_done(sb, tmp_path)
    # Frame 0 has no visual_url set
    await callback(0)

    result = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert result["frames"][0]["visual_url"] is None


# ---------------------------------------------------------------------------
# P3-0: CAS audio naming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_audio_uses_cas_naming(tmp_path):
    """_synthesize_audio should name audio files by md5(narration), not frame index."""
    from unittest.mock import patch

    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="hello world"),
            StoryboardFrame(index=1, narration="foo bar"),
        ],
    )

    async def _fake_synth(text, **kwargs):
        # CAS 键含 voice+speed（2026-07-21）：写服务端分配的 output_path 而非自算文件名
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=2.0)

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth), patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()), patch("app.gateway.video_gen.service._write_storyboard_srt"):
        await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural")

    # Check that audio files use CAS naming
    audio_dir = tmp_path / "audio"
    files = list(audio_dir.iterdir())
    assert len(files) == 2
    # File names should be md5 hashes, not frame_000.mp3
    for f in files:
        assert not f.name.startswith("frame_")
        assert f.suffix == ".mp3"
    # Frame audio_path should point to CAS-named files（键含 voice+speed）
    from app.gateway.video_gen.service import _audio_cache_key

    for frame in sb.frames:
        assert _audio_cache_key(frame.narration, "zh-CN-YunjianNeural", 1.2) in frame.audio_path


@pytest.mark.asyncio
async def test_generate_draft_audio_merges_audio_by_frame_index(tmp_path):
    """_generate_draft_audio synthesizes per-frame TTS, merges audio metadata back
    into storyboard.json keyed by frame.index (E11 口径：非 narration hash——重复旁白
    不再互相覆盖，但 TTS 文件仍按 md5(narration) CAS 去重），并置 payload audio_ready。"""
    import json
    from unittest.mock import patch

    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)

    # Initial storyboard with 2 frames
    sb = Storyboard(
        title="test",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="zh-CN-YunjianNeural"),
        frames=[
            StoryboardFrame(index=0, narration="hello"),
            StoryboardFrame(index=1, narration="world"),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    def _side_effect_synth(text, **kwargs):
        # CAS 键含 voice+speed（2026-07-21）：写服务端分配的 output_path 而非自算文件名
        out = Path(kwargs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=2.0)

    # Mock task
    task = MagicMock()
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()

    with patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_side_effect_synth), patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()), patch("app.gateway.video_gen.service._write_storyboard_srt"):
        await svc._generate_draft_audio("test-task", tmp_path)

    # Verify the storyboard was updated with audio info keyed by frame.index
    result = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    frame0, frame1 = result["frames"][0], result["frames"][1]
    for f in (frame0, frame1):
        assert f.get("audio_path"), f"帧 {f['index']} 缺 audio_path"
        assert Path(f["audio_path"]).exists(), "audio_path 必须指向真实落盘的 TTS 文件"
        assert f.get("duration") == 2.0, "TTS 实测 duration 须合并回 storyboard"
    # 帧键合并：frame0 的音频须对应其 narration（hello）的 CAS 文件，不串帧
    # （tts_speed 取 StoryboardConfig 缺省 1.2，与 test_synthesize_audio_uses_cas_naming 同口径）
    from app.gateway.video_gen.service import _audio_cache_key

    assert _audio_cache_key("hello", "zh-CN-YunjianNeural", 1.2) in frame0["audio_path"]
    assert _audio_cache_key("world", "zh-CN-YunjianNeural", 1.2) in frame1["audio_path"]

    # Verify audio_ready was set in payload
    svc.repo.update.assert_called_once()
    call_args = svc.repo.update.call_args
    payload_str = call_args.kwargs.get("payload") or call_args[1].get("payload", "")
    pl = json.loads(payload_str)
    assert pl.get("audio_ready") is True


# ---------------------------------------------------------------------------
# P3-1: _adjust_index
# ---------------------------------------------------------------------------


def test_adjust_index_noop():
    """_adjust_index should return original when from == to."""
    from app.gateway.routers.video_gen import _adjust_index

    assert _adjust_index(5, 3, 3) == 5


def test_adjust_index_move_forward():
    """Move frame at index 2 to index 5 (forward)."""
    from app.gateway.routers.video_gen import _adjust_index

    # The moved frame goes to to_idx - 1
    assert _adjust_index(2, 2, 5) == 4  # moved frame → new position
    # Frames 3, 4 shift left
    assert _adjust_index(3, 2, 5) == 2
    assert _adjust_index(4, 2, 5) == 3
    # Frame 5 stays
    assert _adjust_index(5, 2, 5) == 5
    # Frame 0, 1 stay
    assert _adjust_index(0, 2, 5) == 0
    assert _adjust_index(1, 2, 5) == 1


def test_adjust_index_move_backward():
    """Move frame at index 5 to index 2 (backward)."""
    from app.gateway.routers.video_gen import _adjust_index

    # The moved frame goes to to_idx
    assert _adjust_index(5, 5, 2) == 2  # moved frame → new position
    # Frames 2, 3, 4 shift right
    assert _adjust_index(2, 5, 2) == 3
    assert _adjust_index(3, 5, 2) == 4
    assert _adjust_index(4, 5, 2) == 5
    # Frame 0, 1 stay
    assert _adjust_index(0, 5, 2) == 0
    assert _adjust_index(1, 5, 2) == 1


def test_adjust_index_adjacent_forward():
    """Move frame at index 2 to index 3 (adjacent forward)."""
    from app.gateway.routers.video_gen import _adjust_index

    assert _adjust_index(2, 2, 3) == 2  # moved → to - 1 = 2 (stays same position visually)
    assert _adjust_index(3, 2, 3) == 3  # not in range (from, to] is empty, stays

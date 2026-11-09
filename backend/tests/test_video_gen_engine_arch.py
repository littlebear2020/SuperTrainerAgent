"""Tests for service.py multi_engine path (阶段 1.5).

engine_arch='multi_engine' routes the full-pipeline entry through
_prepare_storyboard_and_audio → engine.render → mode dispatch, instead of
the legacy _prepare_storyboard_video monolith. legacy path stays unchanged
(covered by test_video_gen_service.py).
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.engines.base import EngineOutcome
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_service(tmp_path, engine_arch="multi_engine"):
    cfg = VideoGenConfig(qihu_api_key="t", engine_arch=engine_arch)
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


class _FakeEngine:
    name = "vimax"

    def __init__(self, outcome):
        self._outcome = outcome
        self.rendered_ctx = None

    async def render(self, ctx):
        self.rendered_ctx = ctx
        return self._outcome


@pytest.mark.asyncio
async def test_legacy_arch_does_not_use_engine(tmp_path):
    """engine_arch='legacy' must NOT touch the engine registry — legacy path intact."""
    svc = _make_service(tmp_path, engine_arch="legacy")
    progress = AsyncMock()
    with (
        patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock, return_value=None) as mock_legacy,
        patch.object(svc, "_get_engine_registry") as mock_reg,
    ):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="ai_image",
            payload={"script": "hello", "mode": "fixed", "auto_render": True},
            task_dir=tmp_path,
            progress=progress,
        )
    mock_legacy.assert_called_once()
    mock_reg.assert_not_called()


@pytest.mark.asyncio
async def test_multi_engine_ai_image_routes_through_legacy_not_engine(tmp_path):
    """Regression (Pexels removal): under engine_arch='multi_engine', ai_image/ai_video
    must STILL route through legacy _prepare_storyboard_video (Worker handoff), NOT the
    engine layer. The former stock_clip engine was the "generic segments engine" that
    handled ai_image/ai_video in multi_engine mode — removed with Pexels. Without it,
    ai_image/ai_video have no engine; routing them to _run_pipeline_engine raises
    'unsupported engine task_type'. Only remotion/vimax are engine-routable now."""
    svc = _make_service(tmp_path, engine_arch="multi_engine")
    progress = AsyncMock()
    with (
        patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock, return_value=None) as mock_legacy,
        patch.object(svc, "_get_engine_registry") as mock_reg,
    ):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="ai_image",
            payload={"script": "hello", "mode": "fixed", "auto_render": True},
            task_dir=tmp_path,
            progress=progress,
        )
    mock_legacy.assert_called_once()
    mock_reg.assert_not_called()


@pytest.mark.asyncio
async def test_multi_engine_vimax_resume_from_draft_routes_through_engine_not_legacy_visual_source(tmp_path):
    """Regression (3.2 end-to-end): resuming a vimax draft (POST /render, which
    sets frame_edits + auto_render) must route through the multi_engine engine path
    (_run_pipeline_engine → VimaxEngine), NOT the legacy _render_from_draft which
    hardcodes _make_visual_source and raises 'visual source for vimax not implemented'.

    The create-via-REST path (auto_render=True, no frame_edits) already worked
    (Entry 1 → _run_pipeline_engine). The draft→render path went through Entry 3
    (_render_from_draft) which only knew legacy visual_sources. Same dispatcher,
    different entry — must both reach the engine for vimax."""
    svc = _make_service(tmp_path)
    progress = AsyncMock()

    with (
        patch.object(svc, "_run_pipeline_engine", new_callable=AsyncMock, return_value=MagicMock()) as mock_engine_path,
        patch.object(svc, "_render_from_draft", new_callable=AsyncMock) as mock_legacy_draft,
    ):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="vimax",
            payload={"frame_edits": {}, "auto_render": True, "script": "a story", "mode": "fixed"},
            task_dir=tmp_path,
            progress=progress,
        )

    # vimax draft→render MUST go through the engine path (VimaxEngine), not legacy
    mock_engine_path.assert_awaited_once()
    mock_legacy_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_multi_engine_writes_storyboard_json_before_engine_render(tmp_path):
    """Regression (1.8): _run_pipeline_engine must persist storyboard.json before
    engine.render. Both real engines read it from disk; RemotionEngine._resolve_duration falls back
    but loses the real duration. 1.5 masked this by mocking the engine registry; this
    test uses an engine that mimics the on-disk storyboard read.
    """
    svc = _make_service(tmp_path)
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="vimax", width=1080, height=1920, voice_id="zh-CN-YunxiNeural"),
        frames=[StoryboardFrame(index=0, narration="a", duration=2.0)],
    )
    audio_path = str(tmp_path / "audio.mp3")
    sub_path = str(tmp_path / "sub.srt")

    class _StoryboardReadingEngine:
        """Mimics an engine that reads task_dir/storyboard.json from disk."""

        name = "vimax"

        def __init__(self):
            self.rendered = False

        async def render(self, ctx):
            self.rendered = True
            path = ctx.task_dir / "storyboard.json"
            if not path.exists():
                raise FileNotFoundError(f"storyboard.json not found in task_dir {ctx.task_dir}")
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["title"] == "t"  # on-disk storyboard matches what prep produced
            return EngineOutcome(mode="segments", segments=[])

    engine = _StoryboardReadingEngine()
    fake_registry = MagicMock()
    fake_registry.get_engine = MagicMock(return_value=engine)
    progress = AsyncMock()

    with (
        patch.object(svc, "_prepare_storyboard_and_audio", new_callable=AsyncMock, return_value=(storyboard, audio_path, sub_path)),
        patch.object(svc, "_get_engine_registry", return_value=fake_registry),
        patch("app.gateway.video_gen.worker_probe.probe_worker_capabilities", new_callable=AsyncMock, return_value=MagicMock()),
        patch("app.gateway.video_gen.worker_probe.decide_pipeline_version", return_value="v2"),
        patch("app.gateway.video_gen.service.build_worker_payload", return_value={"frames": []}),
    ):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="vimax",
            payload={"script": "hello", "mode": "fixed", "auto_render": True},
            task_dir=tmp_path,
            progress=progress,
        )

    assert engine.rendered
    assert (tmp_path / "storyboard.json").exists()  # persisted for the engine (and SSE/timeline endpoints)


@pytest.mark.asyncio
async def test_service_vimax_factory_wires_trainerai_config(tmp_path):
    """阶段 2.6: the registry's vimax engine must carry a config_factory that
    translates TrainerAI video_gen config (qihu key/url, vimax_llm_*) into a valid
    ViMax pipeline config pointing at the host bridges."""
    svc = _make_service(tmp_path)
    engine = svc._get_engine_registry().get_engine("vimax")
    assert engine.name == "vimax"
    cfg = engine._config_factory(tmp_path / "wd", {"script": "x"})

    # LLM runs on the 360 gateway with the qihu key
    assert cfg["chat_model"]["init_args"]["api_key"] == svc._config.qihu_api_key
    assert cfg["chat_model"]["init_args"]["model"] == svc._config.vimax_llm_model
    assert cfg["chat_model"]["init_args"]["model_provider"] == "openai"
    # generators are the host bridges (C5 2026-10-12: image = 360 seedream bridge
    # by default; video = SeedanceVideoGeneratorForViMax)
    assert cfg["image_generator"]["class_path"].endswith("Seedream360GeneratorForViMax")
    assert cfg["video_generator"]["class_path"].endswith("SeedanceVideoGeneratorForViMax")
    assert cfg["working_dir"] == str(tmp_path / "wd")


@pytest.mark.asyncio
async def test_legacy_vimax_raises_explicit_misconfiguration_error(tmp_path):
    """Regression (vg_7477487923ef): a vimax task under engine_arch='legacy'
    must raise an explicit, actionable misconfiguration error — NOT the old
    "visual source for vimax not implemented in this phase" message that reads
    like an unimplemented feature and sent the agent / users down a rabbit hole.

    Root cause: config.yaml omitted engine_arch, so it defaulted to 'legacy',
    and vimax (which only exists in the engine layer) fell into _make_visual_source.
    The new error names the fix (set engine_arch: multi_engine)."""
    svc = _make_service(tmp_path, engine_arch="legacy")

    with pytest.raises(ValueError) as exc_info:
        svc._make_visual_source("vimax")

    msg = str(exc_info.value)
    # actionable + names the fix, not "not implemented"
    assert "engine_arch='multi_engine'" in msg or "engine_arch: multi_engine" in msg
    assert "misconfiguration" in msg
    # the old misleading wording must be gone for vimax
    assert "not implemented" not in msg


@pytest.mark.asyncio
async def test_chat_triggered_vimax_render_routes_through_engine_not_legacy(tmp_path):
    """Regression (vg_7477487923ef, the actual failing payload): the chat path
    is plan_storyboard (draft, auto_render=False) → user confirms → generate_video
    (run_pipeline_for_tool re-sets auto_render=True, no frame_edits). That payload
    enters _run_pipeline at Entry 1 (full pipeline). Under multi_engine it MUST
    route to _run_pipeline_engine (VimaxEngine) — never the legacy
    _prepare_storyboard_video → _make_visual_source path that crashed with
    'visual source for vimax not implemented'.

    This is the exact payload shape that failed in production:
    {mode: generate, style: Anime Style, auto_render: True, no frame_edits}."""
    svc = _make_service(tmp_path)  # multi_engine (the fix)
    progress = AsyncMock()

    with (
        patch.object(svc, "_run_pipeline_engine", new_callable=AsyncMock, return_value=MagicMock()) as mock_engine_path,
        patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock) as mock_legacy_path,
        patch.object(svc, "_make_visual_source") as mock_visual_source,
    ):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="vimax",
            # the exact production payload shape: auto_render=True, NO frame_edits
            payload={
                "mode": "generate",
                "style": "Anime Style",
                "auto_render": True,
                "topic": "职场高效沟通",
                "n_scenes": 12,
            },
            task_dir=tmp_path,
            progress=progress,
        )

    mock_engine_path.assert_awaited_once()
    mock_legacy_path.assert_not_awaited()
    mock_visual_source.assert_not_called()


@pytest.mark.asyncio
async def test_run_pipeline_engine_rejects_remotion(tmp_path):
    """Remotion removed from engine layer (2026-07-15 split): _run_pipeline_engine must
    reject task_type="remotion" with ValueError — remotion renders via chat direct-write
    now, never the backend engine path. (vimax is the only engine-routable task_type.)

    _prepare_storyboard_and_audio is mocked so the test reaches the task_type rejection
    (step 2) without the async LLM/TTS prep — which is contamination-sensitive under the
    full suite's cross-file event-loop pollution (mirrors test_multi_engine_writes_
    storyboard_json; the rejection is the point, not the prep)."""
    svc = _make_service(tmp_path)
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion", width=1080, height=1920, voice_id="zh-CN-YunxiNeural"),
        frames=[StoryboardFrame(index=0, narration="a", duration=2.0)],
    )
    with patch.object(
        svc,
        "_prepare_storyboard_and_audio",
        new_callable=AsyncMock,
        return_value=(storyboard, str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt")),
    ):
        with pytest.raises(ValueError, match="vimax only"):
            await svc._run_pipeline_engine(
                task_id="t1",
                user_id="u1",
                task_type="remotion",
                payload={"mode": "generate", "topic": "topic"},
                task_dir=tmp_path,
                progress=AsyncMock(),
            )


@pytest.mark.asyncio
async def test_idea2video_story_ready_stores_result_as_json_string(tmp_path):
    """Regression: STORY_READY must store result as a JSON STRING, not a dict.
    ProgressUpdater.__call__ passes kwargs straight to repo.update, and SQLite
    rejects a dict for the result column ('Error binding parameter: type dict is
    not supported'). idea2video's expanded story must be json.dumps'd."""
    from app.gateway._shared.task_framework.schemas import STORY_READY
    from app.gateway.video_gen.pipeline_result import PipelineDraft

    svc = _make_service(tmp_path)
    progress = AsyncMock()
    with patch.object(svc, "_generate_draft", new_callable=AsyncMock, return_value=PipelineDraft(story="一段故事")):
        await svc._run_pipeline(
            task_id="t1",
            user_id="u1",
            task_type="vimax",
            payload={"mode": "generate", "topic": "x", "pipeline": "idea2video", "auto_render": False},
            task_dir=tmp_path,
            progress=progress,
        )
    story_calls = [c for c in progress.call_args_list if c.kwargs.get("status") == STORY_READY]
    assert story_calls, "expected a STORY_READY progress call for the idea2video story draft"
    result_val = story_calls[0].kwargs.get("result")
    assert isinstance(result_val, str), f"result must be a JSON string (SQLite can't bind a dict), got {type(result_val).__name__}"
    assert json.loads(result_val)["story"] == "一段故事"


@pytest.mark.asyncio
async def test_transient_cancel_reschedules_render(tmp_path):
    """B1: a transient CancelledError (chat-run interrupt) reschedules the render
    (resume) instead of marking failed — but only when NOT explicitly deleting."""
    import asyncio
    from unittest.mock import patch

    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=MagicMock(retry_count=0))
    svc.repo.update = AsyncMock()

    def _reschedule_calls(mock_ct):
        """筛出 create_task(_wrap_pipeline) 的重排调用。

        2026-08-24 活性重构（W2/W2b）：_wrap_pipeline 现在会为排队期/阶段滴答
        create_task(liveness tick)--那是活性信号，不是重排；B1 断言只数重排。
        """
        out = []
        for c in mock_ct.call_args_list:
            arg = c.args[0] if c.args else c.kwargs.get("coro")
            name = getattr(getattr(arg, "cr_code", None), "co_name", None)
            if name == "_wrap_pipeline":
                out.append(c)
        return out

    # Transient cancel (not deleting) → reschedule (status=pending), not failed
    with (
        patch.object(svc, "_run_pipeline", new_callable=AsyncMock, side_effect=asyncio.CancelledError()),
        patch("asyncio.create_task") as mock_ct,
    ):
        mock_ct.return_value = MagicMock()
        await svc._wrap_pipeline("t1", "u1", "vimax", {})
    statuses = [c.kwargs.get("status") for c in svc.repo.update.call_args_list if c.kwargs.get("status")]
    assert "pending" in statuses, f"transient cancel should reschedule (pending), got statuses={statuses}"
    assert _reschedule_calls(mock_ct), "should have created a reschedule task"

    # Explicit delete → failed (no reschedule)
    svc.repo.update.reset_mock()
    svc._deleting.add("t2")
    with (
        patch.object(svc, "_run_pipeline", new_callable=AsyncMock, side_effect=asyncio.CancelledError()),
        patch("asyncio.create_task") as mock_ct2,
    ):
        with pytest.raises(asyncio.CancelledError):
            await svc._wrap_pipeline("t2", "u1", "vimax", {})
    statuses2 = [c.kwargs.get("status") for c in svc.repo.update.call_args_list if c.kwargs.get("status")]
    assert "failed" in statuses2, "explicit delete cancel should fail, not reschedule"
    assert not _reschedule_calls(mock_ct2), "explicit delete should NOT reschedule"


@pytest.mark.asyncio
async def test_cleanup_preserves_task_dir(tmp_path):
    """B2: _cleanup_task does NOT delete the task_dir — preserve on-disk artifacts
    (ViMax scene finals, shots, characters.json) for resume after cancel/restart."""
    from unittest.mock import MagicMock

    svc = _make_service(tmp_path)
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "vimax_working").mkdir()
    (task_dir / "vimax_working" / "scene_0").mkdir()
    (task_dir / "vimax_working" / "scene_0" / "final_video.mp4").write_bytes(b"x")

    await svc._cleanup_task(MagicMock(id="t1", user_id="u1"))

    assert task_dir.exists(), "task_dir must be preserved for resume"
    assert (task_dir / "vimax_working" / "scene_0" / "final_video.mp4").exists()


@pytest.mark.asyncio
async def test_delete_task_removes_preserved_dir(tmp_path):
    """B2: explicit delete_task DOES remove the (otherwise-preserved) task_dir."""
    from unittest.mock import MagicMock

    svc = _make_service(tmp_path)
    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "artifact.mp4").write_bytes(b"x")
    svc.repo.get = AsyncMock(return_value=MagicMock(id="t1", user_id="u1"))
    svc.repo.delete = AsyncMock()

    await svc.delete_task("t1", "u1")

    assert not task_dir.exists(), "explicit delete should remove the preserved dir"


# ── Remotion backend path removed (2026-07-15 split) ──────────────────────────
# Remotion now renders via chat direct-write (course-video agent + auto-video-flow);
# /api/video/create no longer accepts video_type=remotion and the engine layer no
# longer routes remotion. These guard against a regression that re-opens the broken
# nested-agent path (RemotionAgentRunner re-launching course-video → self-contradiction).


def test_remotion_removed_from_create_request_schema():
    """CreateVideoRequest must reject video_type="remotion" with ValidationError —
    remotion renders via chat now, /api/video/create returns 422 for it."""
    from pydantic import ValidationError

    from app.gateway.video_gen.schemas import CreateVideoRequest

    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="remotion", mode="fixed", script="x")


def test_is_engine_routable_excludes_remotion(tmp_path):
    """_is_engine_routable must exclude "remotion" (only vimax remains). Otherwise a
    remotion task would route into _run_pipeline_engine → get_engine("remotion") →
    500 UnknownVideoTypeError."""
    svc = _make_service(tmp_path)
    assert svc._is_engine_routable("remotion") is False
    assert svc._is_engine_routable("vimax") is True

"""Risk C: narration failure + skip_tts must NOT silently produce a silent video.

vimax uses skip_tts=True so audio.mp3 is never created. If narration also
fails, the overlay fallback finds no audio and returns the video as-is
(silent). This must raise instead of silently degrading.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax import narration_generator
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine


def _make_ctx(task_dir: Path) -> EngineContext:
    """Build a real EngineContext (Pydantic BaseModel) for render().

    Using the real model instead of a hand-written stand-in avoids silent
    schema drift: EngineContext requires user_id + task_id + task_dir, and if
    render() ever starts reading ctx.task_id (e.g. for logging/progress), a
    stand-in missing it would let the test falsely pass.
    """
    return EngineContext(
        user_id="u1",
        task_id="t1",
        task_dir=task_dir,
        voice_id="zh-CN-YunjianNeural",
        request={"script": "hello", "user_requirement_extra": ""},
    )


def _make_engine(tmp_path, monkeypatch, *, narration_succeeds: bool, audio_mp3_exists: bool):
    """Build a VimaxEngine with controllable narration + overlay + audio.mp3.

    render() derives working_dir = ctx.task_dir / "vimax_working" and expects
    the pipeline to write final_video.mp4 there. We stub the pipeline to do
    exactly that. generate_vimax_narration is imported locally inside render()
    (``from .narration_generator import generate_vimax_narration``), so we
    patch it on the narration_generator module, not the engine module.
    """
    working_dir_holder: dict = {}

    async def _fake_pipeline(*args, **kwargs):
        wd = working_dir_holder.get("wd")
        if wd is not None:
            (wd / "final_video.mp4").write_bytes(b"\x00\x00\x00\x20ftyp")

    def _config_factory(working_dir, request):
        working_dir_holder["wd"] = working_dir
        return {}

    overlay_calls: list[dict] = []

    async def _overlay_fn(video_path, task_dir, **kwargs):
        overlay_calls.append(kwargs)
        return video_path

    engine = VimaxEngine(
        config_factory=_config_factory,
        pipeline_factory=lambda config_path: _fake_pipeline,
        overlay_fn=_overlay_fn,
        narration_llm_factory=lambda: MagicMock(),
    )

    if audio_mp3_exists:
        (tmp_path / "audio.mp3").write_bytes(b"ID3")

    if narration_succeeds:
        audio_out = tmp_path / "narration.mp3"
        audio_out.write_bytes(b"ID3")
        monkeypatch.setattr(narration_generator, "generate_vimax_narration", AsyncMock(return_value=audio_out))
    else:
        monkeypatch.setattr(
            narration_generator,
            "generate_vimax_narration",
            AsyncMock(side_effect=RuntimeError("360 LLM down")),
        )

    return engine, overlay_calls


@pytest.mark.asyncio
async def test_narration_failure_with_no_fallback_audio_raises(tmp_path, monkeypatch):
    """skip_tts + narration failure + no audio.mp3 → raise, not silent video."""
    engine, _ = _make_engine(tmp_path, monkeypatch, narration_succeeds=False, audio_mp3_exists=False)
    ctx = _make_ctx(tmp_path)

    with pytest.raises(RuntimeError, match="no fallback audio"):
        await engine.render(ctx)


@pytest.mark.asyncio
async def test_narration_failure_with_fallback_audio_does_not_raise(tmp_path, monkeypatch):
    """If audio.mp3 exists (e.g. non-vimax or skip_tts off), fallback is fine."""
    engine, _ = _make_engine(tmp_path, monkeypatch, narration_succeeds=False, audio_mp3_exists=True)
    ctx = _make_ctx(tmp_path)

    outcome = await engine.render(ctx)
    assert outcome is not None  # did not raise; fell back to audio.mp3


@pytest.mark.asyncio
async def test_narration_success_uses_narration_audio(tmp_path, monkeypatch):
    """Happy path: narration succeeds → overlay called with audio_override."""
    engine, overlay_calls = _make_engine(tmp_path, monkeypatch, narration_succeeds=True, audio_mp3_exists=False)
    ctx = _make_ctx(tmp_path)

    await engine.render(ctx)
    assert any("audio_override" in kw for kw in overlay_calls), "narration success must pass audio_override to overlay_fn"

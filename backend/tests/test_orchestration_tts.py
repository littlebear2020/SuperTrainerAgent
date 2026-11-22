from unittest.mock import AsyncMock

import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult

DOUBAO_VOICE = "doubao:zh_female_vv_uranus_bigtts"


@pytest.mark.asyncio
async def test_synthesize_routes_doubao_without_fallback_options(tmp_path, monkeypatch):
    called = {}

    async def fake_doubao(text, voice_id, speed, output_path, **kwargs):
        called.update(voice_id=voice_id, kwargs=kwargs)
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_doubao", fake_doubao)
    await tts_mod.synthesize("你好", voice_id=DOUBAO_VOICE, output_path=str(tmp_path / "f.mp3"), section_id="course-1", emotion="平静")
    assert called == {"voice_id": DOUBAO_VOICE, "kwargs": {"section_id": "course-1", "emotion": "平静"}}


@pytest.mark.asyncio
async def test_synthesize_routes_existing_clone_without_fallback(tmp_path, monkeypatch):
    clone = AsyncMock(return_value=TTSResult(audio_path="clone.mp3", srt="", duration=1.0))
    monkeypatch.setattr(tts_mod, "_synthesize_clone", clone)
    result = await tts_mod.synthesize("你好", voice_id="clone:S_1", output_path=str(tmp_path / "f.mp3"))
    assert result.audio_path == "clone.mp3"
    clone.assert_awaited_once()


@pytest.mark.parametrize("voice_id", ["preset:146", "edge:zh-CN-XiaoxiaoNeural", "zh-CN-XiaoxiaoNeural"])
@pytest.mark.asyncio
async def test_synthesize_rejects_retired_providers_before_io(voice_id, tmp_path, monkeypatch):
    doubao = AsyncMock()
    monkeypatch.setattr(tts_mod, "_synthesize_doubao", doubao)
    with pytest.raises(ValueError, match="原音色已停用"):
        await tts_mod.synthesize("不得调用", voice_id=voice_id, output_path=str(tmp_path / "f.mp3"))
    doubao.assert_not_awaited()


@pytest.mark.asyncio
async def test_doubao_success_preserves_audio_duration_srt_and_word_cues(tmp_path, monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(
        tts_mod,
        "_call_doubao_with_retry",
        AsyncMock(return_value=(b"DOUBAOAUDIO", [{"word": "你", "startTime": 0.0, "endTime": 0.25}])),
    )
    monkeypatch.setattr(tts_mod, "_get_audio_duration", lambda path: 3.5)
    out = tmp_path / "f.mp3"
    result = await tts_mod._synthesize_doubao("你好", DOUBAO_VOICE, 1.0, str(out))
    assert out.read_bytes() == b"DOUBAOAUDIO"
    assert result.duration == 3.5
    assert result.actual_voice_id == DOUBAO_VOICE
    assert result.word_cues == [("你", 0.0, 0.25)]
    assert "00:00:00,000 --> 00:00:00,250" in result.srt


def test_words_to_srt_clamps_non_monotonic_timestamps():
    result = tts_mod._words_to_srt(
        [
            {"word": "二", "startTime": 0.0, "endTime": 0.0},
            {"word": "一", "startTime": 0.0, "endTime": 0.0},
        ]
    )
    assert "00:00:00,000 --> 00:00:00,001" in result
    assert "00:00:00,001 --> 00:00:00,002" in result

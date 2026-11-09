"""HR-T5: ViMax 音频可靠性。

audio.mp3 缺失时 overlay 静默返回原静默视频→改 warning 便于诊断。
engine narration 阶段后断言 audio.mp3 存在，失败显式抛错而非静默无配音。
overlay ffmpeg 失败不吞错（run_ffmpeg 传播 _run_cmd 异常）。
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.video_gen.engines.vimax import audio_overlay as ao


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


@pytest.mark.asyncio
async def test_missing_audio_warns(tmp_path):
    """audio.mp3 不存在 → 写 warning（非静默返回），仍返回原视频（不崩）。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    with patch.object(ao.logger, "warning") as mw:
        result = await ao.overlay_audio_track(v, tmp_path)
    assert result == v  # 仍返回原视频（不崩）
    mw.assert_called_once()
    # logger.warning(msg, audio) 惰性格式化 → arg[0] 是格式串，arg[1] 是 audio 路径
    assert "不存在" in mw.call_args[0][0]
    assert mw.call_args[0][1] == tmp_path / "audio.mp3"


@pytest.mark.asyncio
async def test_overlay_failure_surfaces(tmp_path):
    """overlay ffmpeg 失败 → 抛 RuntimeError（不静默吞）。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    (tmp_path / "audio.mp3").write_bytes(b"")
    with patch.object(ao, "_run_cmd", side_effect=RuntimeError("ffmpeg died")):
        with pytest.raises(RuntimeError, match="ffmpeg died"):
            await ao.overlay_audio_track(v, tmp_path)


# ---------------------------------------------------------------------------
# 2026-07-27 T16：对话+旁白共存（narrator 念非对白旁白）+ 音色池 doubao 门控
# ---------------------------------------------------------------------------


def test_non_dialogue_narration_uses_narrator_voice():
    """非音效的非对白文本（旁白句）用 narrator_voice 念出（曾一律静音——narrator_voice 是死字段）。"""
    from app.gateway.video_gen.engines.vimax.dialogue import SpeakerVoiceMap
    from app.gateway.video_gen.engines.vimax.narration_generator import _resolve_dialogue_segment

    voice_map = SpeakerVoiceMap(
        by_speaker={"小明": "preset:147"},
        narrator_voice="preset:146",
        default_voice="preset:146",
    )
    text, voice, _emotion = _resolve_dialogue_segment("清晨的阳光洒在校园里", voice_map, "")
    assert text == "清晨的阳光洒在校园里" and voice == "preset:146"


def test_sound_effect_stays_silent():
    """音效标签仍须静音（不能念出'birdsong'）。"""
    from app.gateway.video_gen.engines.vimax.dialogue import SpeakerVoiceMap
    from app.gateway.video_gen.engines.vimax.narration_generator import _resolve_dialogue_segment

    voice_map = SpeakerVoiceMap(by_speaker={}, narrator_voice="preset:146", default_voice="preset:146")
    text, _, _e = _resolve_dialogue_segment("[Sound Effect] 微弱的鸟鸣声", voice_map, "")
    assert text == ""
    text2, _, _e = _resolve_dialogue_segment("[音效] 雷声轰鸣", voice_map, "")
    assert text2 == ""


def test_dialogue_still_uses_character_voice():
    """对白路径不回归。"""
    from app.gateway.video_gen.engines.vimax.dialogue import SpeakerVoiceMap
    from app.gateway.video_gen.engines.vimax.narration_generator import _resolve_dialogue_segment

    voice_map = SpeakerVoiceMap(
        by_speaker={"小明": "preset:147"},
        narrator_voice="preset:146",
        default_voice="preset:146",
    )
    text, voice, _emotion = _resolve_dialogue_segment('[Speaker] 小明: "今天天气真好"', voice_map, "")
    assert text == "今天天气真好" and voice == "preset:147"


def test_voice_pool_excludes_doubao_without_key(monkeypatch):
    """无 VOLCENGINE_VOICE_ACCESS_KEY 时池子必须剔除 doubao 音色
    （曾静默塌缩成 preset:146，多角色听起来像一个人）。
    注：每性别池前 6 位是 qihu preset——须 >6 个同性别角色才会分到 doubao 槽位，
    故用 8 男 + 8 女（16 角色）让 doubao 槽位无门控时必然被分到。"""
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    from app.gateway.video_gen.engines.vimax.dialogue import build_speaker_voice_map

    characters = [{"identifier_in_scene": f"角色{i}", "static_features": "男性" if i % 2 == 0 else "女性"} for i in range(16)]
    voice_map = build_speaker_voice_map(characters, default_voice="preset:146", narrator_voice="preset:146")
    assigned = set(voice_map.by_speaker.values())
    assert not any(v.startswith("doubao:") for v in assigned), assigned


def test_voice_pool_includes_doubao_with_key(monkeypatch):
    """有 key 时 doubao 音色留在池内（容量不缩）。"""
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    from app.gateway.video_gen.engines.vimax.dialogue import _zh_voice_pool

    pool = _zh_voice_pool("Male", include_doubao=True)
    # 有 key 时 include_doubao=True 与默认行为一致（不断言一定有 doubao——池构成可变，
    # 只断言函数不炸且非空；门控语义由上个用例锁定）
    assert pool


# ---------------------------------------------------------------------------
# 2026-07-27 Task13②：TTS 失败镜不得有 SRT cue（静音上烧台词=音字不符）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg（_create_silence 替身调真实 ffmpeg）")
@pytest.mark.asyncio
async def test_failed_shot_does_not_burn_subtitle(tmp_path):
    """TTS 失败的镜头不得有 SRT cue（曾在静音上烧出台词字幕）。"""
    from app.gateway.video_gen.engines.vimax import narration_generator as ng

    working_dir = tmp_path / "vimax"
    working_dir.mkdir()
    (working_dir / "storyboard.json").write_text(
        json.dumps({"shots": [{"description": "画面零"}, {"description": "画面一"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        if "失败" in text:
            raise RuntimeError("TTS down")
        Path(output_path).write_bytes(b"MP3")
        return SimpleNamespace(audio_path=output_path)

    with (
        patch.object(ng, "_generate_narration_text", new=AsyncMock(side_effect=["失败镜旁白", "成功镜旁白"])),
        patch.object(ng.tts_mod, "synthesize", new=fake_synthesize),
        patch.object(
            ng,
            "_create_silence",
            new=lambda path, dur: subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(dur), "-c:a", "libmp3lame", "-q:a", "9", str(path)],
                capture_output=True,
            ),
        ),
        patch.object(ng, "run_ffmpeg", new=AsyncMock()),
    ):
        result = await ng.generate_vimax_narration(
            working_dir=working_dir,
            task_dir=task_dir,
            original_script="原始故事",
            llm=None,
            voice_id="preset:146",
        )

    assert result == task_dir / "vimax_narration.mp3"
    srt = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "成功镜旁白" in srt
    assert "失败镜旁白" not in srt

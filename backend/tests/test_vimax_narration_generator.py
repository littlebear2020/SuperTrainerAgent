"""Tests for ViMax per-shot narration generator (Phase 3).

Tests:
1. _find_storyboard_files: idea2video (scene_N/) vs script2video (root)
2. _load_shots: parse storyboard.json shot list
3. generate_vimax_narration: end-to-end with mocked LLM + TTS
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.engines.vimax.dialogue import SpeakerVoiceMap
from app.gateway.video_gen.engines.vimax.narration_generator import (
    _find_storyboard_files,
    _load_shots,
    _resolve_dialogue_segment,
    generate_vimax_narration,
)


def test_find_storyboard_files_idea2video_scene_dirs(tmp_path):
    """idea2video: scene_N/storyboard.json (per-scene)."""
    working = tmp_path / "vimax_working"
    for idx in range(3):
        scene_dir = working / f"scene_{idx}"
        scene_dir.mkdir(parents=True)
        (scene_dir / "storyboard.json").write_text("[]")
    result = _find_storyboard_files(working)
    assert len(result) == 3
    assert "scene_0" in str(result[0])
    assert "scene_2" in str(result[2])


def test_find_storyboard_files_script2video_root(tmp_path):
    """script2video: storyboard.json at working_dir root (no scene dirs)."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text("[]")
    (working / "final_video.mp4").write_bytes(b"x")  # not a scene dir
    result = _find_storyboard_files(working)
    assert len(result) == 1
    assert result[0] == working / "storyboard.json"


def test_find_storyboard_files_none(tmp_path):
    """No storyboard.json → empty list (caller falls back to TrainerAI audio)."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    result = _find_storyboard_files(working)
    assert result == []


def test_load_shots_list_format(tmp_path):
    """storyboard.json as a list of shot dicts."""
    sb = tmp_path / "storyboard.json"
    sb.write_text(
        json.dumps(
            [
                {"idx": 0, "description": "wide shot of the office"},
                {"idx": 1, "description": "close-up of the character"},
            ]
        )
    )
    shots = _load_shots(sb)
    assert len(shots) == 2
    assert shots[0]["description"] == "wide shot of the office"


def test_load_shots_dict_with_shots_key(tmp_path):
    """storyboard.json as {"shots": [...]} dict."""
    sb = tmp_path / "storyboard.json"
    sb.write_text(json.dumps({"shots": [{"idx": 0, "description": "test"}]}))
    shots = _load_shots(sb)
    assert len(shots) == 1
    assert shots[0]["description"] == "test"


def test_load_shots_list_of_strings_coerced(tmp_path):
    """storyboard.json as a list of bare description strings (defensive).

    script2video writes list-of-dict (model_dump), but idea2video's per-scene
    format is unverified. Bare strings must not crash downstream shot.get().
    """
    sb = tmp_path / "storyboard.json"
    sb.write_text(json.dumps(["wide shot of the office", "close-up of character"]))
    shots = _load_shots(sb)
    assert len(shots) == 2
    assert all(isinstance(s, dict) for s in shots), "str elements must be coerced to dict"
    assert shots[0]["description"] == "wide shot of the office"


@pytest.mark.asyncio
async def test_generate_vimax_narration_fallback_no_storyboard(tmp_path):
    """No storyboard.json and no audio.mp3 → returns None (engine loud-fail; 防无声成片)."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    result = await generate_vimax_narration(
        working_dir=working,
        task_dir=tmp_path,
        original_script="test",
        llm=MagicMock(),
        voice_id="preset:146",
    )
    assert result is None


@pytest.mark.asyncio
async def test_generate_vimax_narration_end_to_end(tmp_path):
    """Full flow: storyboard → LLM narration → TTS → concat → vimax_narration.mp3."""
    working = tmp_path / "vimax_working"
    scene_dir = working / "scene_0"
    scene_dir.mkdir(parents=True)
    # Write a storyboard with 2 shots
    (scene_dir / "storyboard.json").write_text(
        json.dumps(
            [
                {"idx": 0, "description": "wide shot"},
                {"idx": 1, "description": "close-up"},
            ]
        )
    )

    # Mock LLM
    fake_llm = AsyncMock()
    fake_resp = MagicMock()
    fake_resp.content = "这是旁白文本"
    fake_llm.ainvoke.return_value = fake_resp

    # Mock TTS
    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            result = await generate_vimax_narration(
                working_dir=working,
                task_dir=tmp_path,
                original_script="原始故事",
                llm=fake_llm,
                voice_id="preset:146",
            )

    assert result == tmp_path / "vimax_narration.mp3"
    assert fake_llm.ainvoke.call_count == 2  # one per shot


@pytest.mark.asyncio
async def test_generate_vimax_narration_uses_visual_desc_field(tmp_path):
    """ViMax ShotBriefDescription uses `visual_desc`/`audio_desc`, NOT `description`.

    Regression: the narration LLM received an empty shot_description (because
    the code read shot.get('description')) and responded with the meta-message
    '请提供具体的故事背景以生成具体画面和旁白' for every shot. Verify the LLM
    now receives the actual visual_desc content.
    """
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(
        json.dumps(
            [
                {
                    "idx": 0,
                    "is_last": False,
                    "cam_idx": 0,
                    "visual_desc": "清晨卧室远景，阳光透过窗帘洒进房间，小明缓缓坐起身。",
                    "audio_desc": "[音效] 微弱的鸟鸣声",
                },
            ]
        )
    )

    fake_llm = AsyncMock()
    fake_resp = MagicMock()
    fake_resp.content = "清晨的阳光唤醒了小明"
    fake_llm.ainvoke.return_value = fake_resp

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            await generate_vimax_narration(
                working_dir=working,
                task_dir=tmp_path,
                original_script="原始故事",
                llm=fake_llm,
                voice_id="preset:146",
            )

    # The LLM must have received the visual_desc content (not empty).
    assert fake_llm.ainvoke.call_count == 1
    user_msg = fake_llm.ainvoke.call_args[0][0][1]["content"]  # messages[1].content
    assert "清晨卧室远景" in user_msg, f"LLM must receive visual_desc; got: {user_msg!r}"


# ── P1/P2: direct unit tests for _resolve_dialogue_segment (decision logic) ──


def _vmap():
    """2-speaker + narrator voice map for _resolve_dialogue_segment tests."""
    return SpeakerVoiceMap(
        by_speaker={"部门主管": "preset:147", "菲利普": "preset:153"},
        narrator_voice="preset:146",
        default_voice="preset:146",
    )


def test_resolve_dialogue_segment_speaker_line():
    """A [Speaker] line → (line without speaker name, that speaker's voice)."""
    text, voice, _emotion = _resolve_dialogue_segment('[Speaker] 菲利普: "什么事情这么重要？"', _vmap(), "visual desc")
    assert text == "什么事情这么重要？"
    assert voice == "preset:153"  # 菲利普's assigned voice


def test_resolve_dialogue_segment_sound_effect_is_silent():
    """A [Sound Effect] line → ('', narrator voice) → caller inserts silence."""
    text, voice, _emotion = _resolve_dialogue_segment("[Sound Effect] Soft footsteps", _vmap(), "visual desc")
    assert text == "", "sound-effect shots must not be TTS-read"
    assert voice == "preset:146"  # narrator voice (irrelevant since text="")


def test_resolve_dialogue_segment_empty_audio_is_silent():
    """Empty/None audio_desc → ('', narrator voice), not a visual-excerpt fallback
    (reading a scene description aloud is the 'reads the script' bug)."""
    text, voice, _emotion = _resolve_dialogue_segment("", _vmap(), "a wide shot of the office")
    assert text == ""
    assert "wide shot" not in text, "must NOT fall back to reading the visual desc"


def test_resolve_dialogue_segment_unknown_speaker_uses_default():
    """A speaker not in the voice map → default voice, line still used."""
    text, voice, _emotion = _resolve_dialogue_segment('[Speaker] 陌生人: "你好。"', _vmap(), "visual")
    assert text == "你好。"
    assert voice == "preset:146"  # default


# ── P1/P2: end-to-end generate_vimax_narration dialogue-mode tests ──


@pytest.mark.asyncio
async def test_generate_vimax_narration_uses_speaker_lines_not_llm(tmp_path):
    """P1: when characters are provided, narration uses the actual [Speaker]
    dialogue line for TTS — NOT an LLM-rewritten third-person narration.

    The LLM must NOT be invoked at all for dialogue shots (regression: the old
    flow called the LLM per shot to 'write a narration', producing '像在念脚本'
    third-person descriptions instead of character speech)."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(
        json.dumps(
            [
                {"idx": 0, "visual_desc": "wide shot", "audio_desc": "[Sound Effect] footsteps"},
                {
                    "idx": 1,
                    "visual_desc": "over-the-shoulder",
                    "audio_desc": '[Speaker] 菲利普: "什么事情这么重要？"',
                },
            ]
        )
    )
    (working / "characters.json").write_text(json.dumps([{"identifier_in_scene": "菲利普", "static_features": "年轻男性"}]))

    fake_llm = AsyncMock()  # MUST NOT be called for dialogue
    fake_resp = MagicMock()
    fake_resp.content = "should-not-be-used"
    fake_llm.ainvoke.return_value = fake_resp

    synthesized = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        synthesized.append((text, voice_id))
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            await generate_vimax_narration(
                working_dir=working,
                task_dir=tmp_path,
                original_script="原始故事",
                llm=fake_llm,
                voice_id="preset:146",
                characters=[{"identifier_in_scene": "菲利普", "static_features": "年轻男性"}],
            )

    # LLM never invoked — dialogue lines used directly.
    assert fake_llm.ainvoke.call_count == 0, "dialogue shots must not call the LLM"
    # Shot 0 was a [Sound Effect] → silent (no TTS call). Shot 1 is the only TTS call.
    assert len(synthesized) == 1, f"expected 1 TTS call (shot 1 dialogue); got {len(synthesized)}"
    # The TTS text is the actual character line, not a narration.
    dialogue_text = synthesized[0][0]
    assert "什么事情这么重要" in dialogue_text
    assert "菲利普" not in dialogue_text, "speaker name must be stripped from TTS text"


@pytest.mark.asyncio
async def test_generate_vimax_narration_assigns_distinct_voices(tmp_path):
    """P2: two speakers in a scene get two different voice_ids in the TTS calls."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(
        json.dumps(
            [
                {"idx": 0, "visual_desc": "s1", "audio_desc": '[Speaker] 部门主管: "你好。"'},
                {"idx": 1, "visual_desc": "s2", "audio_desc": '[Speaker] 菲利普: "你好。"'},
            ]
        )
    )
    fake_llm = AsyncMock()
    synthesized = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        synthesized.append(voice_id)
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            await generate_vimax_narration(
                working_dir=working,
                task_dir=tmp_path,
                original_script="",
                llm=fake_llm,
                voice_id="preset:146",
                characters=[
                    {"identifier_in_scene": "部门主管", "static_features": "中年男性"},
                    {"identifier_in_scene": "菲利普", "static_features": "年轻男性"},
                ],
            )

    assert len(synthesized) == 2
    assert synthesized[0] != synthesized[1], "two speakers must get distinct voices"


@pytest.mark.asyncio
async def test_generate_vimax_narration_sound_effect_shot_is_silent(tmp_path):
    """P1: a [Sound Effect] shot must NOT be read aloud by TTS (saying '微弱的
    鸟鸣声' out loud is jarring). It produces a silent chunk matching the shot's
    video duration so audio stays aligned with the video timeline."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(json.dumps([{"idx": 0, "visual_desc": "wide shot", "audio_desc": "[Sound Effect] Soft footsteps"}]))
    # Fake a video so silence duration is non-trivial.
    shots_dir = working / "shots" / "0"
    shots_dir.mkdir(parents=True)
    (shots_dir / "video.mp4").write_bytes(b"mp4data")

    fake_llm = AsyncMock()  # must not be called

    synthesized = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        synthesized.append(text)
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch("app.gateway.video_gen.engines.vimax.narration_generator._create_silence") as mock_silence:
                mock_silence.side_effect = lambda path, dur: Path(path).write_bytes(b"silence")
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="",
                    llm=fake_llm,
                    voice_id="preset:146",
                    characters=[{"identifier_in_scene": "X", "static_features": "男性"}],
                )

    # TTS must not have been called — the sound-effect shot is silent.
    assert synthesized == [], "sound-effect shots must not be TTS-read; they should be silent"
    assert fake_llm.ainvoke.call_count == 0
    # _create_silence was called once for the sound-effect shot.
    mock_silence.assert_called_once()


@pytest.mark.asyncio
async def test_generate_vimax_narration_no_characters_falls_back_to_llm(tmp_path):
    """Backward compat: characters=None → old LLM-narration path (unchanged)."""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(json.dumps([{"idx": 0, "visual_desc": "wide shot", "audio_desc": '[Speaker] X: "hi"'}]))
    fake_llm = AsyncMock()
    fake_resp = MagicMock()
    fake_resp.content = "旁白"
    fake_llm.ainvoke.return_value = fake_resp

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            await generate_vimax_narration(
                working_dir=working,
                task_dir=tmp_path,
                original_script="",
                llm=fake_llm,
                voice_id="preset:146",
                characters=None,  # old path
            )
    assert fake_llm.ainvoke.call_count == 1


@pytest.mark.asyncio
async def test_generate_vimax_narration_honours_speed_param(tmp_path):
    """2026-07-22: ViMax 旁白语速硬编码 1.2——主链路 payload tts_speed 到不了
    vimax，用户设 1.0 依然 1.2 倍速。speed 参数必须透传到 tts.synthesize。
    用对话模式（characters 提供）避开 LLM 依赖。"""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(json.dumps([{"idx": 0, "visual_desc": "书房内景", "audio_desc": '[Speaker] 小明: "下雨了"'}]))
    speeds: list[float] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        speeds.append(speed)
        Path(output_path).write_bytes(b"fake-audio")

    characters = [{"idx": 0, "identifier_in_scene": "小明", "is_visible": True, "static_features": "年轻男性", "dynamic_features": ""}]
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
        patch(
            "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
            side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
        ),
    ):
        await generate_vimax_narration(
            working_dir=working,
            task_dir=tmp_path,
            original_script="脚本",
            llm=MagicMock(),
            voice_id="preset:146",
            characters=characters,
            speed=1.0,
        )
    assert speeds == [1.0]


# ── Plan A Task 2（2026-10-18）：全片唯一实际发声者继承所选音色 ──────────────


def _write_storyboard_shots(path, shots):
    path.write_text(json.dumps(shots))


@pytest.mark.asyncio
async def test_single_spoken_speaker_all_tts_use_selected_voice(tmp_path):
    """单口播（表里两人、实际只主讲人发声）+ 普通旁白 → 所有 TTS 调用都用所选 332。

    断言落在 tts_mod.synthesize 收到的真实 voice_id 上（非 map 结构）。
    """
    working = tmp_path / "vimax_working"
    working.mkdir()
    _write_storyboard_shots(
        working / "storyboard.json",
        [
            {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "开场", "audio_desc": '[Speaker] 主讲人: "大家好，欢迎来到课程。"'},
            {"idx": 1, "is_last": False, "cam_idx": 0, "visual_desc": "过渡", "audio_desc": "今天我们讲四个象限。"},
            {"idx": 2, "is_last": True, "cam_idx": 0, "visual_desc": "收尾", "audio_desc": '[Speaker] 主讲人: "感谢观看。"'},
        ],
    )
    characters = [
        {"identifier_in_scene": "主讲人", "static_features": "年轻女性，专业讲师"},
        {"identifier_in_scene": "助手", "static_features": "中年男性"},
    ]

    voices: list[str] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        voices.append(voice_id)
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    fake_llm = AsyncMock()
    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch(
                "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
                side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
            ):
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="课程脚本",
                    llm=fake_llm,
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert voices, "至少应有 TTS 调用（2 对白 + 1 旁白）"
    assert set(voices) == {"doubao:zh_male_A"}, f"单口播全片 TTS 应全部使用所选音色（doubao 男声），实际 {voices}"
    assert fake_llm.ainvoke.call_count == 0, "对白/旁白均不需要 LLM"


@pytest.mark.asyncio
async def test_same_single_speaker_across_shots_still_inherits(tmp_path):
    """跨镜重复同一人 = 仍是单口播 → 继承所选音色。"""
    working = tmp_path / "vimax_working"
    working.mkdir()
    _write_storyboard_shots(
        working / "storyboard.json",
        [
            {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "a", "audio_desc": '[Speaker] 主讲人: "第一点。"'},
            {"idx": 1, "is_last": False, "cam_idx": 0, "visual_desc": "b", "audio_desc": '[Speaker] 主讲人: "第二点。"'},
        ],
    )
    characters = [{"identifier_in_scene": "主讲人", "static_features": "年轻女性"}]

    voices: list[str] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        voices.append(voice_id)
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch(
                "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
                side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
            ):
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="课程脚本",
                    llm=AsyncMock(),
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert set(voices) == {"doubao:zh_male_A"}


@pytest.mark.asyncio
async def test_two_speakers_across_scenes_not_single_narrator(tmp_path):
    """跨场景两个 speaker → 不是单口播，角色池分配保持（无人被强制成 332）。"""
    working = tmp_path / "vimax_working"
    for scene_idx, speaker in enumerate(["甲", "乙"]):
        scene_dir = working / f"scene_{scene_idx}"
        scene_dir.mkdir(parents=True)
        _write_storyboard_shots(
            scene_dir / "storyboard.json",
            [{"idx": 0, "is_last": True, "cam_idx": 0, "visual_desc": "s", "audio_desc": f'[Speaker] {speaker}: "台词。"'}],
        )

    characters = [
        {"identifier_in_scene": "甲", "static_features": "女性"},
        {"identifier_in_scene": "乙", "static_features": "男性"},
    ]

    voices: list[str] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        voices.append(voice_id)
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch(
                "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
                side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
            ):
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="课程脚本",
                    llm=AsyncMock(),
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert len(voices) == 2, f"两个 speaker 各一次 TTS，实际 {len(voices)}"
    assert voices[0] != voices[1], "两个 speaker 必须音色互异"
    assert "doubao:zh_male_A" not in voices, "非单口播不得有人继承所选音色（池已剔除 narrator）"


@pytest.mark.asyncio
async def test_characters_one_but_two_actual_speakers_no_inherit(tmp_path):
    """角色表一人但实际两个 speaker → 不触发例外；表内人保持池音色。"""
    working = tmp_path / "vimax_working"
    working.mkdir()
    _write_storyboard_shots(
        working / "storyboard.json",
        [
            {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "a", "audio_desc": '[Speaker] 主讲人: "问。"'},
            {"idx": 1, "is_last": True, "cam_idx": 0, "visual_desc": "b", "audio_desc": '[Speaker] 访客: "答。"'},
        ],
    )
    characters = [{"identifier_in_scene": "主讲人", "static_features": "年轻女性"}]

    voices: dict[str, str] = {}

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        voices[text] = voice_id
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch(
                "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
                side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
            ):
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="课程脚本",
                    llm=AsyncMock(),
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert voices.get("问。") != "doubao:zh_male_A", "表内唯一角色不得继承所选音色（实际有两人发声）"
    # 访客不在表内 → default_voice（= 所选 332）是既有语义，非本批引入
    assert voices.get("答。") == "doubao:zh_male_A"


@pytest.mark.asyncio
async def test_untagged_quoted_narration_not_phantom_speaker(tmp_path):
    """评审修复回归：无标签「X："…"」旁白引用句不得当对白 speaker。

    单口播课程 + 一镜 `俗话说："一寸光阴一寸金"`（无 [Speaker] 标签，主正则
    宽松匹配 speaker="俗话说"）——修前幻影 speaker 把集合撑到 2 人，单口播
    继承静默失效（主讲人落池音色）；修后只统计带对白标签的段，主讲人仍继承 332。
    """
    working = tmp_path / "vimax_working"
    working.mkdir()
    _write_storyboard_shots(
        working / "storyboard.json",
        [
            {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "开场", "audio_desc": '[Speaker] 主讲人: "大家好。"'},
            {"idx": 1, "is_last": False, "cam_idx": 0, "visual_desc": "引言", "audio_desc": '俗话说："一寸光阴一寸金"'},
            {"idx": 2, "is_last": True, "cam_idx": 0, "visual_desc": "收尾", "audio_desc": '[Speaker] 主讲人: "感谢观看。"'},
        ],
    )
    characters = [
        {"identifier_in_scene": "主讲人", "static_features": "年轻女性"},
        {"identifier_in_scene": "助手", "static_features": "中年男性"},
    ]

    voices: dict[str, str] = {}

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        voices[text] = voice_id
        Path(output_path).write_bytes(b"mp3data")
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()):
            with patch(
                "app.gateway.video_gen.engines.vimax.narration_generator._align_chunk_to_shot",
                side_effect=lambda audio_path, duration, task_dir, idx: str(audio_path),
            ):
                await generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="课程脚本",
                    llm=AsyncMock(),
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert voices.get("大家好。") == "doubao:zh_male_A", f"主讲人须继承所选音色（幻影 speaker 不得击穿单口播），实际 {voices}"
    assert voices.get("感谢观看。") == "doubao:zh_male_A"
    # 谚语引用句由 default（=所选音色）念出，同样落在 332（parse 剥掉「俗话说：」与引号）
    assert voices.get("一寸光阴一寸金") == "doubao:zh_male_A"

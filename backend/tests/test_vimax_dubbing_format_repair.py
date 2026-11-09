"""2026-10-14 vimax 配音格式漂移事故修复批（T1/T4/T6 dialogue 层）。

事故：分镜 LLM（qwen3.8-max）输出中文标签格式 ``[说话人] 船夫 （焦急） 快停桨``
（无冒号无引号），解析器只认英文 ``[Speaker]`` + 引号形态 → 标签/角色/情绪
整行被旁白念出并烧字幕；无标签音效描述（"水面滴水声"）也被念出。

plan: docs/plans/2026-10-14-vimax-dubbing-format-repair-plan.md
"""

from app.gateway.video_gen.engines.vimax.dialogue import (
    is_sound_effect,
    parse_dialogue,
    strip_unrecognized_tags,
)

# ── T1: 中文标签格式兼容 ─────────────────────────────────────────────────────


class TestChineseTagFormat:
    """事故片格式：``[说话人] 角色 （情绪） 台词``（无冒号无引号）。"""

    def test_accident_form_no_colon_no_quotes(self):
        """2026-10-14 事故原样格式必须解析为对白（修前两关全漏 → 整行被念出）。"""
        seg = parse_dialogue("[说话人] 船夫 （焦急） 快停桨")
        assert seg is not None
        assert seg.is_dialogue is True, "中文标签无冒号无引号形态必须是对白，不是旁白"
        assert seg.speaker == "船夫"
        assert seg.line == "快停桨"
        assert seg.emotion == "焦急"

    def test_chinese_tag_no_emotion(self):
        seg = parse_dialogue("[说话人] 楚人 我刚才在船帮上刻了记号")
        assert seg is not None
        assert seg.is_dialogue is True
        assert seg.speaker == "楚人"
        assert seg.line == "我刚才在船帮上刻了记号"
        assert seg.emotion == ""

    def test_chinese_tag_with_quotes(self):
        """中文标签 + 冒号 + 引号（T3 prompt 约束的目标格式）也走主正则。"""
        seg = parse_dialogue("[说话人] 船夫：「快停桨，剑掉水里了。」")
        assert seg is not None
        assert seg.is_dialogue is True
        assert seg.speaker == "船夫"
        assert seg.line == "快停桨，剑掉水里了。"

    def test_chinese_tag_colon_fallback(self):
        """中文标签 + 冒号 + 无引号 → colon-split fallback（词表化，不再只认英文标签）。"""
        seg = parse_dialogue("[说话人] 船夫：快停桨")
        assert seg is not None
        assert seg.is_dialogue is True
        assert seg.speaker == "船夫"
        assert seg.line == "快停桨"

    def test_english_tag_emotion_extracted(self):
        """英文标签 + 括号注释 → emotion 提取（原行为只剥不存，T5 需要）。"""
        seg = parse_dialogue('[Speaker] 菲利普（疑惑且防备）: "什么事情这么重要？"')
        assert seg is not None
        assert seg.speaker == "菲利普"
        assert seg.emotion == "疑惑且防备"

    def test_half_width_paren_emotion(self):
        seg = parse_dialogue("[说话人] 船夫 (焦急) 快停桨")
        assert seg is not None
        assert seg.speaker == "船夫"
        assert seg.line == "快停桨"
        assert seg.emotion == "焦急"

    def test_multiple_annotations_takes_last_as_emotion(self):
        """多段括号注释：末段是情绪（先例：``甲（第一幕）（坚定）``——第一幕是幕标）。"""
        seg = parse_dialogue("[说话人] 甲 （第一幕）（坚定）: 「出发。」")
        assert seg is not None
        assert seg.speaker == "甲"
        assert seg.emotion == "坚定"

    def test_existing_english_forms_unchanged(self):
        """既有英文形态行为钉死（回归防护）。"""
        seg = parse_dialogue('[Speaker] 菲利普: "什么事情这么重要？"')
        assert seg is not None
        assert seg.is_dialogue is True
        assert seg.speaker == "菲利普"
        assert seg.line == "什么事情这么重要？"
        assert seg.emotion == ""

    def test_plain_narration_emotion_empty(self):
        seg = parse_dialogue("清晨的阳光唤醒了小明")
        assert seg is not None
        assert seg.is_dialogue is False
        assert seg.emotion == ""

    def test_tagged_unparseable_fallback_strips_tag(self):
        """AI 评审 #1（bug·medium）：``[说话人]船夫快停桨``（无空白无注释无冒号）三级
        解析全失败 → 回落旁白，但**已知标签必须剥掉**——否则 ``[说话人]`` 字面被旁白
        念出并烧字幕（事故形态的变体回归；strip_unrecognized_tags 不剥已知标签）。"""
        seg = parse_dialogue("[说话人]船夫快停桨")
        assert seg is not None
        assert seg.is_dialogue is False
        assert seg.line == "船夫快停桨", "旁白回落时已知标签必须剥离，不进 TTS 文本"

    def test_tagged_unparseable_fallback_strips_tag_english(self):
        """英文全无分隔变体（带空格的 ``[Speaker] Alice speaks`` 会被无冒号正则
        正确解析为对白——真正的漏网形态是 tag 后直接连文）。"""
        seg = parse_dialogue("[Speaker]Alicespeakswithoutseparators")
        assert seg is not None
        assert seg.is_dialogue is False
        assert seg.line == "Alicespeakswithoutseparators"


# ── T4: 无标签音效启发式兜底 ─────────────────────────────────────────────────


class TestSoundEffectHeuristic:
    """决策 5 B 面：无标签文本以 声/音/响 结尾且 ≤20 字且无人称/引号 → 判音效。"""

    def test_accident_water_sounds(self):
        """事故实证用例：两句无标签水声描述必须静音（修前被旁白念出）。"""
        assert is_sound_effect("水面滴水声与轻微波浪声") is True
        assert is_sound_effect("刻痕渗出水珠的细微水声") is True

    def test_bell_sound(self):
        assert is_sound_effect("远处传来钟声") is True

    def test_ends_with_yin(self):
        assert is_sound_effect("波涛翻滚的声音") is True

    def test_long_text_not_sfx(self):
        """超 20 字的长句以声结尾 → 旁白（防误杀正常长旁白）。"""
        long_text = "这是一段很长的旁白句子描述了整个场景的前因后果以及各种各样的细节最后以声结尾"
        assert len(long_text.replace(" ", "")) > 20
        assert is_sound_effect(long_text) is False

    def test_personal_pronoun_not_sfx(self):
        """含人称代词 → 不是音效描述（是旁白句）。"""
        assert is_sound_effect("他说他听见了声音") is False
        assert is_sound_effect("我听到远处传来的声响") is False

    def test_quoted_text_not_sfx(self):
        assert is_sound_effect('她轻声说了"好"的声音') is False

    def test_normal_narration_not_sfx(self):
        assert is_sound_effect("清晨的阳光唤醒了小明") is False
        assert is_sound_effect("船缓缓驶向对岸") is False

    def test_tagged_sfx_still_true(self):
        """带标签的既有路径不变。"""
        assert is_sound_effect("[音效] 小刀刻木声木屑落水声") is True
        assert is_sound_effect("[Sound Effect] birdsong") is True

    def test_whitespace_insensitive(self):
        assert is_sound_effect("  水面滴水声与轻微波浪声  ") is True


# ── T6: 未识别标签剥离（drift 守门的 dialogue 侧原语） ────────────────────────


class TestStripUnrecognizedTags:
    """决策：未识别 ``[...]`` 前缀标签整段剥离后再念，宁剥错不念出。"""

    def test_unknown_tag_stripped(self):
        """新型漂移格式 ``[角色] 小明：…``：标签剥离，剩余文本照念。"""
        cleaned, stripped = strip_unrecognized_tags("[角色] 小明：今天天气真好")
        assert cleaned == "小明：今天天气真好"
        assert stripped == ["[角色]"]

    def test_known_tags_kept(self):
        """认识的对白/音效标签不剥离（下游有自己的解析语义）。"""
        cleaned, stripped = strip_unrecognized_tags("[说话人] 船夫 （焦急） 快停桨")
        assert cleaned == "[说话人] 船夫 （焦急） 快停桨"
        assert stripped == []
        cleaned, stripped = strip_unrecognized_tags("[音效] 流水声")
        assert cleaned == "[音效] 流水声"
        assert stripped == []

    def test_no_tag_unchanged(self):
        cleaned, stripped = strip_unrecognized_tags("普通旁白句子")
        assert cleaned == "普通旁白句子"
        assert stripped == []

    def test_mid_text_brackets_not_stripped(self):
        """只剥**前缀**标签；句中括号（如注释）不动。"""
        cleaned, stripped = strip_unrecognized_tags("旁白句（解释）内容")
        assert cleaned == "旁白句（解释）内容"
        assert stripped == []


# ── T2/T4/T6/T7a/T7c: narration 层 ──────────────────────────────────────────
# harness 形态对齐 test_vimax_narration_generator.py（mock tts_mod.synthesize +
# run_ffmpeg；镜头无 video.mp4 → 时长走 _DEFAULT_SHOT_SECONDS=5.2）。

import json  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from app.gateway.video_gen.engines.vimax.dialogue import SpeakerVoiceMap  # noqa: E402
from app.gateway.video_gen.engines.vimax.narration_generator import (  # noqa: E402
    _enforce_sequential,
    _write_srt,
    generate_vimax_narration,
)
from app.gateway.video_gen.orchestration.tts import TTSResult  # noqa: E402


def _parse_cues(content: str) -> list[tuple[float, float, str]]:
    out = []
    for block in content.strip().split("\n\n"):
        lines = [ln for ln in block.strip().split("\n") if ln]
        if len(lines) < 3:
            continue
        start_s, end_s = lines[1].split(" --> ")
        out.append((_ts(start_s), _ts(end_s), "\n".join(lines[2:])))
    return out


def _ts(ts: str) -> float:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def _write_storyboard(working: Path, shots: list[dict]) -> None:
    scene = working / "scene_0"
    scene.mkdir(parents=True, exist_ok=True)
    (scene / "storyboard.json").write_text(json.dumps(shots), encoding="utf-8")


def _fake_synth(captured: list[dict]):
    """TTS mock：记录调用参数，写假 mp3（ffprobe 失败 → 对齐 no-op，scale=1）。"""

    async def fake(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        captured.append({"text": text, "voice_id": voice_id, "speed": speed, **kwargs})
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    return fake


_VOICE_MAP = SpeakerVoiceMap(by_speaker={"船夫": "preset:146", "楚人": "preset:153"}, narrator_voice="preset:146", default_voice="preset:146")


@pytest.mark.asyncio
async def test_sfx_and_heuristic_sfx_silent_and_not_subtitled(tmp_path):
    """T2/T4：带标签音效 + 无标签启发式音效都静音、不进字幕；启发式命中记 sidecar。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(
        working,
        [
            {"idx": 0, "audio_desc": "[说话人] 船夫 （焦急） 快停桨"},
            {"idx": 1, "audio_desc": "[音效] 小刀刻木声木屑落水声"},
            {"idx": 2, "audio_desc": "水面滴水声与轻微波浪声"},
        ],
    )
    captured: list[dict] = []
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        result = await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    assert result == tmp_path / "vimax_narration.mp3"
    # 只有第 1 镜发声（对白）；两镜音效静音
    spoken = [c["text"] for c in captured]
    assert spoken == ["快停桨"], f"音效段必须静音；实际 TTS 文本：{spoken}"
    # 字幕不含音效描述
    srt = (tmp_path / "subtitle.srt").read_text(encoding="utf-8")
    assert "小刀刻木声" not in srt
    assert "滴水声" not in srt
    assert "快停桨" in srt
    # T4 启发式命中记 sidecar（可审计）——恰好 1 条：无标签水声镜；带 [音效]
    # 标签的镜走既有语义**不**记（防噪声：短标签描述 compact 后常同 hit 启发式）
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    heuristic_records = [rec for rec in deg if rec["kind"] == "sfx_heuristic"]
    assert len(heuristic_records) == 1, f"仅无标签镜记启发式留痕：{deg}"
    assert "滴水声" in heuristic_records[0]["detail"]


@pytest.mark.asyncio
async def test_format_drift_tag_stripped_and_recorded(tmp_path):
    """T6：未识别 [角色] 标签 → 不进 TTS 文本 + KIND_FORMAT_DRIFT sidecar。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "[角色] 小明：今天天气真好"}])
    captured: list[dict] = []
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    assert captured[0]["text"] == "小明：今天天气真好", "drift 标签不进 TTS 文本"
    srt = (tmp_path / "subtitle.srt").read_text(encoding="utf-8")
    assert "[角色]" not in srt
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    drift = [rec for rec in deg if rec["kind"] == "format_drift"]
    assert drift, f"drift 须记 sidecar：{deg}"
    assert "[角色]" in drift[0]["detail"]


@pytest.mark.asyncio
async def test_known_chinese_format_no_drift_record(tmp_path):
    """T6：识别后的 [说话人] 中文格式不触发 drift。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "[说话人] 船夫 （焦急） 快停桨"}])
    captured: list[dict] = []
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    assert not (tmp_path / "degradations.json").exists() or all(rec["kind"] != "format_drift" for rec in json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"])


@pytest.mark.asyncio
async def test_tts_degradation_context_set_for_vimax(tmp_path):
    """T7a：narration 的 TTS 降级从此进 sidecar（修前 vimax 从不设上下文）。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "[说话人] 船夫 （焦急） 快停桨"}])

    from app.gateway.video_gen.orchestration import tts as real_tts_mod

    async def fake_degrading(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        real_tts_mod.record_tts_degradation(requested_voice=voice_id, actual_voice="preset:146", detail="豆包 TTS 瞬时错误，本帧降级")
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_degrading),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    assert any(rec["kind"] == "voice_fallback" for rec in deg), f"vimax 降级须进 sidecar：{deg}"
    # 上下文用后清理，不泄漏到后续调用
    from app.gateway.video_gen.orchestration.tts import _tts_task_dir

    assert _tts_task_dir.get() is None


class TestEnforceSequential:
    """T2：相邻 cue 硬约束 end_i <= start_{i+1}（字幕重影修复）。"""

    def test_overlap_clamped(self):
        from app.gateway.video_gen.subtitle_engine import Cue

        cues = [Cue("甲", 0.0, 2.0), Cue("乙", 1.5, 3.0)]
        out = _enforce_sequential(cues)
        assert out[1].start >= out[0].end, "后 cue 起点必须钳到前 cue 终点"

    def test_too_short_merged_into_previous(self):
        from app.gateway.video_gen.subtitle_engine import Cue

        cues = [Cue("甲", 0.0, 2.0), Cue("乙", 1.9, 2.05)]
        out = _enforce_sequential(cues)
        assert len(out) == 1, "钳不动的极短 cue 并入前 cue"
        assert "乙" in out[0].text

    def test_no_overlap_untouched(self):
        from app.gateway.video_gen.subtitle_engine import Cue

        cues = [Cue("甲", 0.0, 2.0), Cue("乙", 2.0, 4.0)]
        out = _enforce_sequential(cues)
        assert [(c.start, c.end, c.text) for c in out] == [(0.0, 2.0, "甲"), (2.0, 4.0, "乙")]

    def test_too_wide_merge_kept_separate(self):
        """AI 评审二轮 #1（bug·medium）：合并后超两行宽（portrait 13×2=26）时**不并**，
        改为保留独立钳制 cue——并入会产出超宽溢屏字幕。"""
        from app.gateway.video_gen.subtitle_engine import Cue

        wide_a = "十" * 26  # 已满两行
        wide_b = "字" * 13
        cues = [Cue(wide_a, 0.0, 2.0), Cue(wide_b, 1.95, 2.1)]  # 钳后仅 0.15s → 原会并入
        out = _enforce_sequential(cues)
        assert len(out) == 2, "超宽合并须保留独立 cue（钳制），不产出溢屏字幕"
        assert out[1].start >= out[0].end
        assert wide_b in out[1].text, "不丢字"

    def test_narrow_merge_still_works(self):
        from app.gateway.video_gen.subtitle_engine import Cue

        cues = [Cue("甲乙", 0.0, 2.0), Cue("丙", 1.95, 2.1)]
        out = _enforce_sequential(cues)
        assert len(out) == 1, "窄文本合并行为不变（不丢字）"
        assert "丙" in out[0].text

    def test_clamped_cue_never_inverted(self):
        """AI 评审七轮（bug·high）：超宽不并的钳制分支 start=prev.end 但 end 用钳前
        start 算——前 cue 时间窗完全吞没当前 cue 时产出 start > end 倒挂 cue。"""
        from app.gateway.video_gen.subtitle_engine import Cue

        wide_a = "十" * 26
        wide_b = "字" * 13
        # prev 完全吞没 next（next 1.0-1.5 在 prev 0-2.0 内，remaining<0 且超宽不并）
        cues = [Cue(wide_a, 0.0, 2.0), Cue(wide_b, 1.0, 1.5)]
        out = _enforce_sequential(cues)
        assert len(out) == 2
        for c in out:
            assert c.end > c.start, f"倒挂 cue：start={c.start} end={c.end}"
        assert out[1].start >= out[0].end

    def test_merged_text_reflowed_within_line_width(self):
        """AI 评审五轮（bug·medium）：合并发生在折行之后——总量 ≤ 两行宽不保证逐行
        ≤ 单行宽（6\\N6 + 10 字 → 第二行 16 溢屏）。合并产物须重新折行。"""
        from app.gateway.video_gen.subtitle_engine import Cue, _text_display_width

        cues = [Cue("六六六六六六\\N六六六六六六", 0.0, 2.0), Cue("字" * 10, 1.95, 2.1)]
        out = _enforce_sequential(cues)
        assert len(out) == 1, "总量 22 ≤ 26 应合并"
        for line in out[0].text.split("\\N"):
            assert _text_display_width(line) <= 13.0, f"折行后单行超宽：{line!r}"
        assert out[0].text.replace("\\N", "") == "六六六六六六六六六六六六" + "字" * 10, "不丢字"


def test_write_srt_no_overlap_output(tmp_path, caplog):
    """T2 端到端：产出 SRT 的相邻 cue 不得重叠。"""
    long_line = "这是一句特别长的台词需要切成多条字幕显示所以这里写满超过十三宽" * 2
    entries = [(long_line, 0.0, 5.2), ("第二句", 5.2, 10.4)]
    _write_srt(entries, tmp_path / "subtitle.srt")
    cues = _parse_cues((tmp_path / "subtitle.srt").read_text(encoding="utf-8"))
    assert len(cues) >= 3
    for prev, nxt in zip(cues, cues[1:]):
        assert nxt[0] >= prev[1] - 0.001, f"cue 重叠：{prev} vs {nxt}"


@pytest.mark.asyncio
async def test_doubao_word_cues_drive_subtitle_timing(tmp_path):
    """T7c：doubao chunk 用字级时间戳做 cue（不再宽度估算）；与估算 cue 混排不重叠。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(
        working,
        [
            {"idx": 0, "audio_desc": "[说话人] 船夫 （焦急） 快停桨"},
            {"idx": 1, "audio_desc": "旁白一句由豆包音色念出"},  # narration → narrator_voice=doubao
        ],
    )
    vmap = SpeakerVoiceMap(by_speaker={"船夫": "preset:146"}, narrator_voice="doubao:zh_female_vv_uranus_bigtts", default_voice="preset:146")

    async def fake(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        if voice_id.startswith("doubao:"):
            return TTSResult(audio_path=output_path, srt="1\n00:00:00,200 --> 00:00:01,100\nx\n", duration=1.1, word_cues=[("旁", 0.2, 0.5), ("白", 0.5, 0.8), ("一", 0.8, 1.0), ("句", 1.0, 1.1)])
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=vmap),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="doubao:zh_female_vv_uranus_bigtts", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    cues = _parse_cues((tmp_path / "subtitle.srt").read_text(encoding="utf-8"))
    assert len(cues) == 2
    # 第 1 cue（第 2 镜 doubao 旁白）= 字级时间（chunk 内 0.2-1.1s + 镜起点 5.2）
    doubao_cue = next(c for c in cues if "旁白" in c[2])
    assert abs(doubao_cue[0] - (5.2 + 0.2)) < 0.05 and abs(doubao_cue[1] - (5.2 + 1.1)) < 0.05, f"字级时间戳须直接驱动 cue：{doubao_cue}"
    # 估算 cue 不与字级 cue 重叠
    for prev, nxt in zip(cues, cues[1:]):
        assert nxt[0] >= prev[1] - 0.001, f"cue 重叠：{prev} vs {nxt}"


@pytest.mark.asyncio
async def test_mixed_three_voices_cues_no_overlap(tmp_path):
    """T7 混合音色：doubao/360/edge 各一角色 → 字级 cue 与估算 cue 混排正确不重叠。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(
        working,
        [
            {"idx": 0, "audio_desc": "[说话人] 甲 （开心） 你好呀"},  # doubao（带情绪）
            {"idx": 1, "audio_desc": "[说话人] 乙 （平静） 大家好"},  # preset 估算
            {"idx": 2, "audio_desc": "[说话人] 丙 很高兴认识你"},  # edge 估算
        ],
    )
    vmap = SpeakerVoiceMap(by_speaker={"甲": "doubao:zh_female_vv_uranus_bigtts", "乙": "preset:146", "丙": "zh-CN-XiaoxiaoNeural"}, narrator_voice="preset:146", default_voice="preset:146")
    captured_emotion: list[dict] = []

    async def fake(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        captured_emotion.append({"voice": voice_id, "kwargs": kwargs})
        if voice_id.startswith("doubao:"):
            return TTSResult(audio_path=output_path, srt="", duration=1.4, word_cues=[("你", 0.3, 0.7), ("好", 0.7, 1.1), ("呀", 1.1, 1.4)])
        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=vmap),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(
            working_dir=working,
            task_dir=tmp_path,
            original_script="故事",
            llm=MagicMock(),
            voice_id="zh-CN-YunjianNeural",
            characters=[{"identifier_in_scene": "甲", "static_features": "女"}, {"identifier_in_scene": "乙", "static_features": "男"}, {"identifier_in_scene": "丙", "static_features": "女"}],
        )
    cues = _parse_cues((tmp_path / "subtitle.srt").read_text(encoding="utf-8"))
    assert len(cues) == 3
    for prev, nxt in zip(cues, cues[1:]):
        assert nxt[0] >= prev[1] - 0.001, f"混合 cue 重叠：{prev} vs {nxt}"
    # doubao 首镜 cue 用字级时间（0.3-1.4），后两镜 5.2 栅格估算
    assert abs(cues[0][0] - 0.3) < 0.05
    assert cues[1][0] >= 5.2 - 0.01
    # T5：doubao 音色 + 已映射情绪（开心）→ emotion 下发；preset 不带
    doubao_call = next(c for c in captured_emotion if c["voice"].startswith("doubao:"))
    assert doubao_call["kwargs"].get("emotion") == "开心"
    preset_call = next(c for c in captured_emotion if c["voice"].startswith("preset:"))
    assert not preset_call["kwargs"].get("emotion")


@pytest.mark.asyncio
async def test_alignment_failure_keeps_cue_scale_one(tmp_path, monkeypatch):
    """AI 评审（bug·low）：对齐失败（沿用原速 chunk，aligned==原路径）时字级时间
    **不得**按 atempo 预除——否则该镜字幕相对音频整体前移。"""
    from app.gateway.video_gen.engines.vimax import narration_generator as ng

    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "旁白由豆包念出且过长需要对齐"}])
    vmap = SpeakerVoiceMap(by_speaker={}, narrator_voice="doubao:zh_female_vv_uranus_bigtts", default_voice="preset:146")

    async def fake(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        return TTSResult(
            audio_path=output_path,
            srt="",
            duration=8.0,  # 远超 5.2 镜长 → 理应 atempo≈1.54
            word_cues=[("旁", 0.0, 1.0), ("白", 1.0, 2.0)],
        )

    # 对齐失败：返回原路径（真实失败路径沿原速 chunk）
    monkeypatch.setattr(ng, "_align_chunk_to_shot", lambda p, dur, td, ci: str(p))

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=vmap),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="doubao:zh_female_vv_uranus_bigtts")
    cues = _parse_cues((tmp_path / "subtitle.srt").read_text(encoding="utf-8"))
    assert cues, "应有字幕"
    # 对齐失败 → cue_scale=1：词时间 0-2s 原样映射到镜起点（不按 1.54 压缩）
    first = cues[0]
    assert abs(first[0] - 0.0) < 0.05 and abs(first[1] - 2.0) < 0.05, f"对齐失败时字级时间不得预除：{first}"


@pytest.mark.asyncio
async def test_cue_scale_derived_from_alignment_durations(tmp_path, monkeypatch):
    """AI 评审七轮（bug·high）：cue_scale 判定源与对齐函数分叉（result.duration vs
    实测 ffprobe）→ 阈值两侧判定不一致时字级时间戳失同步。修法 = scale 由**同一
    探针**的对齐前后时长比反推，不再依赖 result.duration。"""
    from app.gateway.video_gen.engines.vimax import narration_generator as ng

    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "旁白由豆包念出且需要对齐"}])
    vmap = SpeakerVoiceMap(by_speaker={}, narrator_voice="doubao:zh_female_vv_uranus_bigtts", default_voice="preset:146")

    async def fake(text, *, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"mp3data")
        # duration 与实测不一致的场景：TTS 报 3.0，实测 8.0，镜长 5.2 → 对齐函数
        # （按实测判定）会 atempo，而 result.duration 口径会误判无需换算
        return TTSResult(
            audio_path=output_path,
            srt="",
            duration=3.0,
            word_cues=[("旁", 0.0, 1.0), ("白", 1.0, 2.0)],
        )

    def fake_align(p, dur, td, ci):
        aligned = Path(str(p) + ".aligned.mp3")
        aligned.write_bytes(b"mp3data")
        Path(p).unlink()  # 十轮（bug·high）：模拟真实成功路径——原 chunk 被删除
        return str(aligned)

    def fake_probe(p):
        # 十轮（bug·high）：真实探针对已删除文件返回 0.0——若实现在对齐后探测，
        # 恒得 0 → scale 恒 1 → 本测试的 atempo 断言失败
        return 8.0 if Path(p).exists() else 0.0

    monkeypatch.setattr(ng, "_align_chunk_to_shot", fake_align)
    monkeypatch.setattr(ng, "_probe_audio_duration", fake_probe)

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=vmap),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="doubao:zh_female_vv_uranus_bigtts")
    cues = _parse_cues((tmp_path / "subtitle.srt").read_text(encoding="utf-8"))
    assert cues, "应有字幕"
    # 九轮（bug·medium）：ratio 8.0/5.2≈1.54 超 cap → 实际加速只有 1.3（封顶+截断），
    # scale 须取 min(ratio, cap)=1.3 而非时长比 1.54；词 0-2s → cue 0-1.538s
    first = cues[0]
    assert abs(first[1] - 2.0 / 1.3) < 0.05, f"封顶截断分支 scale 须取 cap：{first}"


@pytest.mark.asyncio
async def test_legacy_narration_branch_also_strips_drift_tags(tmp_path):
    """AI 评审八轮（bug·medium）：legacy LLM-narration 分支的旁白文本同样不得念出
    漂移标签——剥离须在两分支汇合处统一做（修前只覆盖 dialogue 模式）。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "visual_desc": "画面描述", "audio_desc": "[角色] 小明：今天天气真好"}])
    captured: list[dict] = []

    fake_llm = AsyncMock()

    async def llm_ainvoke(messages):
        resp = MagicMock()
        resp.content = "[旁白标记] 黄昏的光线洒满房间"  # LLM 改写产物也可能带漂移标签
        return resp

    fake_llm.ainvoke = llm_ainvoke

    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=fake_llm, voice_id="preset:146", characters=None)
    assert captured, "legacy 分支应正常发声"
    assert all("[旁白标记]" not in c["text"] for c in captured), f"legacy 旁白文本须剥漂移标签：{[c['text'] for c in captured]}"
    deg_path = tmp_path / "degradations.json"
    assert deg_path.exists(), "drift 须记 sidecar（legacy 分支同样）"
    deg = json.loads(deg_path.read_text(encoding="utf-8"))["items"]
    assert any(rec["kind"] == "format_drift" for rec in deg), f"drift sidecar 缺失：{deg}"


# ── T5 映射表 / T7b 语速透传 ────────────────────────────────────────────────


class TestResolveDoubaoEmotion:
    """T5：情绪注释 → 探针验证过的 emotion 枚举；未映射不下发。"""

    def test_mapped_keywords(self):
        from app.gateway.video_gen.orchestration.tts import resolve_doubao_emotion

        assert resolve_doubao_emotion("焦急") == "焦急"
        assert resolve_doubao_emotion("疑惑且防备，内心焦急") == "焦急"  # 子串命中
        assert resolve_doubao_emotion("喜悦") == "开心"
        assert resolve_doubao_emotion("悲伤") == "sad"
        assert resolve_doubao_emotion("愤怒") == "angry"
        assert resolve_doubao_emotion("笃定") == "平静"
        assert resolve_doubao_emotion("特别开心") == "开心"  # 九轮：程度副词「特别」非否定
        assert resolve_doubao_emotion("特别焦急") == "焦急"

    def test_compound_emotion_returns_none(self):
        """九轮（low）：复合情绪命中 ≥2 个不同枚举 → 宁不发不错发。"""
        from app.gateway.video_gen.orchestration.tts import resolve_doubao_emotion

        assert resolve_doubao_emotion("又焦急又开心") is None
        assert resolve_doubao_emotion("开心却焦急") is None
        assert resolve_doubao_emotion("悲伤又愤怒") is None

    def test_unmapped_returns_none(self):
        from app.gateway.video_gen.orchestration.tts import resolve_doubao_emotion

        assert resolve_doubao_emotion("疑惑且防备") is None
        assert resolve_doubao_emotion("天真") is None
        assert resolve_doubao_emotion("") is None

    def test_negated_emotion_returns_none(self):
        """AI 评审二轮 #2（bug·low）：否定前缀（不开心/别难过/毫无悲伤）会反向命中
        积极情绪词——语义颠倒，必须不下发。三轮修正：只查关键词前缀邻域。"""
        from app.gateway.video_gen.orchestration.tts import resolve_doubao_emotion

        assert resolve_doubao_emotion("不开心") is None
        assert resolve_doubao_emotion("不高兴") is None
        assert resolve_doubao_emotion("别难过") is None
        assert resolve_doubao_emotion("毫无悲伤") is None
        assert resolve_doubao_emotion("不要那么开心") is None  # 六轮：插入式否定
        assert resolve_doubao_emotion("一点也不高兴") is None  # 六轮：插入式否定
        assert resolve_doubao_emotion("开心") == "开心"  # 无否定词不受影响
        assert resolve_doubao_emotion("他说不，要保持平静") == "平静"  # 远邻否定不误杀

    def test_non_negating_usage_not_blocked(self):
        """AI 评审三轮（bug·medium）：含「不」但非否定搭配的合法情绪词不得被全串
        扫描误杀——焦急不安/兴奋不已/悲不自胜里的「不」是词的一部分。"""
        from app.gateway.video_gen.orchestration.tts import resolve_doubao_emotion

        assert resolve_doubao_emotion("焦急不安") == "焦急"
        assert resolve_doubao_emotion("兴奋不已") == "开心"
        # 气不打一处来：表内无关键词命中，本就不映射（与否定过滤无关）
        assert resolve_doubao_emotion("气不打一处来") is None

    def test_doubao_payload_carries_emotion(self):
        """T5：emotion 进 audio_params（payload 形态钉死）。"""
        from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

        client = DoubaoTTSClient(access_key="k")
        payload = client._build_payload("zh_female_vv_uranus_bigtts", "文本", 1.0, enable_subtitle=True, emotion="焦急")
        assert payload["req_params"]["audio_params"]["emotion"] == "焦急"
        assert payload["req_params"]["audio_params"]["enable_subtitle"] is True
        # None 不下发（回落现状）
        payload_none = client._build_payload("zh_female_vv_uranus_bigtts", "文本", 1.0)
        assert "emotion" not in payload_none["req_params"]["audio_params"]


@pytest.mark.asyncio
async def test_engine_ctx_tts_speed_wins_over_payload(tmp_path):
    """T7b：项目级语速（ctx.tts_speed 三级解析结果）必须生效——修前 engine 只读
    原始 payload tts_speed，项目级设置在 vimax 管线静默失效。"""
    from unittest.mock import MagicMock, patch  # noqa: F811

    from app.gateway.video_gen.engines.base import EngineContext
    from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

    class _FakePipeline:
        async def __call__(self, **kwargs):
            (tmp_path / "vimax_working" / "final_video.mp4").write_bytes(b"x")

    engine = VimaxEngine(
        config_factory=lambda wd, req: {"pipeline": "script2video"},
        pipeline_factory=lambda path: _FakePipeline(),
        overlay_fn=None,
        narration_llm_factory=lambda: MagicMock(),
    )
    # payload 无 tts_speed（项目级场景）；ctx 带三级解析结果 0.8
    ctx = EngineContext(
        user_id="u",
        task_id="t",
        task_dir=tmp_path,
        voice_id="zh-CN-YunjianNeural",
        style="Cartoon",
        request={"script": "x"},
        tts_speed=0.8,
    )
    with patch(
        "app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration",
        new=AsyncMock(return_value=tmp_path / "narration.mp3"),
    ) as mock_gen:
        await engine.render(ctx)
    assert mock_gen.call_args.kwargs["speed"] == 0.8

    # 兼容路径：旧调用方/测试无 ctx.tts_speed → 回落 payload 再落 1.2
    ctx2 = EngineContext(user_id="u", task_id="t", task_dir=tmp_path, voice_id="zh-CN-YunjianNeural", request={"script": "x"})
    (tmp_path / "vimax_working").mkdir(exist_ok=True)
    with patch(
        "app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration",
        new=AsyncMock(return_value=tmp_path / "narration.mp3"),
    ) as mock_gen2:
        await engine.render(ctx2)
    assert mock_gen2.call_args.kwargs["speed"] == 1.2


# ── T6（2026-10-16 收口批）：narration 三项 low ──────────────────────────────
# ①sidecar 镜号口径统一为全局 chunk_idx（== vimax_shots manifest 全局 gid，
#   前端按 frame_index 定位不再错镜）；②no-colon 形态多段括号注释全消费；
# ③已知标签 fallthrough 剥标签记 KIND_FORMAT_DRIFT sidecar（修前留痕断链）。


def test_no_colon_form_multiple_annotations_all_consumed():
    """``[说话人] 船夫 （焦急）（坚定） 快停桨``：第二段注释不漏进台词。"""
    seg = parse_dialogue("[说话人] 船夫 （焦急）（坚定） 快停桨")
    assert seg is not None and seg.is_dialogue is True
    assert seg.speaker == "船夫"
    assert seg.line == "快停桨", f"多段注释须全部消费，实际 line={seg.line!r}"
    assert seg.emotion == "坚定", "情绪取末段注释"

    seg2 = parse_dialogue("[说话人] 船夫（第一幕）（焦急） 快停桨")
    assert seg2.line == "快停桨" and seg2.emotion == "焦急"


def test_fallthrough_tag_strip_flags_segment():
    """``[说话人]船夫快停桨``（无分隔符）fallthrough 剥标签 → segment 带
    tag_fellthrough 标记（narration 层据此记 sidecar）。"""
    seg = parse_dialogue("[说话人]船夫快停桨")
    assert seg is not None and seg.is_dialogue is False
    assert seg.line == "船夫快停桨", "标签已剥，不念出"
    assert getattr(seg, "tag_fellthrough", False) is True, "须携带 fallthrough 标记"


def test_normal_shapes_not_flagged():
    """正常解析形态不得带 tag_fellthrough（防误报刷 sidecar）。"""
    assert getattr(parse_dialogue("[说话人] 船夫：快停桨"), "tag_fellthrough", False) is False
    assert getattr(parse_dialogue("[说话人] 船夫 （焦急） 快停桨"), "tag_fellthrough", False) is False
    assert getattr(parse_dialogue("普通旁白"), "tag_fellthrough", False) is False
    assert getattr(parse_dialogue("[音效] 水声"), "tag_fellthrough", False) is False


@pytest.mark.asyncio
async def test_fallthrough_strip_records_format_drift_sidecar(tmp_path):
    """fallthrough 剥标签（修前只 logger.warning，sidecar 记录断链）→ 记
    KIND_FORMAT_DRIFT（含样本，可审计）。"""
    working = tmp_path / "vimax_working"
    _write_storyboard(working, [{"idx": 0, "audio_desc": "[说话人]船夫快停桨"}])
    captured: list[dict] = []
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "船夫", "static_features": "男性"}])
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    drift = [rec for rec in deg if rec["kind"] == "format_drift"]
    assert len(drift) == 1, f"fallthrough 剥标签须记 format_drift，实际 {deg}"
    assert "船夫快停桨" in drift[0]["detail"], "样本须含原始形态"
    spoken = [c["text"] for c in captured]
    assert spoken == ["船夫快停桨"], "剥标签后正文照念"


@pytest.mark.asyncio
async def test_sidecar_shot_numbering_uses_global_chunk_index(tmp_path):
    """多场景：sidecar detail 镜号 = 全局 chunk_idx+1（== manifest 全局 gid），
    与 frame_index 同口径——修前 detail 用 scene 局部 idx，前端按 frame_index
    定位错镜。"""
    working = tmp_path / "vimax_working"
    for scene_name, shots in (
        ("scene_0", [{"idx": 0, "audio_desc": "[说话人] 甲：你好"}, {"idx": 1, "audio_desc": "[说话人] 甲：再见"}]),
        ("scene_1", [{"idx": 0, "audio_desc": "水面滴水声与轻微波浪声"}, {"idx": 1, "audio_desc": "[说话人] 乙：好"}]),
    ):
        scene = working / scene_name
        scene.mkdir(parents=True, exist_ok=True)
        (scene / "storyboard.json").write_text(json.dumps(shots), encoding="utf-8")
    captured: list[dict] = []
    with (
        patch("app.gateway.video_gen.engines.vimax.narration_generator.build_speaker_voice_map", return_value=_VOICE_MAP),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=_fake_synth(captured)),
        patch("app.gateway.video_gen.engines.vimax.narration_generator.run_ffmpeg", new=AsyncMock()),
    ):
        await generate_vimax_narration(working_dir=working, task_dir=tmp_path, original_script="故事", llm=MagicMock(), voice_id="preset:146", characters=[{"identifier_in_scene": "甲", "static_features": "男性"}])
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    heuristic = [rec for rec in deg if rec["kind"] == "sfx_heuristic"]
    assert len(heuristic) == 1
    rec = heuristic[0]
    assert rec["frame_index"] == 2, "全局 chunk_idx（scene_0 两镜后）"
    assert "第 3 镜" in rec["detail"], f"detail 镜号须与 frame_index 同口径（全局），实际 {rec['detail']!r}"
    assert "第 1 镜" not in rec["detail"], "不得用 scene 局部镜号"

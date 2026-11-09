"""Tests for ViMax dialogue parsing + per-speaker voice assignment (P1/P2).

Parses the audio_desc field ViMax's screenwriter emits — format
``[Speaker] 菲利普: "什么事情..."`` (dialogue) or ``[Sound Effect] ...``
(non-dialogue) — into structured (speaker, line) segments, and assigns
per-speaker voice ids so a two-person dialogue video gets two voices
instead of one narration voice reading the whole script.
"""

import logging

from app.gateway.video_gen.engines.vimax.dialogue import (
    build_speaker_voice_map,
    parse_dialogue,
)


def test_parse_dialogue_speaker_line():
    """A [Speaker] line parses to (speaker, line) with is_dialogue=True."""
    seg = parse_dialogue('[Speaker] 菲利普: "什么事情这么重要，需要开正式会议？"')
    assert seg is not None
    assert seg.is_dialogue is True
    assert seg.speaker == "菲利普"
    assert seg.line == "什么事情这么重要，需要开正式会议？"


def test_parse_dialogue_sound_effect_is_not_dialogue():
    """A [Sound Effect] line is non-dialogue; line keeps the full text."""
    seg = parse_dialogue("[Sound Effect] Soft footsteps and chairs scraping.")
    assert seg is not None
    assert seg.is_dialogue is False
    assert seg.speaker == ""
    assert "Soft footsteps" in seg.line


def test_parse_dialogue_chinese_sound_effect_tag():
    """[音效] (Chinese tag) is also non-dialogue."""
    seg = parse_dialogue("[音效] 微弱的鸟鸣声")
    assert seg is not None
    assert seg.is_dialogue is False
    assert seg.line == "[音效] 微弱的鸟鸣声"


def test_parse_dialogue_empty_returns_none():
    """Empty/None audio_desc → None (caller skips TTS for this shot)."""
    assert parse_dialogue("") is None
    assert parse_dialogue(None) is None


def test_parse_dialogue_strips_speaker_whitespace():
    """Speaker name with surrounding spaces is trimmed."""
    seg = parse_dialogue('[Speaker]  部门主管 : "我找你来是想谈谈。"')
    assert seg is not None
    assert seg.speaker == "部门主管"
    assert seg.line == "我找你来是想谈谈。"


def test_parse_dialogue_plain_text_no_marker():
    """Text with no [Speaker]/[Sound Effect] marker is treated as narration
    (is_dialogue=False, line=text). This is the idea2video/legacy fallback."""
    seg = parse_dialogue("清晨的阳光唤醒了小明")
    assert seg is not None
    assert seg.is_dialogue is False
    assert seg.line == "清晨的阳光唤醒了小明"


def test_parse_dialogue_full_width_colon():
    """A [Speaker] line with a full-width colon （：） also parses. ViMax's LLM
    may emit either half-width ':' or full-width '：' — both must work, else a
    dialogue line silently falls back to narration."""
    seg = parse_dialogue('[Speaker] 菲利普："什么事情这么重要？"')
    assert seg is not None
    assert seg.is_dialogue is True
    assert seg.speaker == "菲利普"
    assert seg.line == "什么事情这么重要？"


def test_parse_dialogue_multi_sentence_in_quotes():
    """A dialogue line with multiple sentences inside one quote pair parses as
    a single line (the regex's non-greedy capture + $ anchor backtracks to the
    final closing quote). Pins this so a future regex tweak can't split it."""
    seg = parse_dialogue('[Speaker] 菲利普: "第一句。第二句。第三句。"')
    assert seg is not None
    assert seg.is_dialogue is True
    assert seg.line == "第一句。第二句。第三句。"


def test_parse_dialogue_nested_quotes_preserved():
    """Nested escaped quotes inside the dialogue line are preserved in `line`.
    The regex backtracks to the outermost closing quote at end-of-string."""
    seg = parse_dialogue('[Speaker] X: "她说"你好"然后离开"')
    assert seg is not None
    assert seg.is_dialogue is True
    assert seg.speaker == "X"
    assert '她说"你好"然后离开' in seg.line


def test_parse_dialogue_speaker_no_quotes_falls_back(caplog):
    """A [Speaker]-tagged line with NO quote chars (regex misses it) must STILL
    parse as dialogue via the two-stage fallback (split on first colon), not
    silently become narration. A warning is logged so ViMax LLM format drift
    is visible."""
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.engines.vimax.dialogue"):
        seg = parse_dialogue("[Speaker] 菲利普: 台词没有引号")
    assert seg is not None
    assert seg.is_dialogue is True, "[Speaker]-tagged line must be dialogue even without quotes"
    assert seg.speaker == "菲利普"
    assert seg.line == "台词没有引号"
    assert "fell back" in caplog.text, "fallback must log a warning for visibility"


def test_parse_dialogue_strips_parenthetical_emotion_from_speaker():
    """ViMax screenwriter annotates speaker names with emotion in full-width
    parens: '[Speaker] 菲利普（疑惑且防备）: "..."'. characters.json has the
    clean name '菲利普'. parse_dialogue must strip the annotation so voice_map
    lookup hits. Regression: vg_cda1b579a220 every line fell back to 146."""
    seg = parse_dialogue('[Speaker] 菲利普（疑惑且防备）: "什么事情这么重要？"')
    assert seg is not None
    assert seg.is_dialogue is True
    assert seg.speaker == "菲利普", f"must strip full-width paren annotation; got {seg.speaker!r}"
    assert seg.line == "什么事情这么重要？"


def test_parse_dialogue_strips_half_width_paren_from_speaker():
    """Half-width parens also stripped (English/idea2video path)."""
    seg = parse_dialogue('[Speaker] Alice (whispering): "hello"')
    assert seg is not None
    assert seg.speaker == "Alice"
    assert seg.line == "hello"


def test_parse_dialogue_speaker_without_parens_unchanged():
    """Clean speaker name (no parens) passes through unchanged."""
    seg = parse_dialogue('[Speaker] 菲利普: "你好。"')
    assert seg is not None
    assert seg.speaker == "菲利普"


def test_strip_speaker_annotations_direct():
    """Direct unit test for the helper."""
    from app.gateway.video_gen.engines.vimax.dialogue import _strip_speaker_annotations

    assert _strip_speaker_annotations("菲利普（疑惑且防备）") == "菲利普"
    assert _strip_speaker_annotations("Alice (whispering)") == "Alice"
    assert _strip_speaker_annotations("菲利普") == "菲利普"
    assert _strip_speaker_annotations("  菲利普  ") == "菲利普"
    assert _strip_speaker_annotations("甲（第一幕）（坚定）") == "甲"


# ── P2: build_speaker_voice_map tests ──────────────────────────────────────


def _char(name: str, features: str) -> dict:
    """Build a characters.json-style entry."""
    return {"identifier_in_scene": name, "static_features": features}


def test_build_speaker_voice_map_assigns_by_gender():
    """Male character → a male preset voice; female → a female preset voice.
    Two characters must get two DIFFERENT voices."""
    chars = [
        _char("部门主管", "中年男性，身材匀称"),  # male
        _char("玛丽", "年轻女性，长发"),  # female
    ]
    m = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:146")
    mgr_voice = m.voice_for("部门主管")
    mary_voice = m.voice_for("玛丽")
    assert mgr_voice != mary_voice, "two speakers must get distinct voices"
    # male→male preset, female→female preset (assert via CURATED_VOICES lookup)
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    def gender_of(vid):
        return next(v["gender"] for v in CURATED_VOICES if v["id"] == vid)

    assert gender_of(mgr_voice) == "Male"
    assert gender_of(mary_voice) == "Female"


def test_build_speaker_voice_map_unknown_speaker_falls_back():
    """A speaker not in the characters list gets the default voice."""
    m = build_speaker_voice_map([_char("部门主管", "男性")], default_voice="preset:146", narrator_voice="preset:146")
    assert m.voice_for("陌生人") == "preset:146"


def test_build_speaker_voice_map_narrator_voice():
    """Non-dialogue segments use the narrator voice."""
    m = build_speaker_voice_map([_char("部门主管", "男性")], default_voice="preset:146", narrator_voice="preset:153")
    assert m.voice_for("") == "preset:153"  # narrator
    assert m.narrator_voice == "preset:153"


def test_build_speaker_voice_map_three_speakers_all_distinct():
    """2 male + 1 female: the two males must still get distinct voices
    (rotate through available male presets, not collapse to one)."""
    chars = [
        _char("甲", "中年男性"),
        _char("乙", "年轻男性"),
        _char("丙", "女性"),
    ]
    m = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:146")
    voices = {m.voice_for("甲"), m.voice_for("乙"), m.voice_for("丙")}
    assert len(voices) == 3, f"three speakers must get 3 distinct voices, got {voices}"


def test_build_speaker_voice_map_empty_characters():
    """No characters → only default/narrator available, never raises."""
    m = build_speaker_voice_map([], default_voice="preset:146", narrator_voice="preset:146")
    assert m.voice_for("任何人") == "preset:146"
    assert m.by_speaker == {}


def test_build_speaker_voice_map_unknown_gender_gets_distinct_voice():
    """A character whose gender can't be inferred from static_features must
    STILL get a distinct voice (not default_voice, which would collide with
    other unknowns + the narrator). It rotates through the combined overflow
    pool so two unknown-gender speakers get different voices."""
    chars = [
        _char("甲", "一个穿着风衣的人"),  # no 男/女 marker
        _char("乙", "戴帽子的角色"),  # no 男/女 marker
    ]
    m = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:146")
    v1 = m.voice_for("甲")
    v2 = m.voice_for("乙")
    assert v1 != v2, "two unknown-gender speakers must still get distinct voices"
    assert v1 != "preset:146", "unknown-gender speaker must not collapse to default"
    assert v2 != "preset:146"


def test_build_speaker_voice_map_english_gender_keywords():
    """English gender words (Male/Female/man/woman) are recognized, not just
    Chinese 男/女. idea2video may produce English static_features."""
    m = build_speaker_voice_map(
        [_char("Bob", "a young man with short hair"), _char("Alice", "a woman with long hair")],
        default_voice="preset:146",
        narrator_voice="preset:146",
    )
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    def gender_of(vid):
        return next(v["gender"] for v in CURATED_VOICES if v["id"] == vid)

    assert gender_of(m.voice_for("Bob")) == "Male"
    assert gender_of(m.voice_for("Alice")) == "Female"


def test_build_speaker_voice_map_pool_is_zh_cn_only():
    """The voice pool must NOT contain non-Chinese voices (en-US/ja-JP/ko-KR/
    fr-FR/de-DE/es-ES). Without a locale filter, a 10th same-gender speaker
    would get a foreign-language voice reading Chinese text → garbage."""
    m = build_speaker_voice_map(
        [_char(f"角色{i}", "男性") for i in range(12)],  # 12 males > zh-CN pool (9)
        default_voice="preset:146",
        narrator_voice="preset:146",
    )
    assigned = {m.voice_for(f"角色{i}") for i in range(12)}
    # zh-CN voice_id prefixes: edge-tts "zh-CN-", 360 "preset:", doubao "doubao:".
    # default_voice is "preset:146" (overflow fallback). Any voice_id NOT matching
    # those patterns is a foreign locale leak.
    non_zh = [v for v in assigned if not v.startswith("preset:") and not v.startswith("zh-CN") and not v.startswith("doubao:")]
    assert not non_zh, f"non-Chinese voices leaked into pool: {non_zh}"


def test_build_speaker_voice_map_assigns_doubao_first_with_key(monkeypatch):
    """Plan B 新契约（2026-10-19）：有 VOLCENGINE_VOICE_ACCESS_KEY 时池 = doubao 优先
    → Edge 溢出，**不含 360 preset**；前两位 speaker 不得直接落 edge（bing 被墙即
    静音——2026-07-15 silent dialogue lines 回归的排序教训保留）。"""
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    def is_edge(vid):
        return next((v["provider"] == "edge-tts" for v in CURATED_VOICES if v["id"] == vid), False)

    m = build_speaker_voice_map(
        [_char("甲", "中年男性"), _char("乙", "年轻女性")],
        default_voice="zh-CN-YunjianNeural",
        narrator_voice="zh-CN-YunjianNeural",
    )
    assert not any(v.startswith("preset:") for v in m.by_speaker.values()), f"ViMax 池不得分配 preset：{m.by_speaker}"
    assert not is_edge(m.voice_for("甲")), f"有字节 key 时 first male 应拿 doubao，实际 {m.voice_for('甲')!r}"
    assert not is_edge(m.voice_for("乙")), f"有字节 key 时 first female 应拿 doubao，实际 {m.voice_for('乙')!r}"


# ── Plan A Task 2（2026-10-18）：全片唯一实际发声者继承所选音色 ──────────────


def test_build_speaker_voice_map_single_spoken_speaker_inherits_narrator_voice():
    """单口播例外：全片实际只有一人发声 → 该 speaker 继承 narrator_voice。

    事故 vg_da6419f0c0e6：角色表多人但只有「主讲人」开口，用户选 332，
    主讲人却被轮转到池音色。表里多人不阻断例外——按**实际发声**判定。
    """
    chars = [
        _char("主讲人", "年轻女性，长发"),
        _char("助手", "中年男性"),
    ]
    m = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332", spoken_speakers={"主讲人"})
    assert m.voice_for("主讲人") == "preset:332", "唯一发声者必须继承用户所选音色"


def test_build_speaker_voice_map_single_spoken_speaker_not_in_characters():
    """唯一发声者不在角色表 → 同样继承 narrator_voice（而非 default）。"""
    m = build_speaker_voice_map([_char("助手", "男性")], default_voice="preset:146", narrator_voice="preset:332", spoken_speakers={"神秘人"})
    assert m.voice_for("神秘人") == "preset:332"


def test_build_speaker_voice_map_two_spoken_speakers_keep_pool_assignment():
    """两个实际发声者 → 不触发单口播例外，池分配与无 spoken_speakers 完全一致。"""
    chars = [_char("甲", "女性"), _char("乙", "男性")]
    m_ref = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332")
    m_two = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332", spoken_speakers={"甲", "乙"})
    assert m_two.by_speaker == m_ref.by_speaker
    assert m_two.voice_for("甲") != "preset:332" and m_two.voice_for("乙") != "preset:332"


def test_build_speaker_voice_map_none_and_empty_spoken_speakers_unchanged():
    """None（旧调用兼容）与空集（纯旁白片）都不改动分配。"""
    chars = [_char("甲", "女性"), _char("乙", "男性")]
    m_ref = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332")
    m_none = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332", spoken_speakers=None)
    m_empty = build_speaker_voice_map(chars, default_voice="preset:146", narrator_voice="preset:332", spoken_speakers=set())
    assert m_none.by_speaker == m_ref.by_speaker
    assert m_empty.by_speaker == m_ref.by_speaker

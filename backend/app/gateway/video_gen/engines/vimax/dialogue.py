"""ViMax dialogue parsing + per-speaker voice assignment (P1/P2).

ViMax's screenwriter emits each shot's audio as an ``audio_desc`` string in
one of two regular shapes:

- Dialogue:  ``[Speaker] 菲利普: "什么事情这么重要，需要开正式会议？"``
- Non-dialogue (sound effect / narration): ``[Sound Effect] ...`` / ``[音效] ...`` / plain text

This module parses that into a structured ``DialogueSegment`` so the narration
generator can (a) use the actual character line for TTS instead of LLM-rewriting
it into third-person narration, and (b) assign a distinct voice per speaker.

Pure functions, no IO — easy to unit test in isolation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DialogueSegment:
    """One shot's audio, parsed into speaker + line.

    - is_dialogue=True: a character speaking. speaker is the character name,
      line is the quoted speech (markers stripped).
    - is_dialogue=False: a sound effect, narration, or unparseable text.
      speaker is "" and line is the cleaned original text.
    - emotion: 说话人括号注释里的情绪标注原文（如 ``焦急``/``疑惑且防备``），
      无注释为 ""。T5 据此映射 doubao emotion 参数；非 doubao 音色忽略。
    """

    speaker: str
    line: str
    is_dialogue: bool
    emotion: str = ""
    # T6（2026-10-16 收口批）：已知对白标签开头但三级解析全失败（如
    # ``[说话人]船夫快停桨`` 无分隔符）→ 标签剥掉回落旁白时置 True——narration
    # 层据此记 KIND_FORMAT_DRIFT sidecar（修前只 logger.warning，留痕断链）。
    tag_fellthrough: bool = False


# Match: optional [Speaker] tag, a speaker name, a colon, then quoted speech.
# Supported quote pairs: Chinese 「」/『』, curly “”/‘’, ASCII ""/''.
# The non-greedy capture + $ anchor backtracks to the LAST closing quote,
# so multi-sentence and nested-quote lines parse as one segment.
#
# Task 8b: 引号**成对**匹配——正则分别捕获开引号 (?P=q) 与闭引号 (?P=close)，
# 成对性由下方 _QUOTE_PAIRS 字典在匹配后校验（不是正则反向引用；开/闭引号
# 字符集不同，如 “…”『…』，反向引用表达不了跨字符配对）。此前是
# 「任意开引号 … 任意闭引号」的字符类，且字符类**实际不含 「」** —— 注释声称支持
# 却没有实现。中文 LLM 输出极常用 「」，导致 `小明：「台词」` 被当旁白，TTS 把
# 人名、冒号、引号一起念出来且丢角色音色。
_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "\u201c": "\u201d",  # “ ”
    "\u2018": "\u2019",  # ‘ ’
    "\u300c": "\u300d",  # 「 」
    "\u300e": "\u300f",  # 『 』
}

# 对白标签词表（2026-10-14 格式漂移事故）：qwen3.8-max 迁移后分镜 LLM 倾向输出
# 中文标签 ``[说话人]``（上游 prompt 只定义英文 ``[Speaker]``），解析器漏识别 →
# 标签/角色/情绪整行被旁白念出并烧字幕。后续再漂移只往词表加词，不改结构。
_DIALOGUE_TAGS = ("[Speaker]", "[说话人]")
# AI 评审二轮 #4（2026-10-14）：re.escape——词表设计上「后续再漂移只加词」，
# 未来若加入含正则元字符的标签，未转义会静默改变两个正则的匹配语义。
_TAG_ALTERNATION = "|".join(re.escape(tag.strip("[]")) for tag in _DIALOGUE_TAGS)

_DIALOGUE_RE = re.compile(
    r"^\s*(?:\[(" + _TAG_ALTERNATION + r")\]\s*)?"  # optional [Speaker]/[说话人] tag
    r"(?P<speaker>[^:：\[\]]+?)\s*[:：]\s*"  # speaker name, then half/full-width colon
    r"(?P<q>[\"'\u201c\u2018\u300c\u300e])"  # opening quote (captured)
    r"(?P<line>.+?)"
    r"(?P<close>[\"'\u201d\u2019\u300d\u300f])\s*$",  # closing quote
    re.DOTALL,
)

# T1（2026-10-14 事故根修）：无冒号无引号形态——``[说话人] 船夫 （焦急） 快停桨``。
# 必须排在 colon-fallback **之后**（见 parse_dialogue）：``船夫：快停桨`` 若先走本
# 正则，非贪婪 speaker 会切成 speaker=船 / line=夫：快停桨。
# T6（2026-10-16 收口批）：情绪注释改**任意段**全消费（``（焦急）（坚定）`` 第二段
# 修前漏进台词被念出——PR#19 评审 low）；末段作情绪（_extract_emotion 取末段）。
_DIALOGUE_NO_COLON_RE = re.compile(
    r"^\[(" + _TAG_ALTERNATION + r")\]\s*"
    r"(?P<speaker>[^:：\[\]()（）\s]+)"  # 单 token 说话人（无空白/括号/冒号）
    r"(?P<emotion_anns>(?:\s*[（(][^）)]*[）)])*)"  # 任意段（焦急）（坚定） 注释全消费
    r"(?:(?<=[）)])|\s+)"  # 台词边界：注释结尾或至少一个空白
    r"(?P<line>.+)$",
)

# Tags that mark a non-dialogue line (sound effects). Verified against upstream
# ViMax shot_description.py: only [Sound Effect] and [Speaker] are defined; we
# include [音效] (Chinese for Sound Effect) since ViMax's LLM may emit Chinese
# tags for Chinese content. [Narration]/[旁白] are NOT upstream tags — left out
# so a future ViMax narration tag falls through to the plain-text path rather
# than being silently muted. The leading marker is kept in `line`.
_NON_DIALOGUE_TAGS = ("[Sound Effect]", "[音效]")

# T4（2026-10-14 决策 5 B 面）：无标签音效描述的保守启发式——去除空白后 ≤20 字且
# 以 声/音/响 结尾，且不含引号/人称代词 → 判音效（静音 + sidecar 留痕可审计）。
# 修前取舍（2026-07-27「多念优于静音」只认带标签）在刻舟求剑事故翻车：
# ``水面滴水声与轻微波浪声`` 这类纯环境声描述被旁白逐字念出。人称代词/引号
# 排除是防误杀旁白句（``他说他听见了声音`` 是台词不是音效）。
_SFX_TAIL_CHARS = ("声", "音", "响")
_PERSONAL_PRONOUNS = ("我", "你", "他", "她", "它", "您", "咱")
_SFX_HEURISTIC_MAX_CHARS = 20
_QUOTE_CHARS = set("\"'“”‘’「」『』")


def is_heuristic_sound_effect(text: str) -> bool:
    """T4 保守启发式：≤20 字 + 声/音/响 结尾 + 无引号 + 无人称代词。"""
    compact = "".join(text.split())
    if not compact or len(compact) > _SFX_HEURISTIC_MAX_CHARS:
        return False
    if not compact.endswith(_SFX_TAIL_CHARS):
        return False
    if any(ch in _QUOTE_CHARS for ch in compact):
        return False
    return not any(p in compact for p in _PERSONAL_PRONOUNS)


def is_sound_effect_tagged(text: str) -> bool:
    """True 当文本带显式音效标签（[Sound Effect]/[音效]）——既有语义路径。

    T4 sidecar 判定用：带标签的静音不记启发式留痕（防噪声——短标签描述
    compact 后常同 hit 启发式条件）。
    """
    stripped = text.strip()
    return any(stripped.startswith(tag) for tag in _NON_DIALOGUE_TAGS)


def is_sound_effect(text: str) -> bool:
    """True when the text is a sound-effect description (tagged OR heuristic).

    音效段必须静音（环境声来自视频音轨，不能让 TTS 念出'birdsong'）；
    非音效的非对白文本（旁白句）由 narrator 念出——这是两者的分判函数。
    - 带标签（[Sound Effect]/[音效]）：2026-07-27 起的既有路径。
    - 无标签：T4 保守启发式（is_heuristic_sound_effect，2026-10-14 取代
      「多念优于静音」取舍），命中由调用方记 KIND_SFX_HEURISTIC sidecar
      （可审计，防误杀旁白）。
    """
    if is_sound_effect_tagged(text):
        return True
    return is_heuristic_sound_effect(text.strip())


# Strip full-width （）and half-width () parenthetical annotations from a
# speaker name. ViMax's screenwriter emits '[Speaker] 菲利普（疑惑且防备）: ...'
# but characters.json's identifier_in_scene is the clean name '菲利普'. Without
# stripping, voice_map.voice_for() misses → fallback to default_voice → every
# character sounds the same (vg_cda1b579a220 regression).
_ANNOTATION_RE = re.compile(r"[（(][^）)]*[）)]")


def _strip_speaker_annotations(speaker: str) -> str:
    """Remove parenthetical annotations (full/half-width) + trim whitespace."""
    cleaned = _ANNOTATION_RE.sub("", speaker)
    return cleaned.strip()


def _extract_emotion(speaker_raw: str) -> str:
    """说话人原始串里的情绪标注：取**末段**括号注释内容（T5 用）。

    先例 ``甲（第一幕）（坚定）``：第一段是幕标不是情绪，末段才是——
    ``_strip_speaker_annotations`` 全剥，这里在全剥前把末段留下。映射到
    doubao emotion 由 T5 的映射表决定，未映射的不下发。
    """
    anns = _ANNOTATION_RE.findall(speaker_raw)
    if not anns:
        return ""
    return anns[-1].strip("（）()").strip()


def _strip_paired_quotes(text: str) -> str:
    """剥首尾成对引号（「台词」/“台词” 等）；不成对则原样返回。"""
    while len(text) >= 2 and _QUOTE_PAIRS.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    return text


def parse_dialogue(audio_desc: str | None) -> DialogueSegment | None:
    """Parse a ViMax audio_desc into a DialogueSegment.

    Three-stage, in order: (1) primary regex for the ``[Speaker]/[说话人] X:
    "line"`` quoted shape; (2) tag + colon-split fallback (tag is a strong
    signal the line IS dialogue — don't lose it to narration); (3) T1 no-colon
    no-quote shape ``[说话人] 船夫 （焦急） 快停桨`` (2026-10-14 事故格式).
    Stage (3) must run AFTER (2) — see _DIALOGUE_NO_COLON_RE. The fallback logs
    a warning so ViMax LLM format drift is visible.

    Returns None for empty/None input. Never raises.
    """
    if not audio_desc or not audio_desc.strip():
        return None

    text = audio_desc.strip()

    # Sound-effect / narration tags → non-dialogue, keep full text as line.
    if any(text.startswith(tag) for tag in _NON_DIALOGUE_TAGS):
        return DialogueSegment(speaker="", line=text, is_dialogue=False)

    m = _DIALOGUE_RE.match(text)
    # Task 8b: 引号必须**成对** —— `小明：「台词"` 这类开合不匹配不算对白，
    # 否则会把畸形文本当台词切走（丢失原文）。
    if m and _QUOTE_PAIRS.get(m.group("q")) == m.group("close"):
        return DialogueSegment(
            speaker=_strip_speaker_annotations(m.group("speaker").strip()),
            line=m.group("line").strip(),
            is_dialogue=True,
            emotion=_extract_emotion(m.group("speaker")),
        )

    # Two-stage fallback: a [Speaker]/[说话人]-tagged line the regex missed is
    # still dialogue. Split on the first half/full-width colon and treat the
    # rest as the line (stripping surrounding quotes if any).
    for tag in _DIALOGUE_TAGS:
        if text.startswith(tag):
            rest = text[len(tag) :].lstrip()
            for sep in (":", "："):
                if sep in rest:
                    speaker, _, line = rest.partition(sep)
                    line = line.strip().strip('"""\'\'「」')
                    if speaker.strip() and line:
                        logger.warning(
                            "parse_dialogue: %s line fell back to colon-split (regex miss); text=%r",
                            tag,
                            text[:120],
                        )
                        return DialogueSegment(
                            speaker=_strip_speaker_annotations(speaker.strip()),
                            line=line,
                            is_dialogue=True,
                            emotion=_extract_emotion(speaker),
                        )
                    break
            break

    # T1（2026-10-14 事故根修）：标签行无冒号无引号形态——``[说话人] 船夫 （焦急）
    # 快停桨``。排在 colon-fallback 之后（无冒号才轮到它），情绪从注释组取末段。
    m2 = _DIALOGUE_NO_COLON_RE.match(text)
    if m2:
        line = _strip_paired_quotes(m2.group("line").strip())
        speaker = _strip_speaker_annotations(m2.group("speaker").strip())
        if speaker and line:
            return DialogueSegment(
                speaker=speaker,
                line=line,
                is_dialogue=True,
                emotion=_extract_emotion(m2.group("emotion_anns") or m2.group("speaker")),
            )

    # Plain text (no marker, no quotes) → narration fallback.
    # AI 评审 #1（2026-10-14，bug·medium）：已知对白标签开头但三级解析全失败
    # （如 ``[说话人]船夫快停桨``——无空白无注释无冒号，边界不成立）→ 标签
    # **必须剥掉**再回落旁白：strip_unrecognized_tags 不剥已知标签，不剥则
    # ``[说话人]`` 字面被旁白念出并烧字幕（本批事故形态的变体回归）。
    # T6（2026-10-16 收口批）：剥标签时置 tag_fellthrough——narration 层据此记
    # KIND_FORMAT_DRIFT sidecar（修前只 logger.warning，可审计留痕断链）。
    for tag in _DIALOGUE_TAGS:
        if text.startswith(tag):
            logger.warning(
                "parse_dialogue: %s-tagged line fell through all dialogue stages; tag stripped for narration; text=%r",
                tag,
                text[:120],
            )
            text = text[len(tag) :].strip()
            return DialogueSegment(speaker="", line=text, is_dialogue=False, tag_fellthrough=True)
    return DialogueSegment(speaker="", line=text, is_dialogue=False)


def strip_unrecognized_tags(text: str) -> tuple[str, list[str]]:
    """T6：剥掉**前缀**里的未识别 ``[...]`` 标签，返回 (净文本, 剥掉的标签列表)。

    2026-10-14 决策：drift 标签不进 TTS 文本——未识别前缀整段剥离后再念，
    宁剥错不念出；剥离动作本身由调用方记 KIND_FORMAT_DRIFT sidecar。
    认识的标签（对白/音效词表）不剥——下游 parse 有自己的语义。
    """
    known = set(_DIALOGUE_TAGS) | set(_NON_DIALOGUE_TAGS)
    stripped: list[str] = []
    rest = text.strip()
    while True:
        m = re.match(r"^\[[^\[\]\n]+\]", rest)
        if not m or m.group(0) in known:
            break
        stripped.append(m.group(0))
        rest = rest[m.end() :].lstrip()
    return rest, stripped


# ── P2: per-speaker voice assignment ───────────────────────────────────────

from app.gateway.video_gen.orchestration.voices import CURATED_VOICES  # noqa: E402


@dataclass(frozen=True)
class SpeakerVoiceMap:
    """Maps a speaker name → a TTS voice_id.

    - by_speaker: per-character voice (from characters.json gender).
    - narrator_voice: voice for non-dialogue segments ([Sound Effect]/narration).
    - default_voice: fallback for unknown speakers (not in characters list).
    """

    by_speaker: dict[str, str]
    narrator_voice: str
    default_voice: str

    def voice_for(self, speaker: str) -> str:
        """Resolve the voice_id for a speaker name ("" → narrator)."""
        if speaker == "":
            return self.narrator_voice
        return self.by_speaker.get(speaker, self.default_voice)


# Voice pools per gender, zh-CN only. Plan B（2026-10-19，D4）：ViMax 整体禁用
# 360 音色——池 = doubao（有 VOLCENGINE_VOICE_ACCESS_KEY 时优先）→ zh-CN Edge
# 溢出，**不含任何 ``preset:*``**（池内 preset 会在正式 narration 的 reject
# 防漏线上炸掉）。WITHOUT the locale filter the pool would mix in en-US/ja-JP/
# ko-KR voices — a 10th same-gender speaker would get a Korean/Japanese voice
# reading Chinese text (garbage).
def _zh_voice_pool(gender: str, include_doubao: bool = True) -> list[str]:
    """zh-CN voice pool for a gender：**doubao FIRST**（有字节 key 的国内可达首选），
    edge-tts LAST（溢出槽位——需 bing 可达）。

    历史（2026-07-15 修前）：池曾保序把 edge-tts 排前 → 对白角色拿 edge → TTS
    失败插静音（silent dialogue lines 回归）——「国内可达渠道在前」的排序教训保留。
    ``include_doubao=False`` 剔除 doubao 槽位（无 VOLCENGINE_VOICE_ACCESS_KEY 时
    doubao 音色在 tts 层静默塌缩成 default，2026-07-24 核实）——池退化为纯 Edge
    （仍可用；Plan B 起不再回落 360 preset）。"""
    zh = [v for v in CURATED_VOICES if v["gender"] == gender and v.get("locale", "").startswith("zh")]
    doubao = [v["id"] for v in zh if v.get("provider") == "doubao"] if include_doubao else []
    edge = [v["id"] for v in zh if v.get("provider") == "edge-tts"]
    return doubao + edge


_MALE_PRESET_VOICES = _zh_voice_pool("Male")
_FEMALE_PRESET_VOICES = _zh_voice_pool("Female")


def _infer_gender(static_features: str) -> str | None:
    """Infer 'Male'/'Female' from a characters.json static_features string.

    Matches Chinese (男/女) and English (male/female/man/woman/boy/girl) markers,
    case-insensitive on English. Returns None if no marker found.
    """
    if not static_features:
        return None
    lower = static_features.lower()
    if "女" in static_features or any(w in lower for w in ("female", "woman", "girl")):
        return "Female"
    if "男" in static_features or any(w in lower for w in ("male", "man", "boy")):
        return "Male"
    return None


def build_speaker_voice_map(
    characters: list[dict],
    *,
    default_voice: str,
    narrator_voice: str,
    spoken_speakers: set[str] | None = None,
) -> SpeakerVoiceMap:
    """Assign a distinct voice to each character by inferred gender.

    - Known gender: rotate through that gender's pool (qihu first, edge-tts overflow).
    - Unknown gender: rotate through whichever pool has more remaining, so the
      character still gets a distinct voice rather than colliding on default
      (which would also collide with other unknowns + the narrator). Logged info.
    - Pool exhausted: fall back to default_voice (logged warning).
    - ``narrator_voice`` 从两个池中剔除，保证旁白与任一角色都不同声（Task 8b）。
    - Plan A Task 2（2026-10-18）单口播例外：``spoken_speakers``（全片**实际
      发声者**集合，None=未知/旧调用兼容）恰好一人时，该 speaker 继承
      ``narrator_voice``（用户所选）——单口播课的主讲人不必从角色池轮转（事故
      vg_da6419f0c0e6：用户选 332、主讲人落池音色）。按实际发声判定而非
      角色表人数（表多人仅一人开口仍是单口播）；0/≥2/None 不改动。池剔除
      narrator 后无人持有它，单口播改写不产生同声冲突。
    """
    male_pool = list(_MALE_PRESET_VOICES)
    female_pool = list(_FEMALE_PRESET_VOICES)
    import os

    include_doubao = bool(os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY"))
    if not include_doubao:
        # 2026-07-27：无 key 时 doubao 槽位会在 tts 层静默塌缩成 default（多角色
        # 听起来像一个人）——从轮转池剔除，改用 qihu/edge 槽位。
        logger.info("build_speaker_voice_map: VOLCENGINE_VOICE_ACCESS_KEY unset — doubao voices excluded from pools")
        male_pool = _zh_voice_pool("Male", include_doubao=False)
        female_pool = _zh_voice_pool("Female", include_doubao=False)

    # Task 8b: 把 narrator 音色从轮转池剔除。此前 narrator 不参与排除 → 第一个
    # 女性角色拿到与 narrator 相同的 preset:146（实跑坐实）→ 旁白和该角色听起来
    # 是同一个人。审计的 C5「角色音色无法区分」本身不成立（角色间确实互不相同），
    # 真 bug 在这里。
    if narrator_voice:
        male_pool = [v for v in male_pool if v != narrator_voice]
        female_pool = [v for v in female_pool if v != narrator_voice]

    by_speaker: dict[str, str] = {}
    for ch in characters:
        name = ch.get("identifier_in_scene") or ch.get("name") or ""
        if not name:
            continue
        gender = _infer_gender(ch.get("static_features", ""))
        if gender == "Male":
            pool = male_pool
        elif gender == "Female":
            pool = female_pool
        else:
            logger.info(
                "build_speaker_voice_map: gender unknown for %r (features=%r); using overflow pool",
                name,
                (ch.get("static_features") or "")[:80],
            )
            # Unknown gender: take from whichever pool has more remaining.
            pool = male_pool if len(male_pool) >= len(female_pool) else female_pool
        if pool:
            by_speaker[name] = pool.pop(0)
        else:
            logger.warning("build_speaker_voice_map: voice pool exhausted for %r; using default", name)
            by_speaker[name] = default_voice

    # Plan A Task 2：单口播例外（见 docstring）——仅 len==1 覆盖该实际 speaker。
    if spoken_speakers and len(spoken_speakers) == 1:
        only = next(iter(spoken_speakers))
        by_speaker[only] = narrator_voice
        logger.info("build_speaker_voice_map: 单口播——唯一发声者 %r 继承所选音色 %s", only, narrator_voice)

    return SpeakerVoiceMap(
        by_speaker=by_speaker,
        narrator_voice=narrator_voice,
        default_voice=default_voice,
    )

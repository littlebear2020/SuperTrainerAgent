"""Script orchestration: generate narrations from a topic, or split a fixed script.

Modeled on Pixelle-Video standard.py generate_content() + prompts/topic_narration.py.
LLM is injected (a langchain chat model) so callers control model selection and tests can mock.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

from app.gateway.video_gen.orchestration import _invoke_llm_bounded

logger = logging.getLogger(__name__)

_NARRATION_SYSTEM = "你是培训视频脚本撰写专家。根据主题生成口语化、适合配音的视频旁白。要求：每句 15-25 字，逐句独立成行，不要编号以外的修饰，不要解释。每镜旁白不超过 40 字（常速约 10-12 秒），超出请拆成两镜（可共享场景、变换机位）。"


def _compute_word_bounds(target_duration: float, n_scenes: int, tts_speed: float = 1.0) -> tuple[int, int]:
    """target_duration → (min_words, max_words) 每句字数区间（~200字/分 ≈ 3.33字/s）。

    P0-3 反馈点 2：既有内联逻辑抽出为可测函数。旁白字数反推保证 TTS 时长贴近目标。
    """
    per_scene = target_duration / max(n_scenes, 1)
    chars_per_sec = 3.33 * tts_speed  # ~200字/分 ≈ 3.33字/s
    target_chars = int(per_scene * chars_per_sec)
    min_words = max(int(target_chars * 0.8), 8)
    max_words = max(target_chars, min_words + 2)
    return min_words, max_words


# Task 10：叙事位置 → 字数带缩放系数。
# 修前 _compute_word_bounds 给所有镜同一个字数带（target_duration / n_scenes），
# 旁白因此等长，frame.duration（= TTS 实测）随之等长，成片像 PPT 翻页。
# 注意这是节奏被压平的**第一层**；第二层是 seconds_per_frame 补静音把短镜拉到统一
# 下限——若用户开了 spf，本优化效果会被部分抵消。
_OPENING_SCALE = 0.75
_ENDING_SCALE = 0.8
_MIDDLE_SCALE = 1.1
# 位置缩放的最小镜数门槛（2026-08-03 评审修）。低于此值主体镜不足以摊销开场/结尾的
# 下探，总量显著偏短：n=2 −22.5%、n=3 −11.7%、n=4 −6.2%（n=5 起 |漂移|<3%）。
_PACING_MIN_SCENES = 5


def word_bounds_for_position(index: int, total: int, base_min: int, base_max: int) -> tuple[int, int]:
    """按镜在片中的位置返回字数带（Task 10）。

    开场短促建立语境，主体承载信息，结尾收束。保证 0 < lo <= hi。

    **总量守恒有下限门槛**：系数和 = 0.75 + 1.1×(n-2) + 0.8，主体镜太少时摊销不掉
    开场/结尾的下探。实测各 n 的总量漂移（base=(40,60)）：

        n=2 −22.5%  n=3 −11.7%  n=4 −6.2%  n=5 −3.0%  n=10 +3.5%  n=20 +6.8%

    n<5 会让成片明显偏短（用户设 2 镜时旁白少近 1/4），故**小镜数不缩放**——
    节奏张弛的收益本就需要足够镜数才成立，2-3 镜谈不上"开场/主体/结尾"结构。
    （2026-08-03 评审修：原实现只跳过 total<=1。）

    **生效范围**：仅 ``generate_narrations``（generate 模式 + 短主题）接线。用户上传
    文档走 ``parse_script_to_frames`` / ``normalize_script`` / ``split_fixed_script``
    三条路径，均忠实切分原文、完全无字数带概念——本函数对它们不生效。
    """
    if total < _PACING_MIN_SCENES:
        return base_min, base_max
    if index == 0:
        scale = _OPENING_SCALE
    elif index == total - 1:
        scale = _ENDING_SCALE
    else:
        scale = _MIDDLE_SCALE
    lo = max(1, int(base_min * scale))
    hi = max(lo, int(base_max * scale))
    return lo, hi


# Phase 3 Task 10-11: 拆镜预防层。
# 单镜旁白预算：字数 = SHOT_MAX_NARRATION_SECONDS × 3.33 字/s × tts_speed
# （3.33 字/s 口径与 _compute_word_bounds 一致）。默认 40 字 ≈ 12s × 3.33 × 1.0 速；
# 主管线按 storyboard.config.tts_speed 动态计算（service._split_long_narration_frames）。
# 超出此预算的旁白在标点边界递归拆分，防止单镜定格 ≥5s。
SHOT_MAX_NARRATION_SECONDS = 12.0
# 标点边界（中文 + 英文）
_SENTENCE_ENDINGS = "。！？；!?;"


def split_long_narration(text: str, *, budget_chars: int = 40) -> list[str]:
    """在标点边界递归切分超长旁白（Phase 3 Task 11）。

    ``budget_chars`` 是每段最大字数。文本 ≤ 预算时原样返回 [text]。
    超预算时在**最后一个 ≤ 预算的标点边界**切开，递归处理剩余部分。

    无标点可切时不拆（不切词中间）——返回原文本。这是安全降级：无法安全拆分的
    超长无标点文本留在原帧，由 worker 分层降级链（Ken Burns/tpad）兜底。

    spf 显式设置时调用方应不调本函数（spf 是用户明确意愿）。
    """
    if not text or len(text) <= budget_chars:
        return [text] if text else []

    # 找最后一个 ≤ 预算的标点边界
    best_pos = -1
    for i, ch in enumerate(text):
        if ch in _SENTENCE_ENDINGS and i + 1 <= budget_chars:
            best_pos = i + 1  # 切在标点之后（含标点）

    if best_pos <= 0:
        # 无标点可切——安全降级，不拆
        return [text]

    first = text[:best_pos]
    rest = text[best_pos:].strip()
    if not rest:
        return [first]

    # 递归处理剩余部分
    return [first] + split_long_narration(rest, budget_chars=budget_chars)


# ── C2c (2026-08-09): TTS 情感线关闭 ──────────────────────────────────────
# 双探针实证：360 TTS 全部 400（不支持 emotion 参数）；doubao seed-tts 的
# synthesize_sse() 无 emotion 参数签名。两者均不支持情感/语调控制。
# 用户决策：放弃情感线，不做 ffmpeg atempo 硬凑营销效果。
# 以下两个函数保留作文档参考，但不再接线（零调用死代码）。


def _annotate_emotion(text: str, sentence_type: str = "statement") -> dict:
    """[DEAD CODE — C2c] 按句型生成 SSML prosody/emphasis 标注。勿接线。

    360 不支持 SSML，doubao 无 emotion 参数——此方案已废弃。
    question → 升调(pitch +high)；emphasis → strong + 略慢；conclusion → 慢速。
    2026-08-09 双探针确认后关闭；保留作设计参考，未来 TTS 引擎升级后可复活。
    """
    import html

    safe = html.escape(text or "")
    if sentence_type == "question":
        return {"sentence_type": "question", "ssml": f'<prosody pitch="+high">{safe}</prosody>'}
    if sentence_type == "emphasis":
        return {"sentence_type": "emphasis", "ssml": f'<prosody rate="slow"><emphasis level="strong">{safe}</emphasis></prosody>'}
    if sentence_type == "conclusion":
        return {"sentence_type": "conclusion", "ssml": f'<prosody rate="slow">{safe}</prosody>'}
    return {"sentence_type": "statement", "ssml": safe}


def detect_sentence_type(text: str) -> str:
    """[DEAD CODE — C2c] 启发式判断句型：?→question，含强调词→emphasis，结论词→conclusion。勿接线。

    360 不支持 SSML，doubao 无 emotion 参数——无可用的 TTS 情感接口。
    2026-08-09 确认后关闭，零调用。
    """
    t = (text or "").strip()
    if t.endswith(("？", "?")):
        return "question"
    if any(w in t for w in ("重要", "关键", "核心", "必须", "一定", "切记")):
        return "emphasis"
    if any(w in t for w in ("总之", "综上", "因此", "所以", "最终")):
        return "conclusion"
    return "statement"


_NORMALIZE_SYSTEM = "你是视频脚本编辑。把用户给的文档正文改写成适合配音的口语化旁白：剥掉标题/页码/项目符号/引用等非叙事内容，按场景组织，每句 15-25 字，输出纯旁白文本，每句一行，不要编号不要解释。"


def _chunk_text(text: str, chunk_size: int = 3000) -> list[str]:
    """S1: 把长文本切成 ~chunk_size 字的块，按段落/句号边界，不硬切词/句。

    供 normalize_script 分块重写与 S4 parse_script_to_frames 复用。
    """
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    buf = ""
    for para in text.split("\n"):
        # 单段过长 → 按句号再切
        while len(para) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            cut = para.rfind("。", 0, chunk_size)
            cut = cut + 1 if cut > 0 else chunk_size
            chunks.append(para[:cut])
            para = para[cut:]
        if buf and len(buf) + len(para) > chunk_size:
            chunks.append(buf)
            buf = ""
        buf = (buf + para + "\n") if para.strip() else buf
    if buf.strip():
        chunks.append(buf)
    return chunks


# 风格 id → 旁白改写用的中文风格词（Task 2，2026-08-01）。
#
# 这些 id 是**画面风格**下拉的值，本不该出现在旁白改写 prompt 里。此前
# `professional` 等原样透传，导致中文归一 prompt 出现 `风格=professional。` 噪声。
# 画面风格与讲述语气本是两件事，这里只取「语气」维度的近似映射：
# 画风偏商务/纪实 → 讲述偏正式；偏插画/3D/温暖 → 讲述偏口语。
_SCRIPT_STYLE_BY_ID: dict[str, str] = {
    "corporate": "正式",
    "professional": "正式",  # 旧值别名
    "documentary": "纪实",
    "photorealistic": "写实",
    "cinematic": "叙事",
    "warm_casual": "口语",
    "casual": "口语",  # 旧值别名
    "education_flat": "口语",
    "education_3d": "口语",
    "minimalist": "简洁",
    "watercolor": "口语",
}


def _effective_script_style(style: str | None) -> str:
    """Bug 2 (F4): 'default' 是 schema 占位（未设置）→ 归一为 '口语'（normalize_script 默认风格）。

    payload.get('style','口语') 在 payload['style']='default' 时返回 'default'（覆盖默认），
    会让 normalize LLM prompt 出现 '风格=default。' 噪声。归一后用 '口语'。

    Task 2（2026-08-01）：风格 **id** 亦须归一。`professional`/`corporate` 这类
    id 原样透传会让 prompt 出现 `风格=professional。` —— 既是英文噪声，也把
    画面风格错当成讲述语气。未知值（含用户自填的中文风格词）原样保留。
    """
    if style is None or style == "default":
        return "口语"
    return _SCRIPT_STYLE_BY_ID.get(style, style)


async def normalize_script(
    llm,
    raw_text: str,
    *,
    style: str = "口语",
    target_scenes: int | None = None,
) -> tuple[str, bool]:
    """C1+S1: 把上传文档正文经 LLM 改写成干净、TTS 友好的旁白。

    fixed 脚本模式修前直接 split_fixed_script（纯正则切句），文档标题/页码/项目符号被
    原样念出。本函数在 split 前先用 LLM 归一化（走 _invoke_llm_bounded 复用 T5 超时保护）。

    S1: 长文档分块重写（修前 ``raw_text[:4000]`` 静默丢尾）。返回 ``(normalized, truncated)``；
    某块失败时保留该块原文 + ``truncated=True``（非静默丢内容）。``_chunk_text`` 被 S4 复用。

    Bug 2 (F4): ``payload.get('style','口语')`` 在 payload['style']='default'（schema 占位）
    时返回 'default' 覆盖默认，会让 normalize LLM prompt 出现 '风格=default。' 噪声。
    ``_effective_script_style`` 把 'default'/None 归一为 '口语'。该路径覆盖 ai_image/ai_video/
    vimax 全部 generate 模式的文档归一（service.py 两处调用），故与 vimax 修复拆成独立 commit。
    """
    style = _effective_script_style(style)

    async def _one(chunk: str) -> str:
        user = f"目标场景数约 {target_scenes or '自适应'}。风格={style}。\n文档：\n{chunk}"
        resp = await _invoke_llm_bounded(
            llm,
            [
                {"role": "system", "content": _NORMALIZE_SYSTEM},
                {"role": "user", "content": user},
            ],
        )
        content = resp.content if hasattr(resp, "content") else str(resp)
        return content.strip()

    chunks = _chunk_text(raw_text)
    if len(chunks) == 1:
        return (await _one(chunks[0]), False)
    parts: list[str] = []
    truncated = False
    for chunk in chunks:
        try:
            parts.append(await _one(chunk))
        except Exception:
            logger.warning("normalize_script chunk failed, keeping raw: %s", chunk[:80])
            parts.append(chunk)
            truncated = True
    return ("\n\n".join(parts), truncated)


# ── S4: AI 结构化脚本解析 ─────────────────────────────────────────────────


class ParsedFrame(BaseModel):
    narration: str = ""
    image_prompt: str = ""
    discard: bool = False


class ParsedScript(BaseModel):
    content_type: str = "narration_only"
    frames: list[ParsedFrame] = []


# P-04（2026-08-18 两反馈批）：image_prompt 指令从「英文描述」改为「与旁白语言一致」，
# 与 storyboard_planner 的语言锚同口径（历史遗留反向指令；该字段产物在 service 两条
# 调用路径均被丢弃、只取 narration，指令从不生效，但不留误导）。
_PARSE_SYSTEM = (
    "你是视频脚本解析器。把用户输入的文本逐段改写为视频分镜旁白。\n"
    "【最高优先级：内容保真】必须覆盖输入的全部要点、数据、结论与逻辑，"
    "不得压缩、不得遗漏、不得丢弃正文。总旁白字数须 ≥ 输入字数的 90%。\n"
    "- narration: 要配音的口语化台词（单镜≤40字，内容多就拆成多镜，宁多镜不可丢内容）\n"
    "- image_prompt: 该画面的描述，语言与旁白一致（50字内；无画面信息则空）\n"
    "- discard: 仅丢弃纯噪声（页码/广告/赞赏词/作者署名）；"
    "标题/小标题/列表项/数据/结论等含信息内容必须改写进 narration，不得 discard\n"
    "目标分镜数 ≥ 给定值（按内容可多不可少）。\n"
    '输出 STRICT JSON: {"content_type":"narration_only|storyboard|article",'
    '"frames":[{"narration":"...","image_prompt":"","discard":false}]}'
)


def _target_scenes_from_len(text: str) -> int:
    """M3-1: 无显式时长/密度时按文长推导【最少】镜数（内容保真优先，宁多勿少）。

    每镜承载 ~60 字正文 → 镜数 = ceil(len/60)，clamp [8,80]。修前 target_scenes=None
    → LLM "自适应" 把 860 字压成 10 镜 ~400 字（54% 内容丢失）。
    """
    n = max(1, len(text.strip()))
    return max(8, min(80, -(-n // 60)))  # ceil(len/60)，clamp [8,80]


def _extract_json_from_text(text: str) -> dict | None:
    """从 LLM 回复中提取 JSON（处理 markdown fence）。"""
    s = text.strip()
    if "```" in s:
        for part in s.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    continue
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


async def parse_script_to_frames(
    llm,
    raw_text: str,
    *,
    target_scenes: int | None = None,
    on_progress=None,
) -> list[dict] | None:
    """S4: AI 结构化脚本解析——把任意输入归一成 {narration, image_prompt, discard}。

    比 normalize_script（扁平重写）更治本：识别台词 vs 画面描述 vs 噪声（标题/广告），
    产出统一结构。长文档分块（复用 _chunk_text）。LLM 失败/空帧 → 返回 None（调用方
    回退到 normalize+split）。走 _invoke_llm_bounded（T5 超时保护）。

    C1: on_progress(chunks_done, chunks_total) 可选 async 回调，每个 chunk 处理完后调用，
    供调用方在 progress=5→20 区间逐块刷 updated_at，避免长文档解析阶段被 reaper 误杀
    （Predicate A: updated_at 过期）。不传时行为不变。
    """
    chunks = _chunk_text(raw_text)
    all_frames: list[dict] = []
    for i, chunk in enumerate(chunks):
        user = f"目标场景数约 {target_scenes or '自适应'}。\n文本：\n{chunk}"
        try:
            resp = await _invoke_llm_bounded(
                llm,
                [
                    {"role": "system", "content": _PARSE_SYSTEM},
                    {"role": "user", "content": user},
                ],
            )
            content = resp.content if hasattr(resp, "content") else str(resp)
            data = _extract_json_from_text(content)
            if not data:
                continue
            parsed = ParsedScript.model_validate(data)
            for f in parsed.frames:
                if not f.discard and f.narration.strip():
                    all_frames.append(f.model_dump())
        except Exception:
            logger.warning("parse_script_to_frames chunk failed", exc_info=True)
            continue
        finally:
            if on_progress is not None:
                await on_progress(i + 1, len(chunks))
    return all_frames if all_frames else None


def _parse_lines(text: str) -> list[str]:
    """Strip list markers / numbering / bullets, drop blank lines."""
    lines: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        s = re.sub(r"^\s*(?:\d+[.)、]|[-*•])\s*", "", s).strip()
        if s:
            lines.append(s)
    return lines


_TOPIC_MAX_LEN = 80


async def generate_narrations(
    llm,
    *,
    topic: str,
    n_scenes: int = 5,
    min_words: int = 15,
    max_words: int = 25,
    target_duration: float | None = None,
    style: str | None = None,
    tts_speed: float = 1.0,
) -> list[str]:
    """Ask the LLM to produce ~n_scenes narration lines for the topic.

    若 topic 超过 _TOPIC_MAX_LEN 字，视为用户贴了中等长度内容 → 忠实切分（兜底）。
    target_duration：目标总时长（秒）→ 按语速反推每句字数，使配音时长贴近目标。
    style：画面风格（如「水彩插画」「3D 写实」）→ 注入旁白描述，引导后续生图/生视频风格。
    """
    if len(topic.strip()) > _TOPIC_MAX_LEN:
        return split_fixed_script(topic)

    # T4: 时长约束 → 反推每句字数（~200字/分 × tts_speed 语速）
    if target_duration is not None and target_duration > 0:
        min_words, max_words = _compute_word_bounds(target_duration, n_scenes, tts_speed)

    system = _NARRATION_SYSTEM
    if style and style != "default":
        system += f"\n画面风格为「{style}」，旁白内容应契合该风格的主题与氛围。"

    user = f"主题：{topic}\n请生成 {n_scenes} 句旁白，每句 {min_words}-{max_words} 字，每句一行。"
    if target_duration is not None and target_duration > 0:
        user += f"\n目标总时长约 {target_duration:.0f} 秒（{n_scenes} 镜），请控制每句字数使配音时长贴近。"
    # Task 10：节奏张弛。旁白是**一次** LLM 调用产出全部镜（不是逐镜），故字数带
    # 只能逐条写进提示词——统一区间会让每句等长、frame.duration 随之等长，成片像
    # PPT 翻页。开场/结尾短、主体长；总量近似守恒故不拉偏目标时长。
    if n_scenes > 1:
        _per_scene = [f"第 {i + 1} 镜：{lo}-{hi} 字" for i, (lo, hi) in enumerate(word_bounds_for_position(i, n_scenes, min_words, max_words) for i in range(n_scenes))]
        user += "\n为制造节奏张弛，各镜字数请按下列区间（开场与结尾短促，主体充实）：\n" + "；".join(_per_scene)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    resp = await _invoke_llm_bounded(llm, messages)  # E5: 有界超时
    content = resp.content if hasattr(resp, "content") else str(resp)
    return _parse_lines(content)


_LENS_KEYWORDS = r"镜头|画面|特写|场景|全景|近景|远景|推近|拉远|变焦|zoom|close-?up|pan|tilt|lens|shot|scene"


_VISUAL_MARKER_PATTERNS = [
    re.compile(r"【[^】]*】[ \t]*"),
    re.compile(rf"（[^）]*(?:{_LENS_KEYWORDS})[^）]*）[ \t]*"),
    re.compile(rf"\([^)]*(?:{_LENS_KEYWORDS})[^)]*\)[ \t]*"),
    re.compile(r"\*\*[^*]+\*\*[：:]?\s*"),
    re.compile(r"^\s*场景\s*\d+\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*镜头\s*\d*\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*(?:旁白|解说词?|画外音|画面描述|场景描述)\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*\[[^\]]*\]\s*"),
    # S3: markdown/公众号标题 + 元信息 + 裸页码（不当台词念出）
    re.compile(r"^\s*#{1,6}\s+.*$", re.MULTILINE),
    re.compile(r"^\s*(?:作者|阅读|赞赏|广告|来源|扫码|关注|点击)[：:].*", re.MULTILINE),
    re.compile(r"第\s*\d+\s*页"),
]


def _strip_visual_markers(text: str) -> str:
    """剥离脚本里的画面/镜头/场景/旁白结构性标记，只保留要进 TTS 的台词。

    仅剥白名单标签（R6），不做通配"短文本+冒号"，避免误伤合法内容。
    enclosed 标记（【】、（））整体剥离；行首标签（场景N：、旁白：）仅剥前缀。
    角色名前缀（张经理：）不剥——那是台词不是画面标记。
    """
    for pat in _VISUAL_MARKER_PATTERNS:
        text = pat.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _merge_to_count(parts: list[str], target_count: int) -> list[str]:
    """E4: 合并相邻句到 target_count 段（尽量均分），避免贴长脚本爆 N 帧（54 句→12 段）。

    无 target_count / target_count>=len(parts) 时不动（保持 1 句=1 帧）。
    """
    n = len(parts)
    if target_count <= 0 or target_count >= n:
        return parts
    base, extra = divmod(n, target_count)
    merged: list[str] = []
    idx = 0
    for i in range(target_count):
        size = base + (1 if i < extra else 0)
        merged.append("".join(parts[idx : idx + size]))
        idx += size
    return merged


def split_fixed_script(text: str, *, split_mode: str = "sentence", target_count: int | None = None) -> list[str]:
    """Split a user-provided script into narration segments.

    先剥离画面/镜头/场景/旁白标记（防止念画面描述），再切分。
    句子模式以 。！？!? 和换行作为切分边界；末句无标点也保留。
    段落模式以空行切分。
    E4: target_count → 合并相邻句到 target_count 段（Path C 长脚本不爆 N 帧）。
    """
    cleaned = _strip_visual_markers(text)

    if split_mode == "paragraph":
        parts = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    else:
        parts: list[str] = []
        for line in cleaned.split("\n"):
            line = line.strip()
            if not line:
                continue
            line_parts = re.findall(r"[^。！？!?]*[。！？!?]", line)
            if line_parts:
                parts.extend(p.strip() for p in line_parts if p.strip())
                consumed = "".join(line_parts)
                remainder = line[len(consumed) :].strip()
                if remainder:
                    parts.append(remainder)
            else:
                parts.append(line)

    if not parts:
        parts = [cleaned.strip()] if cleaned.strip() else []
    if target_count and len(parts) > target_count:
        parts = _merge_to_count(parts, target_count)
    elif len(parts) > 30:  # S2: 无 target 的长脚本硬上限——防爆爆炸（短脚本 1:1 不受影响）
        parts = _merge_to_count(parts, 30)
    return parts


_CHAPTER_SYSTEM = (
    "你是培训视频脚本撰写专家。根据章节标题和知识要点，生成口语化、适合配音的视频讲解旁白。要求：口语化表达，每段3-5句话，段落之间用空行分隔。语速约200字/分钟。按'概念→解释→示例'结构组织内容。不要编号，不要标题，只输出旁白文本本身。"
)


async def generate_chapter_narrations(
    llm,
    *,
    chapter_title: str,
    key_points: list[str],
    target_chars: int = 600,
) -> str:
    """Generate narration text for a single chapter, organized by paragraphs.

    Returns text with paragraphs separated by blank lines (\\n\\n).
    Each paragraph will become a SceneGroup downstream.
    """
    points_str = "\n".join(f"- {p}" for p in key_points)
    user = f"章节标题：{chapter_title}\n知识要点：\n{points_str}\n请生成约 {target_chars} 字的讲解旁白，段落之间用空行分隔。"
    messages = [
        {"role": "system", "content": _CHAPTER_SYSTEM},
        {"role": "user", "content": user},
    ]
    resp = await _invoke_llm_bounded(llm, messages)  # E5: 有界超时
    content = resp.content if hasattr(resp, "content") else str(resp)
    return content.strip()

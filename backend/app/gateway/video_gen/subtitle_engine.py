"""字幕质量引擎（2026-08-13 v2）。

四套互不切同的切分实现收敛为本模块的唯一入口 ``segment()``。
渲染从「SRT + force_style 字符串」改为「SRT（canonical）+ 派生 ASS」。

流水线:
    tokenize → cut_chunks → clean → join_tokens → align → post → layout_two_lines
                                                                        ↓
                                                          write_srt (canonical)
                                                          write_ass (派生)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 避头/避尾禁则表（§3 参数表）────────────────────────────────────────────

# 避头字符：不得出现在行首
_NO_LINE_START = frozenset("，。、；：？！）》」』】…—,.)%:;?]}~．，")
# 避尾字符：不得出现在行尾
_NO_LINE_END = frozenset("（【《「『“‘$(<[{")
# 句末标点（最高优先级断点）
_SENTENCE_END = frozenset("。！？!?…;")
# 子句标点（次优先级断点）
_CLAUSE_END = frozenset("，、；：,:")

# P2-A: 常用连词/语气词表（无标点长句的次优断点层）。断点在连词**前**——
# 连词归属后句（"但是/然后/所以/并且…"起句是自然的语义边界）。
# 只收双字词：tokenize 每 CJK 字一 token，双字词=相邻两 token 拼接即可命中，
# 无需分词库；三字以上（"与此同时"）由前两字"同时"覆盖，不单列。
_CONJUNCTIONS = frozenset(
    {
        "但是",
        "然后",
        "所以",
        "而且",
        "并且",
        "因为",
        "如果",
        "以及",
        "同时",
        "接着",
        "最后",
        "不过",
        "然而",
        "因此",
        "此外",
        "或者",
        "还是",
        "只是",
        "可是",
        "于是",
        "其实",
        "当然",
        "另外",
        "首先",
        "其次",
        "从而",
        "进而",
        "甚至",
        "虽然",
        "尽管",
    }
)

# 行尾标点清洗集（吸收 _clean_subtitle_text 契约）
_TRAILING_PUNCT = "。！？!?；;…、，,：:."
# 行首标点清洗集（新增——修 R2）
_LEADING_PUNCT = "。！？!?；;…、，,：:.)(%}]~"

# ── 宽度当量（D8：显示宽度近似）────────────────────────────────────────────


def _display_width(ch: str) -> float:
    """显示宽度近似（P2-B，08-18 二审修正）：组合记号=0；EAW W/F=1.0；
    EAW A（两可）按字符类别分流——字母（L*：é/т/希腊）按窄 0.5、标点符号
    （P*/S*：——/…/×/○）在 CJK 语境按宽 1.0；其余（Na/H/N）=0.5。

    实测依据（Python 3.12.13 / Unicode 15.0）：'—'/'…'/'é'/'т' 的 EAW **均为
    A**——A 一律按宽则 é/т 仍高估一倍（一行溢出），一律按窄则——/……宽度
    减半回归既有用例；按类别分流两头都保。ZWJ（Cf）按 0.5 计——罕见且无
    廉价精确法。
    """
    if unicodedata.combining(ch):
        return 0.0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 1.0
    if eaw == "A":
        # 两可类分流：字母窄、标点/符号宽（本引擎面向中文字幕，CJK 语境）
        return 0.5 if unicodedata.category(ch).startswith("L") else 1.0
    return 0.5


def _text_display_width(text: str) -> float:
    """文本的显示宽度（全角当量总和）。"""
    return sum(_display_width(ch) for ch in text)


# ═══════════════════════════════════════════════════════════════════════════
# T1: Token + tokenize
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Token:
    """词法原子。

    kind: "cjk" | "latin" | "punct" | "space"
    no_line_start: 不得出现在行首（避头）
    no_line_end: 不得出现在行尾（避尾）
    unbreakable: 不可拆（——、……、数字串、Latin 词）
    """

    text: str
    kind: str = "cjk"
    no_line_start: bool = False
    no_line_end: bool = False
    unbreakable: bool = False

    @property
    def width(self) -> float:
        return _text_display_width(self.text)


# Latin 原子正则：连续 [A-Za-z0-9] 内部可含 .'- (如 3.14, don't, U.S.A)
_LATIN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.'\-]*)?")


def tokenize(text: str) -> list[Token]:
    """将文本拆成词法原子序列。

    - 连续 [A-Za-z0-9](+内部 .'-) = 不可分 Latin 原子
    - 每 CJK 字 = 原子
    - 标点打标 no_line_start / no_line_end
    - ——、……、多位数字串标记不可拆
    """
    if not text:
        return []

    tokens: list[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]

        # 空格
        if ch.isspace():
            tokens.append(Token(ch, kind="space"))
            i += 1
            continue

        # 破折号 ——（P1-5：避头——chunk 不得以 —— 开头）
        if ch == "—" and i + 1 < len(text) and text[i + 1] == "—":
            tokens.append(Token("——", kind="punct", no_line_start=True, unbreakable=True))
            i += 2
            continue
        if ch == "-" and i + 1 < len(text) and text[i + 1] == "-":
            # 半角双破折号
            tokens.append(Token("--", kind="punct", no_line_start=True, unbreakable=True))
            i += 2
            continue

        # 省略号 ……
        if ch == "…" and i + 1 < len(text) and text[i + 1] == "…":
            tokens.append(Token("……", kind="punct", no_line_start=True, unbreakable=True))
            i += 2
            continue

        # Latin 原子
        m = _LATIN_RE.match(text, i)
        if m and m.group():
            word = m.group()
            tokens.append(
                Token(
                    word,
                    kind="latin",
                    unbreakable=True,  # Latin 词/数字不可拆
                )
            )
            i = m.end()
            continue

        # CJK 或全角标点
        no_start = ch in _NO_LINE_START
        no_end = ch in _NO_LINE_END
        kind = "punct" if (no_start or no_end or ch in _SENTENCE_END or ch in _CLAUSE_END) else "cjk"
        tokens.append(Token(ch, kind=kind, no_line_start=no_start, no_line_end=no_end))
        i += 1

    return tokens


# ═══════════════════════════════════════════════════════════════════════════
# T1: join_tokens
# ═══════════════════════════════════════════════════════════════════════════


def _is_cjk(ch: str) -> bool:
    """是否为 CJK 字符（非 ASCII、非空白）。"""
    return not ch.isascii() and not ch.isspace()


def join_tokens(tokens: list[Token]) -> str:
    """拼接 tokens 为字符串。

    CJK↔Latin 边界插空格（D2）；Latin↔Latin 保留原空格；
    CJK↔CJK 不插（C3 契约保留）；全角标点两侧不插。
    """
    if not tokens:
        return ""

    parts: list[str] = []
    for idx, tok in enumerate(tokens):
        if not parts:
            parts.append(tok.text)
            continue
        prev_text = parts[-1]
        if not prev_text:
            parts.append(tok.text)
            continue

        prev_last = prev_text[-1]
        cur_first = tok.text[0]

        # 空格 token 直接拼接
        if tok.kind == "space":
            parts.append(tok.text)
            continue

        # 已有尾部空格 → 不再加
        if prev_text.endswith(" "):
            parts.append(tok.text)
            continue

        # 当前是标点 → 不加空格（标点紧贴）
        if tok.kind == "punct":
            parts.append(tok.text)
            continue

        # 前一个字符是标点 → 不加空格
        prev_tok_kind = tokens[idx - 1].kind if idx > 0 else "cjk"
        if prev_tok_kind == "punct":
            parts.append(tok.text)
            continue

        # CJK↔Latin 边界 → 加空格（D2）
        if _is_cjk(prev_last) and cur_first.isascii() and cur_first.isalnum():
            parts.append(" ")
            parts.append(tok.text)
            continue
        if prev_last.isascii() and prev_last.isalnum() and _is_cjk(cur_first):
            parts.append(" ")
            parts.append(tok.text)
            continue

        # ASCII↔ASCII 词边界 → 插单个空格（P1-4：切分/合并造出的英文词间空格被吞）
        if prev_last.isascii() and prev_last.isalnum() and cur_first.isascii() and cur_first.isalnum():
            parts.append(" ")
            parts.append(tok.text)
            continue

        # 默认：直接拼接
        parts.append(tok.text)

    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# T1: cut_chunks
# ═══════════════════════════════════════════════════════════════════════════


def cut_chunks(tokens: list[Token], *, max_width: float) -> list[str]:
    """贪心按显示宽度切分 tokens 为 chunk 字符串列表。

    优先级：句末标点 > 子句标点 > 空格 > 连词/语气词 > 硬切
    硬切永不落在 Latin 原子/不可拆 token 内。
    切点后首字符 ∈ 避头集 → 并入上段。
    上段末字符 ∈ 避尾集 → 移入下段。
    """
    if not tokens:
        return []

    # 过滤纯空白 token（不产生输出但影响宽度计算）
    # 实际上空白需要保留用于 Latin 词间分隔

    chunks: list[list[Token]] = []
    buf: list[Token] = []
    buf_w = 0.0

    def _flush():
        nonlocal buf, buf_w
        if buf:
            chunks.append(buf)
            buf = []
            buf_w = 0.0

    for idx, tok in enumerate(tokens):
        tok_w = tok.width

        # 检查是否超过宽度
        if buf and buf_w + tok_w > max_width:
            # 需要在这里断行——尝试找到最佳断点
            # 先尝试在 buf 内找标点断点
            cut_idx = _find_best_break(buf, max_width)
            if cut_idx is not None and cut_idx > 0:
                # 断在 cut_idx 之后
                chunks.append(buf[: cut_idx + 1])
                buf = buf[cut_idx + 1 :]
                buf_w = sum(t.width for t in buf)

            # 检查当前 token 是否能加入
            if buf_w + tok_w > max_width and buf:
                # 即使断完 buf 内的标点还是放不下
                _flush()

        buf.append(tok)
        buf_w += tok_w

        # 句末标点 → 立即断（天然避头——下一个非标点 token 不会是避头标点）
        if tok.kind == "punct" and tok.text in _SENTENCE_END:
            _flush()

    _flush()

    # kinsoku 后处理：确保避头/避尾规则
    chunks = _apply_kinsoku(chunks)

    # join_tokens 每个 chunk
    return [join_tokens(ch) for ch in chunks if ch]


def _find_best_break(buf: list[Token], max_width: float) -> int | None:
    """在 buf 内找最佳断点索引（在该索引后断开）。

    优先级：句末标点 > 子句标点 > 空格 > 连词/语气词 > 硬切
    返回 None 表示无法在 buf 内找到好的断点。
    """
    # 从后往前找句末标点
    for i in range(len(buf) - 1, -1, -1):
        if buf[i].kind == "punct" and buf[i].text in _SENTENCE_END:
            return i
    # 从后往前找子句标点
    for i in range(len(buf) - 1, -1, -1):
        if buf[i].kind == "punct" and buf[i].text in _CLAUSE_END:
            return i
    # 从后往前找空格
    for i in range(len(buf) - 1, -1, -1):
        if buf[i].kind == "space":
            return i
    # P2-A: 连词/语气词边界——断在连词前（连词归属后句）。从后往前找最后一个
    # 连词起点 j（buf[j]+buf[j+1] 命中词表且均为普通 CJK token），断点 = j-1；
    # j-1==0 交由调用方 cut_idx>0 检查自然退化。与 kinsoku 兼容：连词首字
    # 不在避头集。
    for j in range(len(buf) - 2, 0, -1):
        a, b = buf[j], buf[j + 1]
        if a.kind == "cjk" and b.kind == "cjk" and not a.unbreakable and not b.unbreakable and (a.text + b.text) in _CONJUNCTIONS:
            return j - 1
    # 无标点/空格 → 硬切：找最后一个非不可拆 token 的位置
    for i in range(len(buf) - 1, -1, -1):
        if not buf[i].unbreakable:
            return i
    return None


def _apply_kinsoku(chunks: list[list[Token]]) -> list[list[Token]]:
    """应用避头尾规则：

    - 切点后首 token 是避头标点 → 并入上段
    - 上段末 token 是避尾标点 → 移入下段
    """
    if len(chunks) <= 1:
        return chunks

    result = [list(chunks[0])]
    for i in range(1, len(chunks)):
        cur = list(chunks[i])
        prev = result[-1]

        # 避头：当前 chunk 首个 token 是避头标点 → 并入前段
        while cur and cur[0].no_line_start and prev:
            prev.append(cur.pop(0))
            if not cur:
                break

        # 避尾：前段末 token 是避尾标点 → 移入当前段
        while prev and prev[-1].no_line_end and cur:
            cur.insert(0, prev.pop())

        result[-1] = prev
        if cur:
            result.append(cur)

    return [c for c in result if c]


# ═══════════════════════════════════════════════════════════════════════════
# T1: clean
# ═══════════════════════════════════════════════════════════════════════════


def clean(text: str) -> str:
    """字幕展示前清洗：strip + 去行尾标点 + 去行首标点。

    吸收 _clean_subtitle_text（rstrip 行尾标点）+ 新增 lstrip 行首标点（修 R2）。
    纯标点输入返回 ""——所有调用方必须 ``if not cleaned: continue``，
    空文本 cue 会让 libass 崩。
    """
    if not text:
        return text
    lines = [ln.strip().lstrip(_LEADING_PUNCT).rstrip(_TRAILING_PUNCT).strip() for ln in text.strip().split("\n")]
    return "\n".join(ln for ln in lines if ln)


# ═══════════════════════════════════════════════════════════════════════════
# T2: Cue + segment + layout_two_lines
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class Cue:
    """字幕条目（已含 \\N 预折行）。"""

    text: str
    start: float
    end: float


# 方向 → 单行最大全角当量宽度（§3 参数表）
_MAX_WIDTH = {
    "landscape": 16.0,
    "portrait": 13.0,
}


def segment(
    narration: str,
    *,
    word_cues: list[tuple[str, float, float]] | None = None,
    audio_duration: float | None = None,
    orientation: str = "landscape",
    min_dur: float = 1.0,
    max_dur: float = 7.0,
    max_cps: float = 9.0,
    llm_chunks: list[str] | None = None,
) -> list[Cue]:
    """将旁白文本切分为字幕 Cue 列表。

    流水线：[LLM 重切(T8)] → tokenize → cut_chunks → clean → align → post → layout_two_lines
    llm_chunks 提供时（由 async llm_resegment 预计算）跳过规则切分直接进 align+post；
    未提供时走规则链。护栏照常生效——LLM 只决定文本切点，不管时间。
    """
    if not narration or not narration.strip():
        return []

    max_width = _MAX_WIDTH.get(orientation, 16.0)

    # T8: LLM 语义重切（llm_chunks 由 async 调用方预计算传入）；未提供走规则链
    chunks: list[str] | None = llm_chunks
    if chunks is None:
        # 规则链切分
        tokens = tokenize(narration)
        if not tokens:
            return []
        raw_chunks = cut_chunks(tokens, max_width=max_width)
        chunks = [c for c in (clean(ch) for ch in raw_chunks) if c]

    if not chunks:
        return []

    # 4. align timing
    cues = _align(chunks, word_cues, audio_duration, max_width)

    # 5. post guards
    cues = _post_guard(cues, min_dur, max_dur, max_cps, max_width)

    # 6. two-line layout
    for cue in cues:
        cue.text = layout_two_lines(cue.text, max_width)

    # P2-D: 零时长 cue 出口过滤（引擎层兜底——PPT 路径 P0-5 已在上游挡 dur<=0，
    # 其他调用方传无效时长时 _align/_align_word_cues 会产出 start==end 的 cue，
    # libass 静默丢弃不可见且难排查）。过滤后为空则返回空列表；阈值 0.01s
    # 远小于 min_dur=1.0，不会误杀极短合法 cue。
    return [cue for cue in cues if cue.end - cue.start >= 0.01]


def _align(
    chunks: list[str],
    word_cues: list[tuple[str, float, float]] | None,
    audio_duration: float | None,
    max_width: float,
) -> list[Cue]:
    """给 chunks 分配时间戳。

    有 word_cues → 区间映射（保留 edge 真实时间戳）。
    否则 → 按显示宽度占比分摊 audio_duration。
    """
    if not chunks:
        return []

    # word_cues 路径：映射到真实时间戳
    if word_cues:
        return _align_word_cues(chunks, word_cues, max_width)

    # 宽度占比路径
    total = audio_duration or 0.0
    if total <= 0:
        return [Cue(text=ch, start=0.0, end=0.0) for ch in chunks]

    widths = [_text_display_width(ch) for ch in chunks]
    total_w = sum(widths) or 1.0

    cues: list[Cue] = []
    start = 0.0
    for i, (ch, w) in enumerate(zip(chunks, widths, strict=True)):
        end = total if i == len(chunks) - 1 else start + total * (w / total_w)
        cues.append(Cue(text=ch, start=start, end=end))
        start = end
    return cues


def _align_word_cues(
    chunks: list[str],
    word_cues: list[tuple[str, float, float]],
    max_width: float,
) -> list[Cue]:
    """将 chunks 映射到 word_cues 的真实时间戳——保留逐词时间戳优势。

    按 chunk 的累计显示宽度比例，在 word_cues 序列中定位每个 chunk 对应的
    词区间，取该区间首词 start → 末词 end 作为 cue 时间戳。
    回退：宽度比例估算（仅当 word_cues 太少无法对齐）。
    """
    if not word_cues:
        return [Cue(text=ch, start=0.0, end=0.0) for ch in chunks]

    total_start = word_cues[0][1]
    total_end = word_cues[-1][2]
    total_dur = total_end - total_start

    if total_dur <= 0:
        return [Cue(text=ch, start=total_start, end=total_end) for ch in chunks]

    # 按累计宽度占比分配 word_cues 到各 chunk
    widths = [_text_display_width(ch) for ch in chunks]
    total_w = sum(widths) or 1.0
    n_words = len(word_cues)

    cues: list[Cue] = []
    word_idx = 0  # 当前消费到的 word_cue 索引
    cum_w = 0.0
    for i, (ch, w) in enumerate(zip(chunks, widths, strict=True)):
        cum_w += w
        is_last = i == len(chunks) - 1
        # 该 chunk 应消费到哪个 word_cue
        target_word = n_words if is_last else max(word_idx + 1, round(n_words * cum_w / total_w))
        target_word = min(target_word, n_words)

        if word_idx < target_word and target_word <= n_words:
            start = word_cues[word_idx][1]
            end = word_cues[target_word - 1][2]
        else:
            # 回退：宽度比例（词耗尽——chunks 多于 word_cues）
            start = total_start + total_dur * ((cum_w - w) / total_w)
            end = total_end if is_last else total_start + total_dur * (cum_w / total_w)
            # P1-1：start 钳制到前条 end（保单调不重叠）；end 兜底不早于 start
            if cues:
                start = max(start, cues[-1].end)
            end = max(end, start)

        cues.append(Cue(text=ch, start=start, end=end))
        word_idx = target_word

    return cues


def _apply_gap(cues: list[Cue], gap: float = 0.08) -> list[Cue]:
    """相邻 cue 保 ≥gap 间隙（前条 end 让步——缩短前条而非推迟后条出场）。

    P1-1 兜底：前条短到无法让步（会 <0.1s）时改推迟后条 start——保单调优先于保 gap。
    """
    result = list(cues)
    for i in range(1, len(result)):
        if result[i].start - result[i - 1].end < gap:
            new_end = result[i].start - gap
            if new_end > result[i - 1].start + 0.1:  # 不让前条短到 <0.1s
                result[i - 1].end = new_end
            elif result[i - 1].end + gap <= result[i].end:
                # 前条已到下限 → 推迟后条出场（保单调不重叠）
                result[i].start = result[i - 1].end + gap
    return result


def _finalize_guards(
    cues: list[Cue],
    min_dur: float,
    max_dur: float,
    max_cps: float,
    max_width: float,
) -> tuple[list[Cue], bool]:
    """收尾复查一遍（P1-6 + P1-2）：拆分/合并产物统一复检。

    顺序：先拆（>max_dur / CPS 超标）→ 后并（<min_dur 且可并）→ 再补间隙。
    返回 (新列表, 是否变化)——调用方以深度上限 2 重入防死循环。
    """
    out: list[Cue] = []

    # 1) 拆：超长/超密 → _split_long_cue（拆不动返回原 cue，天然安全）
    for cue in cues:
        text_w = _text_display_width(cue.text.replace("\\N", ""))
        dur = max(cue.end - cue.start, 0.001)
        cps = text_w / dur
        if dur > max_dur or cps > max_cps:
            out.extend(_split_long_cue(cue, max_width, max_cps, min_dur, max_dur))
        else:
            out.append(cue)

    # 2) 并：<min_dur 且可并 → 并入相邻段（同主循环语义：优先前段，首条并后段）
    merged: list[Cue] = []
    i = 0
    while i < len(out):
        cue = out[i]
        if max(cue.end - cue.start, 0.001) >= min_dur:
            merged.append(cue)
            i += 1
            continue
        if merged:
            prev = merged[-1]
            cand = join_tokens(tokenize(prev.text.replace("\\N", "")) + tokenize(cue.text.replace("\\N", "")))
            if _text_display_width(cand) <= max_width * 2:
                prev.text = cand
                prev.end = cue.end
                i += 1
                continue
        if i + 1 < len(out):
            nxt = out[i + 1]
            cand = join_tokens(tokenize(cue.text.replace("\\N", "")) + tokenize(nxt.text.replace("\\N", "")))
            if _text_display_width(cand) <= max_width * 2:
                nxt.text = cand
                nxt.start = cue.start
                i += 1
                continue
        merged.append(cue)
        i += 1

    # 3) 间隙（同主循环语义）
    merged = _apply_gap(merged)
    return merged, merged != cues


def _post_guard(
    cues: list[Cue],
    min_dur: float,
    max_dur: float,
    max_cps: float,
    max_width: float,
) -> list[Cue]:
    """post 护栏：

    - < min_dur 或 ≤3 全角当量 → 并入相邻段（优先前段）
    - > max_dur 或 CPS 超标 → 在标点/词边界再拆（P0-6：无宽度前置）
    - 相邻 cue 保 ≥80ms 间隙（前条 end 让步，P1-1 兜底推迟后条）
    - 收尾复查：拆分/合并产物统一复检（P1-6/P1-2，深度上限 2）
    """
    if not cues:
        return cues

    result: list[Cue] = []
    for ci, cue in enumerate(cues):
        text_w = _text_display_width(cue.text.replace("\\N", ""))
        dur = max(cue.end - cue.start, 0.001)

        # 过短尾段（≤3 全角当量；纯标点段防御性保留——clean 已滤空，正常到不了这里）
        # → 并入前段。文本必须用 join_tokens 一并合并，绝不只并时间丢字（P0-1）；
        # 合并上限按 join 后实宽判断（吸收 P2-1）；超上限保留独立 cue。
        if text_w <= 3.0 and result:
            prev = result[-1]
            merged_text = join_tokens(tokenize(prev.text.replace("\\N", "")) + tokenize(cue.text.replace("\\N", "")))
            if _text_display_width(merged_text) <= max_width * 2:
                prev.text = merged_text
                prev.end = cue.end
                continue

        # 过短段：优先并入前段（无前段则并入后段）
        if dur < min_dur:
            if result:
                prev = result[-1]
                merged_text = join_tokens(tokenize(prev.text.replace("\\N", "")) + tokenize(cue.text.replace("\\N", "")))
                # 合并上限=两行总宽（不再被单行 16 字钳制——R4 修复关键）；
                # 按 join 后实宽判断（P2-1：join 可能插入空格）
                if _text_display_width(merged_text) <= max_width * 2:
                    # 用 join_tokens 保持 CJK↔Latin 空格 + 标点边界
                    prev.text = merged_text
                    prev.end = cue.end
                    continue
            elif ci + 1 < len(cues):
                # 第一个 cue 过短：并入下一个 cue
                nxt = cues[ci + 1]
                merged_text = join_tokens(tokenize(cue.text.replace("\\N", "")) + tokenize(nxt.text.replace("\\N", "")))
                if _text_display_width(merged_text) <= max_width * 2:
                    nxt.text = merged_text
                    nxt.start = cue.start
                    continue

        # 超密/超长 → 再拆（P0-6：无宽度前置——正常宽度 cue 超时同样拆）
        cps = text_w / dur if dur > 0 else 0
        if cps > max_cps or dur > max_dur:
            sub_cues = _split_long_cue(cue, max_width, max_cps, min_dur, max_dur)
            result.extend(sub_cues)
            continue

        result.append(cue)

    # 间隙保证 ≥80ms（P1-1 兜底见 _apply_gap）
    result = _apply_gap(result)

    # 收尾复查（P1-6/P1-2）：拆分/合并产物统一复检，深度上限 2 防死循环
    for _ in range(2):
        result, changed = _finalize_guards(result, min_dur, max_dur, max_cps, max_width)
        if not changed:
            break

    return result


def _split_long_cue(cue: Cue, max_width: float, max_cps: float, min_dur: float, max_dur: float = 7.0) -> list[Cue]:
    """将一个过长的 cue 在标点/词边界再拆，按宽度占比重分配时间。

    首选标点/空格断点（≤max_width）；无标点可断时按 max_dur 目标片数硬切
    （CJK 字边界是合法断点——cut_chunks 硬切语义；不可拆 token 整词保留）。
    拆不动（无任何可拆边界）返回原 cue。
    """
    text = cue.text.replace("\\N", "")
    tokens = tokenize(text)
    chunks = cut_chunks(tokens, max_width=max_width)
    chunks = [c for c in (clean(ch) for ch in chunks) if c]
    if len(chunks) <= 1:
        # 无标点断点 → 按 max_dur 目标片数硬切（P0-6：否则 max_dur 门槛对纯 CJK 长 cue 形同虚设）
        dur = cue.end - cue.start
        if dur > max_dur:
            n = math.ceil(dur / max_dur)
            if n > 1:
                target_w = _text_display_width(text) / n
                chunks = cut_chunks(tokens, max_width=target_w)
                chunks = [c for c in (clean(ch) for ch in chunks) if c]
        if len(chunks) <= 1:
            return [cue]

    dur = cue.end - cue.start
    widths = [_text_display_width(ch) for ch in chunks]
    total_w = sum(widths) or 1.0

    result: list[Cue] = []
    start = cue.start
    for i, (ch, w) in enumerate(zip(chunks, widths, strict=True)):
        end = cue.end if i == len(chunks) - 1 else start + dur * (w / total_w)
        result.append(Cue(text=ch, start=start, end=end))
        start = end
    return result


# ═══════════════════════════════════════════════════════════════════════════
# T2: layout_two_lines
# ═══════════════════════════════════════════════════════════════════════════


def layout_two_lines(text: str, max_width: float) -> str:
    """两行折行：超单行宽时在合法断点插 \\N。

    D1: 下重金字塔——候选断点中上行≤下行者优先。
    上行不得 ≤2 字（除非无解）。
    """
    text = text.replace("\n", "\\N")  # 统一内部表示
    if "\\N" in text:
        return text  # 已有折行标记，不再处理

    w = _text_display_width(text)
    if w <= max_width:
        return text

    # 找所有合法断点
    breakpoints = _find_line_break_points(text, max_width)
    if not breakpoints:
        return text  # 无合法断点——保持原样

    # 按分数升序选断点（下重金字塔 + 上行 ≤2 字惩罚）；
    # 选定后剥离行 1 尾标点（P1-8），剥空则换次优断点。
    # 全部断点剥后为空 → 不折（保现状返回原文）。
    for bp in sorted(breakpoints, key=lambda b: _break_score(text, b)):
        top = text[:bp].rstrip().rstrip(_TRAILING_PUNCT).rstrip()
        bot = text[bp:].lstrip()
        if top and bot:
            return f"{top}\\N{bot}"

    return text


def _break_score(text: str, bp: int) -> float:
    """断点评分：方差 + 金字塔惩罚（top>bot）+ 上行 ≤2 字惩罚（P1-7）。

    无解豁免天然满足：breakpoints 全受罚时仍取分数最小者（现行 score 机制）。
    """
    top_w = _text_display_width(text[:bp])
    bot_w = _text_display_width(text[bp:])
    variance = (top_w - bot_w) ** 2
    penalty = 1000 if top_w > bot_w else 0
    if top_w <= 2.0:
        penalty += 500
    return variance + penalty


def _find_line_break_points(text: str, max_width: float) -> list[int]:
    """在 text 中找所有合法折行点（索引位置=在该字符前断开）。

    合法断点：标点后、空格处、CJK 字符之间。
    """
    points: list[int] = []
    for i in range(1, len(text)):
        prev_ch = text[i - 1]
        cur_ch = text[i]

        # 前半行宽度必须 ≤ max_width
        if _text_display_width(text[:i]) > max_width:
            break

        # P2: 避头——下行首字符不得在避头集
        if cur_ch in _NO_LINE_START:
            continue
        # P2: 避尾——上行末字符不得在避尾集
        if prev_ch in _NO_LINE_END:
            continue

        # 标点后断行（标点留在上行尾——layout 选定断点后统一剥离行 1 尾标点，P1-8）
        if prev_ch in _SENTENCE_END or prev_ch in _CLAUSE_END:
            points.append(i)
            continue

        # 空格处断行
        if prev_ch == " ":
            points.append(i)
            continue

        # CJK 字符之间可断
        if _is_cjk(prev_ch) and _is_cjk(cur_ch):
            points.append(i)
            continue

        # Latin↔CJK 边界可断
        if prev_ch.isascii() and _is_cjk(cur_ch):
            points.append(i)
            continue
        if _is_cjk(prev_ch) and cur_ch.isascii():
            points.append(i)
            continue

    return points


# ═══════════════════════════════════════════════════════════════════════════
# T3: ASS writer + SRT 双写
# ═══════════════════════════════════════════════════════════════════════════

# 样式 → 属性映射。ASS 规范：Bold -1=true, 0=false
_STYLE_ATTRS: dict[str, dict] = {
    "default": {"bold": 0, "border_style": 1, "italic": 0},
    "bold": {"bold": -1, "border_style": 1, "italic": 0},
    "minimal": {"bold": 0, "border_style": 1, "italic": 0},
    "outline": {"bold": 0, "border_style": 1, "italic": 0},
}


def compute_style_params(video_w: int, video_h: int, style: str = "default") -> dict:
    """从视频分辨率推导 ASS 样式参数（§3 公式）。

    Fontsize=clamp(round(video_h×比例), 下限, floor(0.9×video_w/折行宽度))
    比例竖屏 3%、横屏 4%；折行宽度竖屏 13、横屏 16（与 _MAX_WIDTH 同口径——
    竖屏按 16 算会过度钳制，2026-08-20 修复：1080×1920 的 bold/outline 曾被钳到 60）。

    P0-3：非 default 样式按倍率缩放。2026-08-20 字号上调（用户实测 bold@2048×1152
    大小刚好为基准不动，其余三档上调）：default 1.2 / minimal 0.95 / outline 1.25。
    相对大小语义：bold ≥ outline > default > minimal——outline 与 bold 同号
    （用户拍板：1.3 会全面倒挂 outline>bold，且 default/bold 间仅 2px 缝隙塞不下
    严格居中值；两样式靠描边宽度×2 与字重区分），对齐旧 force_style 表
    「bold 最大」语义不倒挂。取整：round（银行家舍入）+ 偶数取整 + 上下限钳制。
    """
    is_portrait = video_h > video_w
    ratio = 0.03 if is_portrait else 0.04

    raw_size = round(video_h * ratio)
    # 偶数取整
    raw_size = raw_size + (raw_size % 2)
    # 宽度上限：按取向取折行宽度（全角字入 90% 安全区）——竖屏折行 13 全角
    # （_MAX_WIDTH）却按 16 算上限会过度钳制（2026-08-20 修复）
    fold_chars = 13.0 if is_portrait else 16.0
    max_by_width = int(0.9 * video_w / fold_chars)
    fontsize = max(12, min(raw_size, max_by_width))

    # 样式倍率（2026-08-20 上调：default 1.0→1.2、minimal 0.75→0.95、
    # outline 1.15→1.25 与 bold 同号；bold 基准不动）
    multiplier = {"default": 1.2, "bold": 1.25, "minimal": 0.95, "outline": 1.25}.get(style, 1.0)
    if multiplier != 1.0:
        scaled = round(fontsize * multiplier)
        scaled = scaled + (scaled % 2)  # 偶数取整
        fontsize = max(12, min(scaled, max_by_width))

    if style == "minimal":
        outline = 1
    elif style == "bold":
        outline = max(1, round(fontsize * 0.06) + 1)
    elif style == "outline":
        outline = max(1, round(fontsize * 0.06) * 2)
    else:
        outline = max(1, round(fontsize * 0.06))

    marginv = round(video_h * (0.10 if is_portrait else 0.06))

    return {
        "fontsize": fontsize,
        "outline": outline,
        "shadow": 1,
        "marginv": marginv,
        "border_style": 1,
    }


def _format_ass_time(sec: float) -> str:
    """ASS 时间格式 H:MM:SS.cc（百分秒）。

    先换算总量再分解——进位不落到 s=60/m=60（P0-4）。
    """
    total_cs = int(round(sec * 100))
    h = total_cs // 360000
    m = (total_cs % 360000) // 6000
    s = (total_cs % 6000) // 100
    cs = total_cs % 100
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_color_hex(ass_bgr: str = "&H00FFFFFF") -> str:
    """ASS 颜色已在本函数内构造好。保留接口便于后续扩展。"""
    return ass_bgr


def write_ass(
    cues: list[Cue],
    *,
    out_path: str,
    video_w: int,
    video_h: int,
    style: str = "default",
) -> None:
    """写 ASS 字幕文件（PlayRes=分辨率、内置 4 样式、\\N 预折行）。"""
    lines: list[str] = []

    # [Script Info]
    lines.append("[Script Info]")
    lines.append("Title: Trainer AI Subtitles")
    lines.append("ScriptType: v4.00+")
    lines.append(f"PlayResX: {video_w}")
    lines.append(f"PlayResY: {video_h}")
    lines.append("ScaledBorderAndShadow: yes")
    lines.append("WrapStyle: 2")  # 禁 libass 自动折行——我们自行预折行
    lines.append("YCbCr Matrix: TV.709")
    lines.append("")

    # [V4+ Styles]
    lines.append("[V4+ Styles]")
    lines.append(
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )

    for style_name, attrs in _STYLE_ATTRS.items():
        p = compute_style_params(video_w, video_h, style_name)
        fontname = "Noto Sans CJK SC"
        bold = attrs["bold"]  # -1=true, 0=false (ASS spec)

        lines.append(f"Style: {style_name.capitalize()},{fontname},{p['fontsize']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,{bold},0,0,0,100,100,0,0,{p['border_style']},{p['outline']},{p['shadow']},2,20,20,{p['marginv']},1")
    lines.append("")

    # [Events]
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")

    style_cap = style.capitalize() if style in _STYLE_ATTRS else "Default"
    for cue in cues:
        if not cue.text:
            continue
        # ASS 无转义机制——裸花括号会被 libass 当 override 块吞（P1-3），换全角。
        # 只动 ASS 派生层；SRT（canonical）原文不动（subtitle_locked/PUT 语义）。
        safe_text = cue.text.replace("{", "｛").replace("}", "｝")
        lines.append(f"Dialogue: 0,{_format_ass_time(cue.start)},{_format_ass_time(cue.end)},{style_cap},,0,0,0,,{safe_text}")

    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _format_srt_time(sec: float) -> str:
    """SRT 时间格式 HH:MM:SS,mmm。

    先换算总量再分解——进位不落到 s=60/m=60（P0-4）。
    """
    total_ms = int(round(sec * 1000))
    h = total_ms // 3600000
    m = (total_ms % 3600000) // 60000
    s = (total_ms % 60000) // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues: list[Cue], *, out_path: str) -> None:
    """写 SRT 字幕文件（canonical，兼容 subtitle_locked/PUT 编辑）。"""
    lines: list[str] = []
    seq = 1
    for cue in cues:
        if not cue.text:
            continue
        text = cue.text.replace("\\N", "\n")  # SRT 用真实换行
        lines.append(str(seq))
        lines.append(f"{_format_srt_time(cue.start)} --> {_format_srt_time(cue.end)}")
        lines.append(text)
        lines.append("")
        seq += 1
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


def _parse_srt_time(ts: str) -> float:
    """解析 SRT 时间戳 HH:MM:SS,mmm → 秒。"""
    ts = ts.strip()
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_srt(srt_path: str) -> list[Cue]:
    """解析 SRT 文件 → Cue 列表。"""
    content = Path(srt_path).read_text(encoding="utf-8")
    cues: list[Cue] = []
    for block in content.strip().split("\n\n"):
        block = block.strip()
        if not block:
            continue
        blines = block.split("\n")
        if len(blines) < 3:
            continue
        parts = blines[1].split("-->")
        if len(parts) != 2:
            continue
        start = _parse_srt_time(parts[0])
        end = _parse_srt_time(parts[1])
        text = "\n".join(blines[2:])
        # 把 SRT 的真实换行转为 \N（ASS 表示）
        text = text.replace("\n", "\\N")
        cues.append(Cue(text=text, start=start, end=end))
    return cues


def srt_to_ass(
    srt_path: str,
    *,
    out_path: str,
    video_w: int,
    video_h: int,
    style: str = "default",
) -> None:
    """从 locked SRT 派生 ASS（用户文本/换行原样保留，不做 kinsoku 再处理）。"""
    cues = _parse_srt(srt_path)
    write_ass(cues, out_path=out_path, video_w=video_w, video_h=video_h, style=style)


# ═══════════════════════════════════════════════════════════════════════════
# T5: edge-tts token 重对齐
# ═══════════════════════════════════════════════════════════════════════════

# 标点吸附前置集 / 后置集（Whisper merge_punctuations 思路）
_PUNCT_PRECEDE = frozenset("'\"¿¡([{「『")
_PUNCT_FOLLOW = frozenset(".。,，!！?？:：、)」』}]…")


def normalize_word_cues(
    word_cues: list[tuple[str, float, float]],
    reference_text: str,
) -> list[tuple[str, float, float]] | None:
    """把 edge-tts 的逐词 cue 规范化后返回，或失配返回 None。

    三步：
    1. 拆词合并：相邻 token end≈start (<20ms) 且拼接后匹配 reference 中同一英文词 → 合并
    2. 标点吸附：前置集 / 后置集（Whisper merge_punctuations 思路）
    3. 重映射回原文（VideoLingo 思路）：归一化拼接 word 串 + 顺序子串搜索定位

    失配 → None → 调用方回退宽度占比。
    """
    if not word_cues:
        return None

    # Step 1: 合并极近 token（end≈start <20ms）
    merged: list[tuple[str, float, float]] = []
    for text, start, end in word_cues:
        text = text.strip()
        if not text:
            continue
        if merged and start - merged[-1][2] < 0.02:
            # 合并到上一个
            merged[-1] = (merged[-1][0] + text, merged[-1][1], end)
        else:
            merged.append((text, start, end))

    # Step 2: 标点吸附——游离标点贴到相邻词
    merged = _adsorb_punctuation(merged)

    # Step 3: 验证——拼接去空白后应能在 reference 中找到
    joined = "".join(t for t, _, _ in merged)
    norm_joined = re.sub(r"\s+", "", joined)
    norm_ref = re.sub(r"\s+", "", reference_text)

    if not norm_joined or not norm_ref:
        return None

    # 顺序子串搜索
    pos = 0
    ok = True
    for t, _, _ in merged:
        norm_t = re.sub(r"\s+", "", t)
        if not norm_t:
            continue
        idx = norm_ref.find(norm_t, pos)
        if idx < 0:
            ok = False
            break
        pos = idx + len(norm_t)

    if not ok:
        # difflib 兜底
        import difflib

        sm = difflib.SequenceMatcher(None, norm_joined, norm_ref)
        if sm.ratio() < 0.85:
            return None

    return merged


def _adsorb_punctuation(
    cues: list[tuple[str, float, float]],
) -> list[tuple[str, float, float]]:
    """标点吸附：把游离标点 token 贴到相邻词 token。

    前置标点（开括号等）→ 贴到后一个词的词首。
    后置标点（逗号、句号等）→ 贴到前一个词的词尾。
    """
    if len(cues) <= 1:
        return cues

    result = list(cues)
    for i, (text, start, end) in enumerate(result):
        stripped = text.strip()
        if not stripped:
            continue
        # 全标点 token
        if all(ch in _PUNCT_PRECEDE or ch in _PUNCT_FOLLOW or ch.isspace() for ch in stripped):
            if stripped[0] in _PUNCT_PRECEDE and i + 1 < len(result):
                # 前置标点 → 贴到后一个
                nxt_text, nxt_start, nxt_end = result[i + 1]
                result[i + 1] = (stripped + nxt_text, start, nxt_end)
                result[i] = ("", start, end)  # 标记为空，后续过滤
            elif i > 0:
                # 后置标点 → 贴到前一个
                prev_text, prev_start, prev_end = result[i - 1]
                result[i - 1] = (prev_text + stripped, prev_start, end)
                result[i] = ("", start, end)

    return [(t, s, e) for t, s, e in result if t.strip()]


# ═══════════════════════════════════════════════════════════════════════════
# T8: LLM 语义重切层（D3）
# ═══════════════════════════════════════════════════════════════════════════

_LLM_RESegment_SYSTEM = """你是一个字幕切分助手。将旁白文本按语义意群切片，用于视频字幕显示。

规则：
1. 每片宽度不超过指定上限（CJK 每字计 1 宽度当量，ASCII 字母/数字每字计 0.5）
2. 片首不得为标点
3. 不得切开英文单词或数字串
4. 中英文交界处保留一个空格
5. 优先在句号、逗号、分号等标点后切分
6. 拼接所有片去空白后必须完全等于原文——不得增删任何字符

输出格式：JSON 字符串数组，如 ["第一片", "第二片"]
"""


def _normalize_for_compare(text: str) -> str:
    """归一化用于不变量校验：去所有空白。"""
    return re.sub(r"\s+", "", text)


async def llm_resegment(
    narration: str,
    *,
    max_width: float,
    llm,
    cache_path: str | None = None,
) -> list[str] | None:
    """用 LLM 按语义意群重切旁白。

    成功返回切分后的文本片段列表；失败（超时/坏 JSON/校验不过）返回 None → 规则链兜底。
    按 ``sha256(narration+max_width)`` 缓存到 ``cache_path``（re-render 命中不重调）。

    硬不变量：``normalize(concat(chunks)) == normalize(narration)``
    """
    if not narration or not narration.strip():
        return None

    # 缓存命中（多帧共享同一 cache 文件，按 cache_key 字典索引）
    cache_key = hashlib.sha256(f"{narration}|{max_width}".encode()).hexdigest()[:16]
    if cache_path:
        cp = Path(cache_path)
        if cp.exists():
            try:
                cache = json.loads(cp.read_text(encoding="utf-8"))
                # N2 修复：支持多帧缓存 {cache_key: chunks} 字典结构
                entry = cache.get(cache_key) if isinstance(cache, dict) else None
                if isinstance(entry, list):
                    return entry
            except (json.JSONDecodeError, TypeError):
                pass

    # 调用 LLM
    try:
        from app.gateway.video_gen.orchestration import _invoke_llm_bounded

        user_msg = f"宽度上限：{max_width}（CJK 每字=1, ASCII 每字=0.5）\n旁白：\n{narration}"
        resp = await _invoke_llm_bounded(
            llm,
            [
                {"role": "system", "content": _LLM_RESegment_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        )
        content = resp.content if hasattr(resp, "content") else str(resp)
    except Exception:
        logger.warning("LLM resegment failed (invoke), falling back to rules", exc_info=True)
        return None

    # 解析 JSON
    chunks = _parse_llm_chunks(content)
    if chunks is None:
        logger.warning("LLM resegment returned unparseable JSON, falling back to rules")
        return None

    # 校验不变量：拼接去空白 == 原文去空白
    joined = _normalize_for_compare("".join(chunks))
    ref = _normalize_for_compare(narration)
    if joined != ref:
        logger.warning("LLM resegment content mismatch (got %d chars, expected %d), falling back", len(joined), len(ref))
        return None

    # 逐片过 kinsoku clean 兜底
    cleaned_chunks = [c for c in (clean(ch) for ch in chunks) if c]
    if not cleaned_chunks:
        return None

    # 写缓存（N2: 多帧共享——追加到 {cache_key: chunks} 字典）
    if cache_path:
        try:
            cp = Path(cache_path)
            existing: dict = {}
            if cp.exists():
                loaded = json.loads(cp.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and cache_key not in loaded:
                    # 是旧式 {"key":...} 还是新式 {hash: chunks}？
                    # 旧式只有 "key" 和 "chunks" 键；新式键是 hash 字符串
                    if "key" in loaded and "chunks" in loaded and len(loaded) <= 2:
                        existing = {}  # 旧格式——丢弃
                    else:
                        existing = loaded
                elif isinstance(loaded, dict):
                    existing = loaded
            existing[cache_key] = cleaned_chunks
            cp.write_text(
                json.dumps(existing, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    return cleaned_chunks


def _parse_llm_chunks(content: str) -> list[str] | None:
    """从 LLM 回复中解析 JSON 字符串数组。"""
    s = content.strip()
    # 处理 markdown fence
    if "```" in s:
        for part in s.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("["):
                try:
                    result = json.loads(part)
                    if isinstance(result, list) and all(isinstance(x, str) for x in result):
                        return result
                except json.JSONDecodeError:
                    continue
    try:
        result = json.loads(s)
        if isinstance(result, list) and all(isinstance(x, str) for x in result):
            return result
    except json.JSONDecodeError:
        pass
    return None

"""graphic_spec：graphic 帧（图内文字镜）的结构化数据与事实校验层。

graphic-native-text（2026-08-17）：spec 的消费方是 inject_graphic_text——把逐字符
校验过的文字串作为「AI 原生文字」指令注入 image_prompt，由生图模型画进画面。
LLM 只负责「提取」，本模块的确定性代码负责「核对」：spec 中所有数字必须逐字符
出现在旁白/文稿原文中，任一不出现即拒绝（幻觉拦截）→ 帧降级主引擎 + sidecar。
AI 画字无法事后逐像素校验，「发给模型的文字串本身来自原文」是唯一准确性闸门。

spec 类型：text_card（标题+≤4 要点）/ big_number（数字+单位+说明）/ steps（≤6 步）。
渲染预算：标题 ≤14 字（中英各计 1 字）；要点/步骤标签 ≤6 渲染单位（汉字 1 单位、
英文词每词 2 单位，≤3 处）——超预算 = 字段级省略（卡片照出），绝不截断/拒帧；
预算常量与词折算单一事实源在本模块（_MAX_TITLE_RENDER_CHARS/_MAX_LABEL_RENDER_
UNITS/_label_units，graphic_text.py 复用）。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Pydantic 模型在本模块非必需（spec 全程以 dict 流转、随 storyboard.json 持久化），
# 校验用纯函数实现——render/extractor/PUT 都只碰 dict，避免「模型→dict」双向漂移。

GRAPHIC_SPEC_TYPES = ("text_card", "big_number", "steps")

# 渲染预算（2026-09-02 标题截断修复 + 2026-09-03 词折算，产品拍板·单一事实源）：
# 标题 ≤14 字（中、英文字符各计 1 字，纯 len() 口径）；标签 ≤6 **渲染单位**（汉字
# 1 单位、英文词每词 2 单位——官方指南「短词 3-5 最稳」本是词数口径，术语型内容
# 按字数罚会整帧全灭，vg_aac257fcf7f3 实证 16/36 帧拒绝）。超预算 = **字段级省略**
# （卡片照出），不是截断、不是拒帧——由 parse 执行并记入 issues；graphic_text
# 注入层对盘上旧 storyboard 遗留长文字同口径省略 + warning。
_MAX_TITLE_RENDER_CHARS = 14
_MAX_LABEL_RENDER_UNITS = 6
# 形状上限（超限 = 帧级拒绝 → extractor 降级主引擎，走既有降级语义）
_MAX_DESCRIPTION_CHARS = 60  # big_number description 长度（要点/步骤标签由渲染预算省略接管）
_MAX_POINTS = 4
_MAX_STEPS = 6
_MAX_NUMBER_CHARS = 12


def _label_units(s: str) -> int:
    """标签渲染单位（2026-09-03 拍板）：汉字/全宽字符各 1 单位，连续拉丁字母数字串
    （一个「英文词」，如 Subagent、BASH、1415）每词 2 单位；分隔符（空格/斜杠/下划
    线/小数点等）不计。

    1 个英文词 ≈ 2 个汉字的视觉复杂度：纯中文标签 ≤6 字口径不变，「直到Session结束」
    （4+2=6）合法；数字串同样按词计，「全年营收3.1415万亿元」（6+4=10）仍超——
    词折算只救术语，不放宽数字幻觉防线（F-B12-006）。
    """
    units = 0
    prev_latin = False
    for ch in s:
        if ord(ch) >= 0x2E80:
            units += 1
            prev_latin = False
        elif ch.isalnum():
            if not prev_latin:
                units += 2
                prev_latin = True
        else:
            prev_latin = False
    return units


# ── 归一化 ────────────────────────────────────────────────────────────────

# 数字 token：连续阿拉伯数字，可含小数点 / 千分位逗号，尾随可选 %（全角％先归一化）
_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*%?")


def normalize_text_for_match(text: str) -> str:
    """归一化核对用文本：NFKC 全半角统一（％→%、３→3）+ 去千分位逗号 + 去空白。

    千分位去法：``1,234`` → ``1234``。``1,234.5`` → ``1234.5``。英文逗号句读
    在中文旁白里不出现在数字间，此转换对纯文本无副作用（只作用于 \d,\d 模式）。
    """
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def text_has_number(text: str) -> bool:
    """文稿级数字存在性（含全角/小数/千分位/百分比）——T7 占比自适应共用。"""
    return bool(_NUMBER_TOKEN_RE.findall(normalize_text_for_match(text or "")))


def _extract_numbers(text: str) -> set[str]:
    """从归一化文本提取数字 token 集合（千分位已剥）。"""
    return set(_NUMBER_TOKEN_RE.findall(normalize_text_for_match(text)))


def _spec_text_fields(spec: dict) -> list[str]:
    """spec 中参与核对的所有文本字段（title/points/steps/number/description）。"""
    fields: list[str] = []
    if spec.get("title"):
        fields.append(str(spec["title"]))
    if spec.get("description"):
        fields.append(str(spec["description"]))
    for key in ("points", "steps"):
        for item in spec.get(key) or []:
            fields.append(str(item))
    if spec.get("number"):
        fields.append(str(spec["number"]))
    return fields


# ── schema 校验 ───────────────────────────────────────────────────────────


def parse_graphic_spec(data: Any) -> tuple[dict | None, list[str]]:
    """校验 spec 形状并执行渲染预算省略；返回 ``(spec | None, issues)``（不抛——spec 来自 LLM 输出）。

    2026-09-02 产品拍板：超渲染预算 = **字段级省略**（卡片照出，绝不整帧降级）——
    标题 >14 字 → title 置空保留 spec；单条标签 >6 字 → 丢弃该条；丢弃后 text_card
    要点 <2 / steps 为空才帧级拒绝。其余形状不合法 → ``(None, [字段级原因])``，
    原因进 sidecar detail（extractor 透传，不再写死串）。
    """
    if not isinstance(data, dict):
        return None, ["spec 不是对象"]
    spec_type = data.get("type")
    if spec_type == "text_card":
        title = str(data.get("title") or "").strip()
        points = [str(p).strip() for p in (data.get("points") or []) if str(p).strip()]
        # graphic-polish T6（2026-08-16）：≥2 条——单点=伪归纳（单句内容本就不该上卡，
        # 由 extractor 降级主引擎）。渲染期 spec=None 即按普通画面出 AI 图（保底
        # text_card_from_narration 已于 2026-08-17 随 Pillow 渲染器退役）。
        if len(points) < 2:
            return None, [f"要点少于 2 条（实际 {len(points)} 条，单点伪归纳）"]
        if len(points) > _MAX_POINTS:
            return None, [f"要点数 {len(points)} 超上限 {_MAX_POINTS}"]
        issues: list[str] = []
        if len(title) > _MAX_TITLE_RENDER_CHARS:
            issues.append(f"标题超长（{len(title)}>{_MAX_TITLE_RENDER_CHARS}），已省略")
            title = ""
        kept_points = []
        for p in points:
            u = _label_units(p)
            if u > _MAX_LABEL_RENDER_UNITS:
                issues.append(f"要点超长已省略（{u} 单位>{_MAX_LABEL_RENDER_UNITS}）：「{p[:6]}…」")
            else:
                kept_points.append(p)
        if len(kept_points) < 2:
            issues.append(f"要点超长省略后仅剩 {len(kept_points)} 条（至少 2 条）")
            return None, issues
        return {"type": "text_card", "title": title, "points": kept_points}, issues
    if spec_type == "big_number":
        number = str(data.get("number") or "").strip()
        if not number:
            return None, ["big_number 缺少 number"]
        if len(number) > _MAX_NUMBER_CHARS:
            return None, [f"number 超长（{len(number)}>{_MAX_NUMBER_CHARS}）"]
        out: dict = {"type": "big_number", "number": number}
        unit = str(data.get("unit") or "").strip()
        if unit:
            if len(unit) > 8:
                return None, [f"unit 超长（{len(unit)}>8）"]
            out["unit"] = unit
        desc = str(data.get("description") or "").strip()
        if desc:
            if len(desc) > _MAX_DESCRIPTION_CHARS:
                return None, [f"description 超长（{len(desc)}>{_MAX_DESCRIPTION_CHARS}）"]
            out["description"] = desc
        return out, []
    if spec_type == "steps":
        title = str(data.get("title") or "").strip()
        steps = [str(s).strip() for s in (data.get("steps") or []) if str(s).strip()]
        if not steps:
            return None, ["steps 为空"]
        if len(steps) > _MAX_STEPS:
            return None, [f"步骤数 {len(steps)} 超上限 {_MAX_STEPS}"]
        issues = []
        if len(title) > _MAX_TITLE_RENDER_CHARS:
            issues.append(f"标题超长（{len(title)}>{_MAX_TITLE_RENDER_CHARS}），已省略")
            title = ""
        kept_steps = []
        for s in steps:
            u = _label_units(s)
            if u > _MAX_LABEL_RENDER_UNITS:
                issues.append(f"步骤超长已省略（{u} 单位>{_MAX_LABEL_RENDER_UNITS}）：「{s[:6]}…」")
            else:
                kept_steps.append(s)
        if not kept_steps:
            issues.append("步骤全部超长已省略，steps 为空")
            return None, issues
        out = {"type": "steps", "steps": kept_steps}
        if title:
            out["title"] = title
        return out, issues
    return None, [f"spec 类型不合法：{spec_type!r}"]


# ── 事实校验层 ────────────────────────────────────────────────────────────


def validate_spec_against_narration(
    spec: dict,
    narration: str,
    full_script: str,
) -> tuple[bool, list[str]]:
    """逐字符核对：spec 所有数字必须出现在旁白或全文稿中（归一化后）。

    单位变体白名单：unit 单独核对为**子串**（``万``/``亿``/``%``/``倍`` 等短单位
    经 NFKC 后子串命中即可），数字部分仍走 token 精确匹配。标题/纯文字要点允许
    LLM 概括（不做逐字核对）——硬要求只约束数字（R2「逐字符准确」的对象是数据）。

    返回 (ok, reasons)；reasons 人类可读（含具体被拒数字），进 sidecar detail。
    """
    if not isinstance(spec, dict):
        return False, ["spec 不是对象"]
    haystack_text = f"{narration or ''}\n{full_script or ''}"
    haystack = normalize_text_for_match(haystack_text)
    reasons: list[str] = []

    # big_number.number 必须整体出现（子串即可：旁白 "达1234万" 含 "1234"）
    number = spec.get("number")
    if number:
        n_norm = normalize_text_for_match(str(number))
        if n_norm and n_norm not in haystack:
            reasons.append(f"数字 {number} 未出现在旁白/文稿中")

    # unit 子串核对（含在 haystack 即可，覆盖 万/亿/%/倍 及其组合）
    unit = spec.get("unit")
    if unit:
        u_norm = normalize_text_for_match(str(unit))
        if u_norm and u_norm not in haystack:
            reasons.append(f"单位 {unit} 未出现在旁白/文稿中")

    # 所有文本字段里的数字 token 全量核对（含 description/points/steps/title）
    for field in _spec_text_fields(spec):
        for token in _extract_numbers(field):
            if token not in haystack:
                reasons.append(f"数字 {token}（出现于「{field[:20]}…」）未出现在旁白/文稿中")

    return (not reasons), reasons

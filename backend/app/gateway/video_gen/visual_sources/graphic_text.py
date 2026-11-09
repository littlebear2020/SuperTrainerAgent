"""graphic-native-text（2026-08-17）：把逐字符校验过的 spec 文字转成「图内文字」渲染指令。

决策背景：Pillow 卡片与 AI 画面无法构造性风格统一（用户三轮实测否决），改为
**AI 原生文字**——文字由生图模型直接画进画面，同模型同风格词 → 风格统一是构造保证。
spec 由 graphic_spec 提取+逐字符校验（文字串本身来自旁白/文稿原文）；本模块只做
确定性拼装，零 LLM。准确性分工：图内文字=强化呈现（非 100%），精确数字由字幕逐字兜底。
"""

from __future__ import annotations

import logging

from .graphic_spec import _MAX_LABEL_RENDER_UNITS, _MAX_TITLE_RENDER_CHARS, _label_units

logger = logging.getLogger(__name__)

_MAX_LABELS = 3  # 处数上限：越多越易错、越乱

# T5b（2026-08-18 拍板⑦）：跨帧版式统一——治「10 张文字卡 10 种风格」。旧 _TAIL 的
# 「与画面风格和配色统一」是帧内软约束，跨帧统一全靠 base prompt 风格词：default 下
# 为零（P-06）、分块漂移下逐块变（P-04）。修法：任务级版式卡（T5 锚定调用现场设计
# 一次，见 storyboard_planner._generate_global_anchor）逐字注入**所有** graphic 帧。
# 2026-09-03：「加粗中文」→「中文或英文单词」——标题修复（14 字中英混排）与标签
# 词折算（术语可直接用英文）后，英文本来就是合法图内文字。
_RENDER_TAIL = "。文字为简短清晰的加粗文字（中文或英文单词），笔画完整、无错字、无变形"
_LAYOUT_PREFIX = "。全片信息图版式统一："
# 回落目标（评审修订）：锚定失败/无版式字段时不回落旧 _TAIL（=回到逐卡漂移坏现状），
# 回落到确定性默认版式卡——等距信息图基座常量（固定配色基调+字体风格+版式骨架），
# 保证任何情况下同任务 graphic 帧版式指令逐字一致。
_DEFAULT_LAYOUT_CARD = "米白底深蓝主色与浅灰辅助色配色，字体风格为无衬线加粗黑体，版式骨架为等距网格——元素等宽等距排布、留白均匀、标题居上"


def _renderable(text: str, kind: str, measure, budget: int, unit_label: str) -> str:  # noqa: ANN001
    """渲染预算闸（2026-09-02 产品拍板 + 2026-09-03 词折算）：超预算 = 省略不画 +
    warning，**绝不截断**。

    预算常量单一事实源在 graphic_spec.py（parse 层对 LLM 提取产物同口径省略）；
    本层是注入前的最后一道同口径闸——防御盘上旧 storyboard 的遗留长文字（截断会
    裁出「3.14」式幻觉数字，F-B12-006）。标题按 len()（中英各计 1 字），标签按
    ``_label_units``（汉字 1 单位、英文词每词 2 单位）。空串原样返回。
    """
    s = (text or "").strip()
    if not s:
        return ""
    n = measure(s)
    if n > budget:
        logger.warning("graphic inject: %s超长（%d>%d %s），已省略不画：「%s」", kind, n, budget, unit_label, s)
        return ""
    return s


def _renderable_labels(items: list | None) -> list[str]:
    """条目标签批处理：超预算条目整条省略（warning），合规条目完整保留，取前 _MAX_LABELS 处。

    ``items`` 允许 None——调用方直接传 ``spec.get("points")``（键缺失时为 None）。
    """
    labels: list[str] = []
    for item in items or []:
        lb = _renderable(str(item or ""), "标签", _label_units, _MAX_LABEL_RENDER_UNITS, "单位")
        if lb:
            labels.append(lb)
    return labels[:_MAX_LABELS]


# ── 审查偏离项 4（2026-08-19）：注入后 prompt 长度守护 ──
# Ark seedream 官方口径「prompt ≤300 汉字 / 600 英文单词」（BYTEDANCE_ARK_API_
# REFERENCE.md §1.2）。极端组合（base 描述 180 字 + 版式卡 60 字 + 文字指令 +
# 渲染尾缀）会越过预算，修前无截断/告警。守护口径：汉字/全宽字符 1 单位，
# 英文单词 0.5 单位（600 词 = 300 汉字的官方换算）。
# 注：预算按默认生图 provider（Ark seedream）计——其他 provider（gpt-image-2
# 等）上限更宽，极端组合下多裁几字是可接受代价，避免按 provider 逐个传限。
_ARK_PROMPT_BUDGET_UNITS = 300


def _prompt_units(s: str) -> int:
    """Ark 混合计长：CJK/全宽字符 1 单位，连续拉丁数字串（词）0.5 单位。

    内部以 half-unit 计（汉字 2、词 1），出口向上取整回单位（保守方向）。
    """
    if not s:
        return 0
    half = 0
    prev_latin = False
    for ch in s:
        if ord(ch) >= 0x2E80:  # CJK 统一表意/扩展/符号、全宽标点等
            half += 2
            prev_latin = False
        elif ch.isalnum() and ord(ch) < 0x2E80:
            if not prev_latin:
                half += 1  # 每词 0.5（词内续字符 0）
                prev_latin = True
        else:
            prev_latin = False
    return (half + 1) // 2


def _trim_to_units(s: str, budget_units: int) -> str:
    """裁**尾部**保头部（分层模板主体在前）到预算单位数内。

    不变式：``_prompt_units(_trim_to_units(s, b)) <= b``（half 预算切齐）。
    """
    if budget_units <= 0 or not s:
        return ""
    budget_half = budget_units * 2
    half = 0
    prev_latin = False
    for i, ch in enumerate(s):
        cost = 0
        if ord(ch) >= 0x2E80:
            cost = 2
            prev_latin = False
        elif ch.isalnum() and ord(ch) < 0x2E80:
            if not prev_latin:
                cost = 1
                prev_latin = True
        else:
            prev_latin = False
        if half + cost > budget_half:
            return s[:i]
        half += cost
    return s


def inject_graphic_text(base_prompt: str, spec: dict | None, layout_card: str = "") -> str:
    """在基础画面描述后追加图内文字渲染指令；无有效 spec 原样返回（按普通画面渲染）。

    ``layout_card``：任务级信息图版式卡（配色基调+字体风格+版式骨架）——同任务所有
    graphic 帧传**同一串**（storyboard.graphic_layout_card，锚定调用产出一次），版式
    指令逐字一致；空串/空白 = 确定性默认版式卡（_DEFAULT_LAYOUT_CARD，写死常量）。
    """
    if not isinstance(spec, dict):
        return base_prompt
    t = spec.get("type")
    if t == "big_number":
        num = f"{spec.get('number', '')}{spec.get('unit', '')}".strip()
        if not num:
            return base_prompt
        text_part = f"画面以巨大醒目的视觉主体呈现加粗数字「{num}」"
    elif t == "text_card":
        title = _renderable(str(spec.get("title") or ""), "标题", len, _MAX_TITLE_RENDER_CHARS, "字")
        labels = _renderable_labels(spec.get("points"))
        if not title and not labels:
            return base_prompt
        text_part = f"画面顶部呈现标题文字「{title}」" if title else ""
        if labels:
            joined = "、".join(f"「{lb}」" for lb in labels)
            text_part += ("，配以关键词标签：" if text_part else "画面呈现关键词标签：") + joined
    elif t == "steps":
        title = _renderable(str(spec.get("title") or ""), "标题", len, _MAX_TITLE_RENDER_CHARS, "字")
        labels = _renderable_labels(spec.get("steps"))
        if not title and not labels:
            return base_prompt
        text_part = f"画面顶部呈现标题文字「{title}」" if title else ""
        if labels:
            joined = "、".join(f"「{lb}」" for lb in labels)
            text_part += ("，下方呈现带数字序号的分步标签：" if text_part else "画面呈现带数字序号的分步标签：") + joined + "，各步以 1/2/3 序号标注"
    else:
        return base_prompt
    layout = (layout_card or "").strip() or _DEFAULT_LAYOUT_CARD
    # 偏离项 4（2026-08-19）：注入后总长守护——超 Ark seedream ≤300 汉字预算时
    # 裁 base 描述尾部（保头部主体层），图内文字指令+版式卡逐字保留（graphic 帧
    # 的全部意义），并 warning 可观测。极端组合（base 180+版式卡 60+文字指令）
    # 修前会静默超发，Ark 端行为未定义（可能 400 整帧失败）。
    tail = f"。{text_part}{_RENDER_TAIL}{_LAYOUT_PREFIX}{layout}"
    budget = _ARK_PROMPT_BUDGET_UNITS - _prompt_units(tail)
    if budget < 0:  # 结构性护栏兜底：尾缀固有开销 <300（test 锁定），负数仅防御
        budget = 0
    if _prompt_units(base_prompt) > budget:
        original_units = _prompt_units(base_prompt)
        base_prompt = _trim_to_units(base_prompt, budget)
        logger.warning(
            "graphic inject: prompt 超 Ark seedream 预算（base %d>%d 单位，版式尾缀 %d 单位）——裁 base 描述尾部至 %d 字符，图内文字指令+版式卡逐字保留",
            original_units,
            budget,
            _prompt_units(tail),
            len(base_prompt),
        )
    return f"{base_prompt}{tail}"

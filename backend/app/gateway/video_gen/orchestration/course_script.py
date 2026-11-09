"""remotion_course 独立管线解析器（2026-09-10 独立管线计划 T2）。

职责：课程脚本 → 结构化课程单元（CourseUnit）。**不复用** ``parse_script_to_frames``
（那是 ai_image/ai_video 的细切口径，保持原样供既有类型）——本模块是 remotion_course
专属分支，ai_image/ai_video 管线零触达。

产出链：``parse_course_script``（faithful|rewrite 双档 prompt）→ ``validate_faithfulness``
（数字等值/覆盖率/防扩写，失败带更严 prompt 整体重试一次）→ ``split_overflow_units``
（容量契约：bullets>4 拆连续单元）→ ``gate_meta_frames``（元帧门槛）→
``units_to_frames``（layout/layout_content/嵌入图/visual_source 一次带齐）。

关键契约（计划 §3）：
- 版式白名单 = REMOTION_LAYOUTS 减去 VideoClipPage（parser 永不自动指派，决策 9）。
- 嵌入图帧只写 local_visual_path（visual_source 保持 None）——与 upload-visual 端点
  约定逐字一致；消费方据此判定"已有素材"。
- ``visual_source="ai_video"`` 只能由用户经编辑器设置，本模块**永不产出**。
- FullVisualPage 仅在有嵌入图/有配图词时自动指派（决策 11 收窄：全屏占位刺目）。
- 标题/列表契约（决策 8 + 决策 17 裁决）：标题进 content.title 不逐字念；列表要点进
  content.bullets 且**完整内容仍在旁白念出**（决策 17："不删减"承诺的对象是旁白）。
  覆盖率基线据此剥离标题/页码/列表标记、保留列表正文。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.gateway.video_gen.orchestration import _invoke_llm_bounded
from app.gateway.video_gen.orchestration.script import _LENS_KEYWORDS, _chunk_text, _extract_json_from_text
from app.gateway.video_gen.storyboard import REMOTION_LAYOUTS, SceneGroup, StoryboardFrame

logger = logging.getLogger(__name__)

# ── 版式白名单与字段契约（计划 §3.3，与模板 src/content.tsx SceneContent 对齐）──

# 18 版式白名单：REMOTION_LAYOUTS 减去 VideoClipPage（parser 永不自动指派，决策 9）。
COURSE_UNIT_LAYOUTS: tuple[str, ...] = tuple(name for name in REMOTION_LAYOUTS if name != "VideoClipPage")

# 图槽版式集合（计划 §3.4）：只有这些版式的帧才可能生成 AI 配图。
IMAGE_SLOT_LAYOUTS: frozenset[str] = frozenset({"ImageTextLeft", "ImageTextRight", "QuotePage", "EndingPage", "FullVisualPage"})

# 版式 ↔ content 字段映射（SceneContent 是共享扁平接口；此处为该版式组件实际读取的子集）。
# layout_content 永不承载图片数据——图槽素材走 local_visual_path/visual_url 通道。
LAYOUT_CONTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "CoverPage": ("title", "subtitle", "brandName", "courseNo", "series", "instructor"),
    "ObjectivesPage": ("kicker", "title", "bullets"),
    "OutlinePage": ("kicker", "title", "chapters", "currentChapter"),
    "ChapterDivider": ("chapterNo", "chapterTotal", "title", "lead"),
    "ConceptPage": ("kicker", "title", "lead", "bullets"),
    "ImageTextLeft": ("kicker", "title", "bullets"),
    "ImageTextRight": ("kicker", "title", "bullets"),
    "FullVisualPage": ("kicker", "title", "subtitle", "note"),
    "VideoClipPage": ("title", "badge"),
    "ComparisonPage": ("kicker", "title", "leftLabel", "rightLabel", "left", "right"),
    "ChartPage": ("kicker", "title", "chart", "headline", "conclusion", "note"),
    "ProcessPage": ("kicker", "title", "steps", "currentStep"),
    "CodePage": ("kicker", "title", "fileName", "code", "bullets"),
    "QuotePage": ("quote", "source"),
    "CasePage": ("kicker", "title", "cards"),
    "SummaryPage": ("kicker", "title", "bullets"),
    "QuizPage": ("kicker", "title", "question", "options"),
    "EndingPage": ("kicker", "recap", "nextLabel", "nextTitle", "nextSubtitle", "cta"),
    "EndCard": ("brandName", "title", "follow", "cta", "copyright"),
}

# 解析模式（计划 §3.5）
COURSE_PARSE_MODES = ("faithful", "rewrite")

# 容量上限（决策 17）：title ≤16 字、bullets ≤4 条（单条 ≤24 字由 prompt 约束，
# 机器侧单条超长只留痕不截断——省略号截断仅 kicker/badge 等装饰字段兜底）。
TITLE_MAX_CHARS = 16
BULLETS_MAX_ITEMS = 4

# 视觉重生/生成侧消费的 degradation kind（字符串字面量——degradations.py 不做枚举
# 校验，课程管线不改动共享文件）。
KIND_COURSE_PARSE_FALLBACK = "course_parse_fidelity_fallback"
KIND_LAYOUT_OVERFLOW_RISK = "layout_overflow_risk"
KIND_EMBEDDED_IMAGES_EXPIRED = "embedded_images_expired"
KIND_EMBEDDED_IMAGE_DEFERRED = "embedded_image_deferred"

_IMAGE_MARKER_RE = re.compile(r"【\s*图片\s*(\d+)\s*】")

# 条目 17（2026-09-12 缺陷批）：结构性画面标记——直贴脚本【画面：…】/行首
# 场景N：/镜头：/旁白：等不得进旁白被 TTS 朗读（修前只剥【图片N】，【画面：城市
# 夜景】整句进 TTS）。与上传路径 _VISUAL_MARKER_PATTERNS（orchestration/script.py）
# 对齐：「【…】包围内容一律不进旁白」是既定产品语义（上传路径首条即无白名单剥
# 全部【…】，「【课程目标】」类内容今天就会被剥——直贴路径对齐属一致性修复）。
# 【图片N】是 【…】 的子集：消费方必须先 findall 提取图槽再剥。
# 复审二轮（2026-09-13）补齐子集差：括号镜头标记（（特写镜头）/(close-up)）、
# **粗体**、行首 [标签]、markdown 标题、公众号元信息行、裸页码——上传路径已在
# 剥、直贴路径漏剥的同类标记。场景/镜头行首标签保留 \d*（比上传路径 \d+ 略宽：
# 无编号的裸「场景：」标签同样是标记）。
_STRUCTURAL_MARKER_PATTERNS = [
    re.compile(r"【[^】]*】[ \t]*"),
    re.compile(rf"（[^）]*(?:{_LENS_KEYWORDS})[^）]*）[ \t]*"),
    re.compile(rf"\([^)]*(?:{_LENS_KEYWORDS})[^)]*\)[ \t]*"),
    re.compile(r"\*\*[^*]+\*\*[：:]?\s*"),
    re.compile(r"^\s*场景\s*\d*\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*镜头\s*\d*\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*(?:旁白|解说词?|画外音|画面描述|场景描述)\s*[：:]\s*", re.MULTILINE),
    re.compile(r"^\s*\[[^\]]*\]\s*", re.MULTILINE),
    re.compile(r"^\s*#{1,6}\s+.*$", re.MULTILINE),
    re.compile(r"^\s*(?:作者|阅读|赞赏|广告|来源|扫码|关注|点击)[：:].*", re.MULTILINE),
    re.compile(r"第\s*\d+\s*页"),
]


def _strip_structural_markers(text: str) -> str:
    """剥结构性画面标记（条目 17）。消费方：_sanitize_units / units_from_paragraphs /
    strip_image_markers / _narrative_baseline 四处必须同集——校验基线与产出侧对称
    剥离，否则标记内数字（如【2024年报】的 2024）会被判数字缺失误杀重试。"""
    for pat in _STRUCTURAL_MARKER_PATTERNS:
        text = pat.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", text)


def _strip_content_markers(content: Any) -> Any:
    """P3-2 / BUG-002（2026-09-14）：content 字段值按类型递归剥标记。

    修前只剥 narration，content（title/bullets/steps/instructor 等）值未剥——
    AI 解析路径（faithful/rewrite）任一处携带【图片N】/【画面：x】会直进成片版式。
    按类型递归而非字段逐一列举（防未来新增字段漏网）：
    - ``code`` 键整键跳过（CodePage 代码内容合法含【】）；
    - str → _strip_structural_markers；list → 逐元素递归；dict → 递归；
    - 其他类型（number/bool）原样。
    """
    if isinstance(content, dict):
        return {k: (v if k == "code" else _strip_content_markers(v)) for k, v in content.items()}
    if isinstance(content, list):
        return [_strip_content_markers(x) for x in content]
    if isinstance(content, str):
        return _strip_structural_markers(content)
    return content


# ── CourseUnit 模型（计划 §3.1）────────────────────────────────────────────


class CourseUnit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    layout: str | None = None  # None = 走确定性规则表（非法值剔除/兜底路径）
    narration: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    chapter: str | None = None
    image_ref: str | None = None  # 文档内嵌图片引用（"img_N"）——一单元多图时为首图
    # 一单元多图（§3.6）：除首图外的标记暂存于此，``defer_extra_images`` 顺延到
    # 后续无图单元（不在本模块落帧——留给 service 编排 + 留痕）。
    image_refs_extra: list[str] = Field(default_factory=list)
    # 顺延目标单元标注（"来自邻近单元的文档配图"）——随 units_to_frames 落到帧。
    image_note: str | None = None
    visual_prompt: str = ""  # 信息图导向配图词；空 = 不生成（占位渲染）


# ── prompt（双档 + 公共规则，计划 §4.1）────────────────────────────────────

_MODE_RULES: dict[str, str] = {
    "faithful": ("【保真档】narration = 原文句子的轻优化拼接：清理口头语、书面语转口语、顺句子；不改大意、不改数据、不扩写、不删减；数字保持原文书写形式；单元边界服从原文段落/小节结构。"),
    "rewrite": ("【改写档】narration = 改写为口语讲解稿，每单元 60-160 字连贯口播段落（禁止一句一镜）；保留全部要点/数据/结论，允许重组句式与详略；数字保持原文书写形式。"),
}


def _field_table_prompt() -> str:
    # 字段表只列 18 个可选版式（VideoClipPage 不自动指派——决策 9，仅用户手切）
    return "\n".join(f"- {name}: {', '.join(fields)}" for name, fields in LAYOUT_CONTENT_FIELDS.items() if name in COURSE_UNIT_LAYOUTS)


def _build_system_prompt(mode: str, *, target_units: int | None = None, failure_reason: str | None = None) -> str:
    layout_guide = (
        "数据/统计→ChartPage；步骤/流程→ProcessPage；对比/对照→ComparisonPage；概念讲解→ConceptPage；"
        "章节开始→ChapterDivider；目标总览→ObjectivesPage；目录→OutlinePage；引述→QuotePage；"
        "案例→CasePage；代码→CodePage；测验→QuizPage；总结→SummaryPage；收尾→EndingPage/EndCard；"
        "图文页→ImageTextLeft/ImageTextRight；全幅图→FullVisualPage（仅当本单元有嵌入图 image_ref "
        "或确需生成配图时才可选，其余内容不要选它）"
    )
    target_hint = f"约 {target_units} 个单元（软提示，按内容结构自适应）。" if target_units else "按内容结构自适应。"
    parts = [
        "你是课程视频分镜解析器。把课程脚本切分为结构化的版式分镜单元。",
        _MODE_RULES.get(mode, _MODE_RULES["faithful"]),
        f"【切镜】按内容结构切分语义单元——一个知识点/一个版式承载单元为一镜；{target_hint}",
        f"【版式】layout 只能从以下 {len(COURSE_UNIT_LAYOUTS)} 个版式中选择（禁止其他值，禁止 VideoClipPage）。\n选型指南：{layout_guide}",
        f"【内容字段】content 按版式产出结构化字段（字段白名单）：\n{_field_table_prompt()}\n容量约束：title ≤{TITLE_MAX_CHARS}字；bullets ≤{BULLETS_MAX_ITEMS}条、单条 ≤24字；超出时拆分为多个连续单元（同版式、同 chapter）。",
        "【标题与列表】标题与列表要点写入 content（进画面）；旁白念正文——列表要点的完整内容也要在旁白中念出（画面装不下时截句，旁白不删减），标题不逐字念。",
        "【图片标记】原文中的【图片N】是文档内嵌图片的位置标记——不得写入 narration；"
        '把标记就近归入所在单元的 image_ref（值如 "img_1"，一单元多标记只填首图——'
        "系统会把其余顺延到后续无图单元）；带图单元优先 ImageTextLeft/"
        "ImageTextRight/FullVisualPage。",
        "【配图词】visual_prompt 仅在同时满足时给出：本单元版式有图槽、无 image_ref、"
        "当页内容用 ChartPage/ProcessPage/ComparisonPage 等原生版式无法表达、确实需要画面。"
        "描述一张承载当页逻辑结构（要点关系/流程/对比/数据）的示意图，不是装饰性背景图；"
        "≤50字；宁可不留。",
        "【章节】chapter 给出所属章节标题（无明确章节可不填）；同章连续单元 chapter 相同。",
        "【数字】数字保持原文书写形式（阿拉伯数字/百分号不改写为中文）。",
        '输出 STRICT JSON: {"units":[{"layout":"...","narration":"...","content":{},"chapter":null,"image_ref":null,"visual_prompt":""}]}，不要输出其他文字。',
    ]
    if failure_reason:
        parts.insert(1, f"【重要】上一次输出未通过机器校验，原因：{failure_reason}。请更严格地遵守上述规则重新输出。")
    return "\n".join(parts)


# ── 解析主入口 ─────────────────────────────────────────────────────────────


async def parse_course_script(
    llm,
    text: str,
    *,
    mode: str = "faithful",
    target_units: int | None = None,
    on_progress=None,
) -> list[CourseUnit] | None:
    """remotion_course 专属解析：脚本 → 结构化单元（faithful|rewrite 双档）。

    - 分块复用 ``_chunk_text``；每块一次 LLM 调用；``on_progress(done, total)`` 沿用
      5→20 区间刷活性（与 parse_script_to_frames 同约定）。
    - 块级失败 → 该块落 ``units_from_paragraphs`` 兜底（不静默丢内容）。
    - 汇总后 ``validate_faithfulness``；不达标 → 带更严 prompt（附失败原因）整体重试
      一次；仍不过 → 返回 None（调用方落兜底 + degradation 留痕
      ``course_parse_fidelity_fallback``）。
    - 成功路径在返回前做超容量单元拆分（``split_overflow_units``）。
    """
    if mode not in COURSE_PARSE_MODES:
        mode = "faithful"
    chunks = _chunk_text(text)
    failure_reason: str | None = None
    for attempt in range(2):
        units: list[CourseUnit] = []
        llm_units_any = False
        for i, chunk in enumerate(chunks):
            chunk_units, chunk_llm_ok = await _parse_chunk(llm, chunk, mode=mode, target_units=target_units, failure_reason=failure_reason)
            if chunk_llm_ok:
                llm_units_any = True
            units.extend(chunk_units)
            if on_progress is not None:
                await on_progress(i + 1, len(chunks))
        if not units or not llm_units_any:
            return None
        reasons = _check_failures(units, text, mode)
        if not reasons:
            return units  # 超容量拆分/元帧门槛由调用方编排（split_overflow_units/gate_meta_frames）
        failure_reason = "；".join(reasons)
        logger.warning("course_script parse attempt %d failed validation: %s", attempt + 1, failure_reason)
    return None


async def _parse_chunk(
    llm,
    chunk: str,
    *,
    mode: str,
    target_units: int | None,
    failure_reason: str | None,
) -> tuple[list[CourseUnit], bool]:
    """单块解析。返回 (units, llm_ok)；llm_ok=False 表示本块 LLM 失败（已落兜底）。"""
    user = f"文本：\n{chunk}"
    try:
        resp = await _invoke_llm_bounded(
            llm,
            [
                {"role": "system", "content": _build_system_prompt(mode, target_units=target_units, failure_reason=failure_reason)},
                {"role": "user", "content": user},
            ],
        )
        content = resp.content if hasattr(resp, "content") else str(resp)
        data = _extract_json_from_text(content)
        if not data or not isinstance(data.get("units"), list):
            raise ValueError("course parse: no units in LLM response")
        raw_units = [u for u in data["units"] if isinstance(u, dict)]
        units = _sanitize_units(raw_units)
        if not units:
            raise ValueError("course parse: all units dropped after sanitization")
        return units, True
    except Exception:
        logger.warning("course_script chunk parse failed, falling back to paragraphs", exc_info=True)
        return units_from_paragraphs(chunk), False


def _sanitize_units(raw_units: list[dict]) -> list[CourseUnit]:
    """3.1 校验规则：layout 白名单 / content 字段白名单 / 图片标记归位 / 空 narration 剔除。"""
    out: list[CourseUnit] = []
    for raw in raw_units:
        narration = str(raw.get("narration") or "").strip()
        # 图片标记：只进 image_ref/image_refs_extra，不进 narration（不进 TTS）。
        # 一单元多图（§3.6）：首图进 image_ref，其余进 extras 待顺延（不静默丢）。
        # 条目 17（2026-09-12 缺陷批）：先提取【图片N】再剥全部结构性标记——
        # 【图片N】是 【…】 全量模式的子集，顺序不可反。
        markers = _IMAGE_MARKER_RE.findall(narration)
        narration = _strip_structural_markers(narration).strip()
        image_ref = raw.get("image_ref") or None
        if not image_ref and markers:
            image_ref = f"img_{markers[0]}"
        extras = [f"img_{m}" for m in markers[1:]]
        layout = raw.get("layout") or None
        if layout not in COURSE_UNIT_LAYOUTS:
            layout = None  # 非法值剔除 → 确定性规则表
        # FullVisualPage 收窄（决策 11）：无嵌入图且无配图词 → 降 ConceptPage（全屏占位刺目）。
        # 在 content 白名单过滤前判定——降级后 content 按 ConceptPage 字段集过滤。
        if layout == "FullVisualPage" and not image_ref and not str(raw.get("visual_prompt") or "").strip():
            layout = "ConceptPage"
        content_raw = raw.get("content") if isinstance(raw.get("content"), dict) else {}
        content: dict[str, Any] = {}
        if layout:
            allowed = LAYOUT_CONTENT_FIELDS.get(layout, ())
            content = {k: v for k, v in content_raw.items() if k in allowed}
        # P3-2 / BUG-002：白名单过滤后统一递归剥离（title/bullets/steps 嵌套值同治）
        content = _strip_content_markers(content)
        # visual_prompt 机器侧门控：仅图槽 + 无嵌入图才保留
        visual_prompt = ""
        if layout in IMAGE_SLOT_LAYOUTS and not image_ref:
            visual_prompt = str(raw.get("visual_prompt") or "").strip()[:50]
        if not narration:
            continue  # 空 narration 单元剔除（图片标记剥完无正文且无先前单元可并）
        chapter = str(raw.get("chapter")).strip() if raw.get("chapter") else None
        out.append(
            CourseUnit(
                layout=layout,
                narration=narration,
                content=content,
                chapter=chapter,
                image_ref=image_ref,
                image_refs_extra=extras,
                visual_prompt=visual_prompt,
            )
        )
    return out


# ── 确定性兜底（零 LLM）────────────────────────────────────────────────────


def units_from_paragraphs(text: str, image_map: dict | None = None) -> list[CourseUnit]:
    """确定性兜底：段落直切，每段一个 narration-only 单元。

    - layout 不写（→ 确定性规则表）、content 空（→ _layout_props 确定性推导）。
    - 段落含 ``【图片N】`` → 剥标记 + 记 image_ref；纯标记段落的图归并到前一单元。
    - 兜底路径零生图（visual_prompt 全空）。
    - ``image_map`` 仅为签名对称保留：路径解析在 ``units_to_frames``（消费 image_paths）。
    """
    del image_map
    units: list[CourseUnit] = []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(paragraphs) <= 1 and "\n" in (text or ""):
        # 2026-09-11 二轮审查 M2：docx 提取的段落以**单 \n** 连接（无空行），空行
        # 切分会把整脚本塌缩成 1 个巨型单元（叠加 P4 跳过拆帧守卫 → 单帧巨段 TTS、
        # 多图顺延无处去）。≤1 段且含单换行时回落行级切分（split_fixed_script
        # sentence 模式同口径）。
        paragraphs = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    else:
        # 条目 16（2026-09-12 缺陷批）：≥2 段分支同样防塌缩——逐段判定，段内行数 >1
        # 即按行级+句末标点切分（split_fixed_script sentence 模式同口径）。修前该
        # 分支整段并一个单元：docx 混合结构（空行分 3 段×段内 5 行）塌成 3 个巨型
        # 单元，旁白/配图粒度全失。单行段落不动（与既有口径一致，不引入句级碎帧）。
        expanded: list[str] = []
        for para in paragraphs:
            lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
            if len(lines) > 1:
                for ln in lines:
                    expanded.extend(_line_sentence_parts(ln))
            else:
                expanded.append(para)
        paragraphs = expanded
    for para in paragraphs:
        markers = _IMAGE_MARKER_RE.findall(para)
        narration = _strip_structural_markers(para).strip()
        if not narration:
            # 纯图片段：首图归并到前一单元（无前一单元/前已有图则不入 image_ref），
            # 全部标记进 extras 交给顺延（§3.6——修前首图之外静默丢弃）。
            if markers and units:
                prev = units[-1]
                if not prev.image_ref:
                    units[-1] = prev.model_copy(
                        update={
                            "image_ref": f"img_{markers[0]}",
                            "image_refs_extra": [*prev.image_refs_extra, *(f"img_{m}" for m in markers[1:])],
                        }
                    )
                else:
                    # 前一单元已有图：全部标记进 extras 顺延（修前静默丢弃）
                    units[-1] = prev.model_copy(update={"image_refs_extra": [*prev.image_refs_extra, *(f"img_{m}" for m in markers)]})
            continue
        units.append(
            CourseUnit(
                layout=None,
                narration=narration,
                content={},
                image_ref=f"img_{markers[0]}" if markers else None,
                image_refs_extra=[f"img_{m}" for m in markers[1:]],
            )
        )
    return units


# ── 一单元多图顺延（§3.6）──────────────────────────────────────────────────


def defer_extra_images(units: list[CourseUnit]) -> tuple[list[CourseUnit], list[dict]]:
    """首图进槽、其余顺延到后续无图单元（计划 §3.6，2026-09-11 审查补交付）。

    - 顺延目标 = 源单元之后第一个 ``image_ref`` 为空且未被占用的单元；目标记
      ``image_note = "来自邻近单元的文档配图"``（随 units_to_frames 落帧，编辑器
      素材区标注供用户纠正）。
    - 之后无无图单元 → 该图丢弃（deferral 记 ``to_pos=None``，调用方留痕可见）。
    - 就地改写 ``units`` 并返回；deferral = {ref, from_pos, to_pos}。
    """
    deferrals: list[dict] = []
    pending: list[tuple[int, str]] = []
    for i, u in enumerate(units):
        for ref in u.image_refs_extra:
            pending.append((i, ref))
        if u.image_refs_extra:
            units[i] = u.model_copy(update={"image_refs_extra": []})
    taken: set[int] = set()  # 已被顺延占用的目标单元位
    for src_pos, ref in pending:
        target: int | None = None
        for j in range(src_pos + 1, len(units)):
            if j in taken or units[j].image_ref:
                continue
            target = j
            break
        if target is None:
            deferrals.append({"ref": ref, "from_pos": src_pos, "to_pos": None})
            continue
        taken.add(target)
        units[target] = units[target].model_copy(update={"image_ref": ref, "image_note": "来自邻近单元的文档配图"})
        deferrals.append({"ref": ref, "from_pos": src_pos, "to_pos": target})
    return units, deferrals


# ── 容量契约（决策 17）─────────────────────────────────────────────────────


def split_overflow_units(units: list[CourseUnit]) -> tuple[list[CourseUnit], list[dict]]:
    """超容量单元拆分（确定性后处理）。

    - bullets >4 条 → 拆连续单元（同版式、同 chapter 承接），拆出单元的 narration
      按句界自然分段。
    - title 超长**不拆不截**（省略号截断仅装饰字段兜底）：返回 overflow 明细供调用方
      ``layout_overflow_risk`` warn 级留痕（不触发兜底）。
    返回 (新 units, overflow 明细列表)。
    """
    out: list[CourseUnit] = []
    overflow: list[dict] = []
    for pos, u in enumerate(units):
        bullets = u.content.get("bullets") if isinstance(u.content, dict) else None
        if u.layout and isinstance(bullets, list) and len(bullets) > BULLETS_MAX_ITEMS:
            chunk_count = -(-len(bullets) // BULLETS_MAX_ITEMS)
            # 句数不足时收缩拆分数（2026-09-11 审查 M3）：句子 < 组数会把第 N 组
            # narration 分空 → 空旁白帧进 TTS = 1s 静音内容帧且无留痕。收缩后剩余
            # bullets 并入末组，>4 溢出走 layout_overflow_risk 留痕（可见、不静默）。
            if (u.narration or "").strip():
                n_sentences = len([s for s in _SENT_SPLIT_RE.split(u.narration) if s.strip()])
                chunk_count = min(chunk_count, max(1, n_sentences))
            # 末组吸收剩余（收缩拆分数后余量全进末组）
            chunks = [bullets[i * BULLETS_MAX_ITEMS : ((i + 1) * BULLETS_MAX_ITEMS if i < chunk_count - 1 else len(bullets))] for i in range(chunk_count)]
            segs = _split_narration_segments(u.narration, len(chunks))
            for j, bullet_chunk in enumerate(chunks):
                content = dict(u.content)
                content["bullets"] = bullet_chunk
                # 条目 15（2026-09-12 缺陷批）：首子单元保留 image_ref/image_note/
                # visual_prompt；其余子单元清空——修前 model_copy 全字段继承，N 个
                # 连续子帧同一 local_visual_path（多帧同图）、图槽版式每子帧重复
                # 生图 N 次（生成费浪费）。defer_extra_images 先于 split（extras 已
                # 清空），首帧嵌入图保留不受影响。
                update: dict = {
                    "content": content,
                    "narration": segs[j] if j < len(segs) else (segs[-1] if segs else u.narration),
                }
                if j > 0:
                    update.update({"image_ref": None, "image_note": None, "visual_prompt": None})
                out.append(u.model_copy(update=update))
            if len(chunks[-1]) > BULLETS_MAX_ITEMS:
                overflow.append({"frame_unit_pos": pos, "layout": u.layout, "issue": "bullets_overflow_unsplit", "bullets": len(chunks[-1])})
        else:
            out.append(u)
        title = u.content.get("title") if isinstance(u.content, dict) else None
        if isinstance(title, str) and len(title) > TITLE_MAX_CHARS:
            overflow.append({"frame_unit_pos": pos, "layout": u.layout, "issue": "title_overflow", "title": title})
    return out, overflow


_SENT_SPLIT_RE = re.compile(r"[。！？；\n]+")


def _line_sentence_parts(line: str) -> list[str]:
    """行内按句末标点切分（条目 16；split_fixed_script sentence 模式同口径）。

    每句（含句末标点）一段；行尾无标点的余数保留为一段；整行无句末标点 → 原样一段。
    """
    parts = re.findall(r"[^。！？!?]*[。！？!?]", line)
    out = [p.strip() for p in parts if p.strip()]
    remainder = line[len("".join(parts)) :].strip()
    if remainder:
        out.append(remainder)
    return out or [line.strip()]


def _split_narration_segments(narration: str, n: int) -> list[str]:
    """把 narration 按句界尽量均分为 n 段（供拆单元后 narration 自然分段）。"""
    sentences = [s for s in _SENT_SPLIT_RE.split(narration or "") if s.strip()]
    if n <= 1 or not sentences:
        return [narration or ""]
    total = len(sentences)
    per = max(1, total // n)
    segs: list[str] = []
    idx = 0
    for i in range(n):
        take = per + (1 if i < total % n else 0)
        seg = [s for s in sentences[idx : idx + take] if s.strip()]
        idx += take
        segs.append("。".join(seg) + ("。" if seg else ""))
    return segs


# ── 元帧门槛（决策 18，确定性后处理）───────────────────────────────────────


def gate_meta_frames(units: list[CourseUnit]) -> list[CourseUnit]:
    """元帧门槛：防短脚本注水。

    - 总单元 <8 → ObjectivesPage/OutlinePage 降级 ConceptPage；
    - 章节 <3 → OutlinePage 降级 ConceptPage；
    - ChapterDivider 需 单元 ≥10 且章节 ≥2，否则降级 ConceptPage。
    降级只改 layout、保留 content——用户可在编辑器手切恢复。阈值为 v1 默认值，实测可调。
    """
    n = len(units)
    n_chapters = len({u.chapter for u in units if u.chapter})
    out: list[CourseUnit] = []
    for u in units:
        layout = u.layout
        if layout in ("ObjectivesPage", "OutlinePage") and n < 8:
            layout = "ConceptPage"
        elif layout == "OutlinePage" and n_chapters < 3:
            layout = "ConceptPage"
        elif layout == "ChapterDivider" and not (n >= 10 and n_chapters >= 2):
            layout = "ConceptPage"
        if layout != u.layout:
            out.append(u.model_copy(update={"layout": layout}))
        else:
            out.append(u)
    return out


# ── 帧构建（计划 §3.6 / §4.1）──────────────────────────────────────────────


def units_to_frames(
    units: list[CourseUnit],
    image_paths: dict[str, str] | None = None,
) -> tuple[list[StoryboardFrame], list[SceneGroup]]:
    """单元 → (frames, scene_groups)。

    - 写 narration/layout/layout_content。
    - ``image_ref`` 命中 image_paths → ``local_visual_path``（visual_source 保持 None，
      与 upload-visual 端点约定一致）；未命中 → 留空（占位渲染）。
    - visual_prompt 非空且版式 ∈ 图槽且无 local_visual → visual_source="ai_image" +
      image_prompt=visual_prompt；否则两字段留空。
    - **永不写 ai_video**（决策 9：只能用户经编辑器显式设置）。
    - 按 chapter 聚 SceneGroup（同章连续）；无 chapter 不建组（编辑器允许无分组）。
    """
    image_paths = image_paths or {}
    frames: list[StoryboardFrame] = []
    for i, u in enumerate(units):
        f = StoryboardFrame(
            index=i,
            narration=u.narration,
            layout=u.layout,
            layout_content=u.content or None,
            image_note=u.image_note,
        )
        local = image_paths.get(u.image_ref) if u.image_ref else None
        if local:
            f.local_visual_path = local
            f.visual_type = "image"  # visual_source 保持 None（3.1 约定）
        elif u.visual_prompt and u.layout in IMAGE_SLOT_LAYOUTS:
            f.visual_source = "ai_image"
            f.image_prompt = u.visual_prompt
        frames.append(f)

    groups: list[SceneGroup] = []
    i = 0
    while i < len(units):
        chapter = units[i].chapter
        if not chapter:
            i += 1
            continue
        j = i
        while j + 1 < len(units) and units[j + 1].chapter == chapter:
            j += 1
        groups.append(
            SceneGroup(
                index=len(groups),
                # P3-2 / BUG-002：章节标题（进 ChapterDivider 版式）同样剥标记
                title=_strip_structural_markers(chapter),
                frame_indices=list(range(i, j + 1)),
            )
        )
        i = j + 1
    return frames, groups


# ── 保真校验（计划 §3.5）───────────────────────────────────────────────────

# 中文数字解析表
_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10**4, "亿": 10**8}
_CN_NUM_CHARS = set("零一两二三四五六七八九十百千万亿点")

_ARABIC_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)(万|亿)?")
_CN_NUM_RUN_RE = re.compile(r"[零一两二三四五六七八九十百千万亿点]{2,}")
_PERCENT_CN_RE = re.compile(r"百分之([零一两二三四五六七八九十百千万亿点\d.]+)")
_PERCENT_AR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")


def _fmt_value(v: float) -> str:
    if v == int(v):
        return str(int(v))
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s


def _parse_cn_integer(s: str) -> float | None:
    """中文整数串 → 数值（十百千节 + 万亿大节）。"三千五百万"→35000000。

    纯数字位串（无单位字符，如"二零二四"/"一三"）按位值拼接（年份/编号书写）——
    加法口径会把"二零二四"读成 4（2026-09-11 审查 Minor）。
    """
    if len(s) > 1 and all(ch in _CN_DIGIT for ch in s):
        v = 0
        for ch in s:
            v = v * 10 + _CN_DIGIT[ch]
        return float(v)
    total = 0.0
    section = 0.0
    current = 0.0
    for ch in s:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if current == 0.0:
                current = 1.0  # "十" 开头
            section += current * unit
            current = 0.0
        elif ch in _CN_BIG:
            big = _CN_BIG[ch]
            section = (section + current) or 1.0
            total += section * big
            section = 0.0
            current = 0.0
        else:
            return None
    return total + section + current


def _parse_cn_number(s: str) -> float | None:
    """中文数字串 → 数值（支持 点 小数、十百千节、万亿大节）。

    "二十"→20；"三千五百"→3500；"三千五百万"→35000000；"一点五"→1.5；
    "一点五亿"→150000000（点后可带末尾万亿单位）；解析失败 → None。
    """
    if not s:
        return None
    if "点" not in s:
        return _parse_cn_integer(s)
    int_part, _, rest = s.partition("点")
    mult = 1.0
    if rest.endswith("亿"):
        mult, rest = 10**8, rest[:-1]
    elif rest.endswith("万"):
        mult, rest = 10**4, rest[:-1]
    base = _parse_cn_integer(int_part)
    if base is None:
        return None
    dec = 0.0
    scale = 0.1
    for ch in rest:
        if ch not in _CN_DIGIT:
            return None
        dec += _CN_DIGIT[ch] * scale
        scale /= 10
    return (base + dec) * mult


def _extract_numbers(text: str) -> set[str]:
    """提取数字表达并归一（v3 修订：百分之二十 ↔ 20%、一点五亿 ↔ 1.5亿 等值）。

    过滤噪声：单个数字（0-9 及中文单字）不参与比对（"三个要点"这类流程词）；
    纯中文 run 须 ≥2 字（避免"万事""十分"误判）。轻量归一器，不引新依赖；
    生僻书写（"三成半""翻番"）可能误判 → 有重试+兜底托底（计划 §9.3）。
    """
    out: set[str] = set()

    def _keep(value: float) -> bool:
        return value != int(value) or value >= 10

    # 阿拉伯数字（可带 万/亿 后缀）
    for m in _ARABIC_NUM_RE.finditer(text):
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        if m.group(2):
            value *= _CN_BIG[m.group(2)]
        if _keep(value):
            out.add(_fmt_value(value))
    # 中文数字 run（≥2 字，内部含万亿节）
    for m in _CN_NUM_RUN_RE.finditer(text):
        value = _parse_cn_number(m.group(0))
        if value is not None and _keep(value):
            out.add(_fmt_value(value))
    # 百分比（阿拉伯 + 中文）
    for m in _PERCENT_AR_RE.finditer(text):
        try:
            out.add(_fmt_value(float(m.group(1))) + "%")
        except ValueError:
            continue
    for m in _PERCENT_CN_RE.finditer(text):
        inner = m.group(1)
        value = _parse_cn_number(inner) if any(c in _CN_NUM_CHARS for c in inner) else None
        if value is None:
            try:
                value = float(inner)
            except ValueError:
                value = None
        # 百分比不套 _keep（2026-09-11 审查 M2）："提升5%"↔"提升百分之五"必须等值
        # ——个位数百分比被 _keep 过滤会让一侧缺失 → false-fail 落兜底（与阿拉伯
        # 百分比分支口径对齐——那边本就不筛）。
        if value is not None:
            out.add(_fmt_value(value) + "%")
    return out


def _narrative_baseline(text: str) -> str:
    """覆盖率基线：剥离标题/页码/列表标记，保留叙述性文本（与 prompt 口径一致）。

    - 剥离：markdown 标题行、纯页码行、第N页、≤16 字且无句末标点的短行（标题形态）、
      列表标记前缀（正文保留——决策 17：列表要点完整内容仍在旁白念出）。
    - 条目 17（2026-09-12 缺陷批）：先剥结构性画面标记（与产出侧同集）——产出侧
      parser/兜底剥掉【画面：…】后，基线若仍含标记内数字（如【2024年报】）会被
      校验①判「数字缺失」误杀重试/落兜底；两侧对称剥离后分母略缩（benign）。
    """
    text = _strip_structural_markers(text)
    lines: list[str] = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if re.fullmatch(r"\d+", s) or re.fullmatch(r"第\s*\d+\s*页", s):
            continue
        if s.startswith("#") and re.match(r"^#{1,6}\s+\S", s):
            continue
        s = re.sub(r"^\s*(?:\d+[.)、]|[-*•·])\s*", "", s).strip()
        if not s:
            continue
        if len(s) <= TITLE_MAX_CHARS and not re.search(r"[。！？；，、：,.!?;:]", s):
            continue  # 标题形态短行（不进旁白、不进基线）
        lines.append(s)
    return "\n".join(lines)


def _mask_numbers(text: str) -> str:
    """把数字表达（阿拉伯/中文/百分比）替换为占位符——覆盖率只量叙事文本。

    数字等值由校验①单独负责（"20%"↔"百分之二十"等值归一后须判 True，若 n-gram
    里保留数字字面，等价书写形式的差异会把覆盖率误杀）。
    """
    t = _PERCENT_CN_RE.sub("#", text or "")
    t = _PERCENT_AR_RE.sub("#", t)
    t = _ARABIC_NUM_RE.sub("#", t)
    t = _CN_NUM_RUN_RE.sub("#", t)
    return t


def _char_ngrams(text: str, n: int = 2) -> set[str]:
    cleaned = re.sub(r"[\s\W_]+", "", text or "")
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _ngram_recall(baseline: str, narration: str) -> float:
    base = _char_ngrams(_mask_numbers(baseline))
    if not base:
        return 1.0
    return len(base & _char_ngrams(_mask_numbers(narration))) / len(base)


def _check_failures(units: list[CourseUnit], source_text: str, mode: str) -> list[str]:
    """3.5 机器校验（确定性）。返回失败原因列表（空 = 通过）。"""
    reasons: list[str] = []
    narration_all = "".join(u.narration for u in units)
    baseline = _narrative_baseline(source_text)
    # ① 数字等值保留（归一后比对：缺失才判失败）
    missing = _extract_numbers(baseline) - _extract_numbers(narration_all)
    if missing:
        reasons.append(f"数字缺失或被改写：{sorted(missing)[:5]}")
    # ② 正文覆盖率 ≥90%（n-gram 比对）
    if baseline.strip():
        recall = _ngram_recall(baseline, narration_all)
        if recall < 0.9:
            reasons.append(f"正文覆盖率不足（{recall:.0%} < 90%）")
    # ③ faithful 档防扩写：narration 总字数 ≤ 输入正文 ×1.1
    if mode == "faithful" and baseline.strip() and len(narration_all) > len(baseline) * 1.1 + 10:
        reasons.append(f"疑似扩写（narration {len(narration_all)} 字 > 基线 {len(baseline)} 字 ×1.1）")
    return reasons


def validate_faithfulness(units: list[CourseUnit], source_text: str, mode: str = "faithful") -> bool:
    """3.5 三条校验（faithful 全三条；rewrite 前①②）。True = 通过。"""
    return not _check_failures(units, source_text, mode)


# ── 防御网 ─────────────────────────────────────────────────────────────────


def strip_image_markers(text: str) -> str:
    """任何路径进 TTS 前确保无 ``【图片N】``/结构性画面标记残留（仅 remotion_course
    分支调用，不碰共享 ``_strip_visual_markers``）。条目 17（2026-09-12 缺陷批）：
    与 parser/兜底/_narrative_baseline 同集剥离（【图片N】是 【…】 全量模式子集）。"""
    return _strip_structural_markers(text)

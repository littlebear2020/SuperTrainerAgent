"""remotion_course 独立管线 T2：orchestration/course_script.py 解析器。

覆盖计划 §7 第一组：
- JSON 提取/校验/非法 layout 剔除/content 字段白名单/分块/双档 prompt 差异（mock LLM）
- prompt 含信息图导向 + 分工原则 + FullVisualPage 收窄规则
- validate_faithfulness 正反用例（数字等值归一/覆盖率/扩写/轻优化）
- 校验失败 → 更严 prompt 重试一次 → 仍失败返回 None（调用方落兜底）
- 容量契约：bullets>4 拆连续单元、title 超长仅 warn（不触发兜底）
- 元帧门槛矩阵（单元 5/8/12 × 章节 1/2/4）
- units_to_frames：scene_groups 聚合 / image_ref→local_visual_path（visual_source
  保持 None）/ visual_source 赋值矩阵（永不产 ai_video）
- units_from_paragraphs 兜底 / strip_image_markers 防御网
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.gateway.video_gen.orchestration.course_script import (
    COURSE_UNIT_LAYOUTS,
    CourseUnit,
    gate_meta_frames,
    parse_course_script,
    split_overflow_units,
    strip_image_markers,
    units_from_paragraphs,
    units_to_frames,
    validate_faithfulness,
)


class _FakeLLM:
    """按序弹出响应的桩；记录收到的 messages 供 prompt 断言。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)


def _unit(**kw) -> dict:
    base = {"layout": "ConceptPage", "narration": "这是一个测试单元的旁白。", "content": {}, "chapter": None, "image_ref": None, "visual_prompt": ""}
    base.update(kw)
    return base


def _wrap(units: list[dict]) -> str:
    return "```json\n" + json.dumps({"units": units}, ensure_ascii=False) + "\n```"


# ── parse_course_script：JSON 提取 + 校验过滤 ──────────────────────────────


def test_parse_happy_path_filters_layout_and_content():
    source = "机器学习是人工智能的一个分支。它通过数据训练模型，让计算机具备预测能力。机器学习的应用非常广泛，覆盖了金融、医疗等多个领域。"
    resp = _wrap(
        [
            _unit(layout="NotALayout", narration="机器学习是人工智能的一个分支。", content={"title": "非法版式"}),
            _unit(content={"title": "概念讲解", "unknown_field": "应被丢弃"}, narration="它通过数据训练模型，让计算机具备预测能力。"),
            _unit(narration="机器学习的应用非常广泛，覆盖了金融、医疗等多个领域。【图片2】", image_ref=None),
        ]
    )
    llm = _FakeLLM([resp])
    units = asyncio.run(parse_course_script(llm, source))
    assert units is not None and len(units) == 3
    # 非法 layout 剔除 → None（走确定性规则表）
    assert units[0].layout is None
    assert units[0].content == {}  # 无版式 → content 白名单为空
    # content 字段白名单过滤（未知字段丢弃）
    assert units[1].content == {"title": "概念讲解"}
    # narration 残留 【图片N】 标记 → 就近归入 image_ref
    assert units[2].image_ref == "img_2"
    assert "【图片" not in units[2].narration


def test_parse_fullvisual_without_image_or_prompt_demoted():
    source = "深度学习的核心是神经网络。神经网络由多层神经元组成，逐层提取特征。"
    llm = _FakeLLM([_wrap([_unit(layout="FullVisualPage", visual_prompt="", narration=source, content={"title": "神经网络", "subtitle": "应被 ConceptPage 白名单过滤"})])])
    units = asyncio.run(parse_course_script(llm, source))
    # FullVisualPage 仅在有嵌入图/配图词时自动指派（决策 11 收窄）
    assert units[0].layout == "ConceptPage"
    # 降级后 content 按 ConceptPage 字段集过滤
    assert units[0].content == {"title": "神经网络"}


def test_parse_chunking_and_progress():
    from app.gateway.video_gen.orchestration.script import _chunk_text

    source = "段落一。" + "内容" * 1000 + "。\n\n" + "段落二。" + "内容" * 1000 + "。"
    chunks = _chunk_text(source)
    assert len(chunks) == 2  # 前提：两段各 ~2000 字 ≤3000 → 恰好 2 块
    # mock 逐块回显（真实解析形态：narration 覆盖本块内容）
    llm = _FakeLLM([_wrap([_unit(narration=chunks[0])]), _wrap([_unit(narration=chunks[1])])])
    seen: list[tuple[int, int]] = []

    async def on_progress(done, total):
        seen.append((done, total))

    units = asyncio.run(parse_course_script(llm, source, on_progress=on_progress))
    assert len(llm.calls) == 2  # 分块复用 _chunk_text
    assert seen == [(1, 2), (2, 2)]
    assert units is not None


def test_parse_prompt_dual_mode_and_common_rules():
    source = "神经网络是深度学习的基础结构。训练过程通过反向传播优化权重。"
    faithful = _FakeLLM([_wrap([_unit(narration=source)])])
    asyncio.run(parse_course_script(faithful, source, mode="faithful"))
    rewrite = _FakeLLM([_wrap([_unit(narration=source)])])
    asyncio.run(parse_course_script(rewrite, source, mode="rewrite"))

    f_sys = faithful.calls[0][0]["content"]
    r_sys = rewrite.calls[0][0]["content"]
    # 双档差异
    assert "不改大意" in f_sys and "不扩写" in f_sys
    assert "改写" in r_sys
    # 公共规则：信息图导向 + 分工原则 + FullVisualPage 收窄 + 图片标记契约
    for sys_prompt in (f_sys, r_sys):
        assert "示意图" in sys_prompt and "不是装饰性背景图" in sys_prompt
        assert "无法表达" in sys_prompt  # 分工原则：原生版式可表达 → 不给配图词
        assert "FullVisualPage" in sys_prompt and "仅当" in sys_prompt
        assert "【图片N】" in sys_prompt
        assert "- VideoClipPage" not in sys_prompt  # 18 白名单不含 VideoClipPage（字段表不可选）


def test_parse_retry_once_with_stricter_prompt_then_fallback_none():
    source = "本季度营收达到300万元，同比增长20%。用户规模突破50万人，留存率提升到85%。研发投入占比15%，共申请专利42项。"
    good = _wrap(
        [
            _unit(narration="本季度营收达到300万元，同比增长20%。用户规模突破50万人，留存率提升到85%。"),
            _unit(narration="研发投入占比15%，共申请专利42项。"),
        ]
    )
    # 第一次：数字被改（300万→500万）→ 校验失败；第二次：通过
    bad = _wrap(
        [
            _unit(narration="本季度营收达到500万元，同比增长20%。用户规模突破50万人，留存率提升到85%。"),
            _unit(narration="研发投入占比15%，共申请专利42项。"),
        ]
    )
    llm = _FakeLLM([bad, good])
    units = asyncio.run(parse_course_script(llm, source))
    assert units is not None
    assert len(llm.calls) == 2  # 重试恰好一次
    retry_sys = llm.calls[1][0]["content"]
    assert "未通过校验" in retry_sys or "更严格" in retry_sys

    # 两次都失败 → None（调用方落 units_from_paragraphs 兜底）
    llm2 = _FakeLLM([bad, bad])
    assert asyncio.run(parse_course_script(llm2, source)) is None
    assert len(llm2.calls) == 2  # 不做第三次重试


def test_parse_block_failure_falls_back_for_that_block():
    source = "第一段讲概念定义，内容完整充分。"
    llm = _FakeLLM([RuntimeError("llm down")])
    # 唯一块失败 → 无任何 LLM 单元 → None（整体兜底由调用方处理）
    assert asyncio.run(parse_course_script(llm, source)) is None


# ── validate_faithfulness ─────────────────────────────────────────────────


def _u(narration: str) -> CourseUnit:
    return CourseUnit(narration=narration)


def test_validate_light_optimization_passes():
    source = "本季度营收达到300万元，同比增长20%。用户规模突破50万人，留存率提升到85%。"
    units = [_u("本季度营收达到了300万元，同比增长20%。"), _u("用户规模突破50万人，留存率提升到85%。")]
    assert validate_faithfulness(units, source, "faithful") is True


def test_validate_digit_equivalence_normalization():
    source = "转化率提升20%，月活跃用户达到1.5亿人。"
    units = [_u("转化率提升百分之二十，月活跃用户达到一点五亿人。")]
    assert validate_faithfulness(units, source, "rewrite") is True


def test_validate_digit_changed_fails():
    source = "本季度营收达到300万元，同比增长20%。"
    units = [_u("本季度营收达到500万元，同比增长20%。")]
    assert validate_faithfulness(units, source, "faithful") is False


def test_validate_coverage_insufficient_fails():
    source = "机器学习是人工智能的一个分支，通过数据训练模型获得预测能力，已在金融、医疗、教育等多个行业落地，成为数字化转型的核心引擎。"
    units = [_u("机器学习很重要。")]  # 严重丢内容
    assert validate_faithfulness(units, source, "rewrite") is False


def test_validate_expansion_fails_faithful_only():
    source = "机器学习是人工智能的一个分支，通过数据训练模型。"
    expanded = _u("机器学习是人工智能的一个分支，通过数据训练模型。" + "它可以帮助企业构建智能应用，提升效率，降低成本，创造价值。" * 10)
    assert validate_faithfulness([expanded], source, "faithful") is False
    # rewrite 档无长度上限（允许详略重组），只校验数字+覆盖率
    assert validate_faithfulness([expanded], source, "rewrite") is True


def test_validate_baseline_strips_titles_and_page_numbers():
    # 标题/页码不进覆盖率基线（标题不逐字念——决策 8）
    source = "# 第一章 机器学习概述\n机器学习是人工智能的一个分支，通过数据训练模型。\n3\n第4页\n"
    units = [_u("机器学习是人工智能的一个分支，通过数据训练模型。")]
    assert validate_faithfulness(units, source, "faithful") is True


# ── 容量契约（决策 17）────────────────────────────────────────────────────


def test_split_overflow_units_bullets_over_four():
    units = [
        CourseUnit(
            layout="ConceptPage",
            narration="优化方向有五个：第一降低延迟；第二提升精度；第三压缩模型；第四增强安全；第五改善体验。各项指标均已达标。",
            content={"title": "优化方向", "bullets": ["降低延迟", "提升精度", "压缩模型", "增强安全", "改善体验"]},
            chapter="性能优化",
        )
    ]
    out, overflow = split_overflow_units(units)
    assert len(out) == 2  # 5 条 bullets → 拆 2 个连续单元
    assert all(u.layout == "ConceptPage" for u in out)
    assert all(u.chapter == "性能优化" for u in out)
    assert len(out[0].content["bullets"]) == 4 and len(out[1].content["bullets"]) == 1
    # 拆出的单元 narration 自然分段（两段都有实质内容）
    assert out[0].narration and out[1].narration
    assert out[0].narration != out[1].narration


def test_split_overflow_units_title_overflow_warn_only():
    # title >16 字：不拆帧不截断，仅产出 overflow 留痕信息（warn 级，不触发兜底）
    long_title = "这是一个特别长的标题超过了十六个字的上限要求"
    units = [CourseUnit(layout="ConceptPage", narration="旁白内容保持原样不变。", content={"title": long_title})]
    out, overflow = split_overflow_units(units)
    assert len(out) == 1
    assert out[0].content["title"] == long_title  # 不截断
    assert overflow and overflow[0]["issue"] == "title_overflow"


# ── 元帧门槛（决策 18）────────────────────────────────────────────────────


def _mk_units(n: int, n_chapters: int) -> list[CourseUnit]:
    return [CourseUnit(layout="ConceptPage", narration=f"单元{i}的旁白内容。", chapter=f"第{c}章" if n_chapters else None) for i, c in enumerate([min(i, n_chapters - 1) for i in range(n)])]


def test_gate_meta_frames_matrix():
    # 单元 5（<8）：ObjectivesPage/OutlinePage/ChapterDivider 全降级
    units = _mk_units(5, 1)
    units[1].layout = "ObjectivesPage"
    units[2].layout = "OutlinePage"
    units[3].layout = "ChapterDivider"
    gated = gate_meta_frames(units)
    assert gated[1].layout == "ConceptPage"
    assert gated[2].layout == "ConceptPage"
    assert gated[3].layout == "ConceptPage"
    # 降级保留 content
    assert gated[1].content == units[1].content

    # 单元 8、章节 1（<3）：OutlinePage 降级；ObjectivesPage 保留；ChapterDivider（<10）降级
    units = _mk_units(8, 1)
    units[1].layout = "ObjectivesPage"
    units[2].layout = "OutlinePage"
    units[3].layout = "ChapterDivider"
    gated = gate_meta_frames(units)
    assert gated[1].layout == "ObjectivesPage"
    assert gated[2].layout == "ConceptPage"
    assert gated[3].layout == "ConceptPage"

    # 单元 12、章节 4：全部保留
    units = _mk_units(12, 4)
    units[1].layout = "ObjectivesPage"
    units[2].layout = "OutlinePage"
    units[5].layout = "ChapterDivider"
    gated = gate_meta_frames(units)
    assert gated[1].layout == "ObjectivesPage"
    assert gated[2].layout == "OutlinePage"
    assert gated[5].layout == "ChapterDivider"

    # 单元 12、章节 2（≥2 但章节 <3）：OutlinePage 降级；ChapterDivider（≥10 且 ≥2 章）保留
    units = _mk_units(12, 2)
    units[2].layout = "OutlinePage"
    units[5].layout = "ChapterDivider"
    gated = gate_meta_frames(units)
    assert gated[2].layout == "ConceptPage"
    assert gated[5].layout == "ChapterDivider"


# ── units_to_frames ───────────────────────────────────────────────────────


def test_units_to_frames_groups_and_embedded_image():
    units = [
        CourseUnit(layout="CoverPage", narration="封面旁白。"),
        CourseUnit(layout="ImageTextLeft", narration="章节一内容。", chapter="第一章", image_ref="img_1"),
        CourseUnit(layout="ConceptPage", narration="章节一继续。", chapter="第一章"),
        CourseUnit(layout="ConceptPage", narration="无章节单元。"),
    ]
    frames, groups = units_to_frames(units, image_paths={"img_1": "visuals/embedded/img_1.png"})
    assert [f.index for f in frames] == [0, 1, 2, 3]
    # image_ref → local_visual_path；visual_source 保持 None（3.1 约定：与 upload-visual 一致）
    assert frames[1].local_visual_path == "visuals/embedded/img_1.png"
    assert frames[1].visual_type == "image"
    assert frames[1].visual_source is None
    # layout/layout_content 写入
    assert frames[1].layout == "ImageTextLeft"
    assert frames[0].layout_content is None  # 空 content → None（走确定性推导）
    # scene_groups：同章连续聚合；无 chapter 不建组
    assert len(groups) == 1
    assert groups[0].title == "第一章"
    assert groups[0].frame_indices == [1, 2]


def test_units_to_frames_visual_source_matrix():
    units = [
        # 图槽 + 配图词 → ai_image
        CourseUnit(layout="ImageTextRight", narration="配图单元。", visual_prompt="本页要点的对比关系示意图"),
        # 非图槽 + 配图词 → 两字段留空
        CourseUnit(layout="ChartPage", narration="数据单元。", visual_prompt="不该生效"),
        # 图槽 + 嵌入图 + 配图词 → 不生 AI 图（有图）
        CourseUnit(layout="QuotePage", narration="引用单元。", image_ref="img_9", visual_prompt="不该生效"),
        # 图槽 + 无配图词 → 占位渲染（不生图）
        CourseUnit(layout="EndingPage", narration="收尾单元。"),
    ]
    frames, _ = units_to_frames(units, image_paths={"img_9": "visuals/embedded/img_9.png"})
    assert frames[0].visual_source == "ai_image"
    assert frames[0].image_prompt == "本页要点的对比关系示意图"
    assert frames[1].visual_source is None and frames[1].image_prompt is None
    assert frames[2].visual_source is None
    assert frames[2].local_visual_path == "visuals/embedded/img_9.png"
    assert frames[3].visual_source is None
    # 全矩阵无 ai_video
    assert all(f.visual_source != "ai_video" for f in frames)


def test_units_to_frames_image_ref_without_path_kept_none():
    # staging 过期/丢失 → image_ref 无路径映射 → local_visual_path 留空（占位渲染）
    units = [CourseUnit(layout="ImageTextLeft", narration="嵌入图丢失单元。", image_ref="img_1")]
    frames, _ = units_to_frames(units, image_paths={})
    assert frames[0].local_visual_path is None
    assert frames[0].visual_source is None


# ── units_from_paragraphs / strip_image_markers ───────────────────────────


def test_units_from_paragraphs_deterministic():
    text = "第一段讲概念的完整定义与背景。\n\n第二段讲应用场景，含【图片1】标记。\n\n第三段总结全文要点。"
    units = units_from_paragraphs(text)
    assert len(units) == 3
    assert units[1].image_ref == "img_1"
    assert "【图片" not in units[1].narration
    # 兜底路径零 AI：无 layout（确定性规则表）、无配图词
    assert all(u.layout is None for u in units)
    assert all(not u.visual_prompt for u in units)


def test_strip_image_markers():
    assert strip_image_markers("前面【图片1】中间【图片12】结尾") == "前面中间结尾"
    assert strip_image_markers("无标记文本") == "无标记文本"


def test_course_unit_layouts_exclude_videoclip():
    assert len(COURSE_UNIT_LAYOUTS) == 18
    assert "VideoClipPage" not in COURSE_UNIT_LAYOUTS


# ── 2026-09-11 审查修复回归 ───────────────────────────────────────────────


def test_validate_single_digit_percent_equivalence():
    """M2：个位数百分比等值（"提升5%"↔"提升百分之五"）——修前 CN 百分比分支套
    _keep 过滤会让一侧缺失 → false-fail 落兜底。"""
    source = "本周活跃率提升5%，次日留存提升3个百分点之外另有9%来自老用户回流。"
    units = [_u("本周活跃率提升百分之五，次日留存提升3个百分点之外另有百分之九来自老用户回流。")]
    assert validate_faithfulness(units, source, "rewrite") is True


def test_parse_cn_year_positional():
    """Minor：纯数字位串按位值拼接——"二零二四"=2024（修前加法口径读成 4）。"""
    from app.gateway.video_gen.orchestration.course_script import _parse_cn_number

    assert _parse_cn_number("二零二四") == 2024
    assert _parse_cn_number("三千五百万") == 35000000  # 单位节路径不受影响
    assert _parse_cn_number("二十") == 20


def test_split_overflow_units_sentence_shortage_never_empty_narration():
    """M3：9 条 bullets + 2 句旁白——修前第 3 组 narration 分空 → 空旁白帧进 TTS。
    修复后收缩为 2 组（剩余 bullets 并入末组）且 overflow 留痕可见。"""
    units = [
        CourseUnit(
            layout="ConceptPage",
            narration="这一页覆盖九个优化方向。其中前四个最为关键。",
            content={"bullets": [f"要点{i}" for i in range(1, 10)]},
        )
    ]
    out, overflow = split_overflow_units(units)
    assert len(out) == 2
    assert all(u.narration.strip() for u in out), "拆出单元不得有空旁白（无声内容帧）"
    # 剩余 bullets 并入末组（5 条 > 4）→ 留痕可见
    assert len(out[1].content["bullets"]) == 5
    assert any(ov["issue"] == "bullets_overflow_unsplit" for ov in overflow)


def test_layout_content_fields_note_added():
    """M9 后端：ChartPage/FullVisualPage 补 note（模板 SceneContent 有此字段）。"""
    from app.gateway.video_gen.orchestration.course_script import LAYOUT_CONTENT_FIELDS

    assert "note" in LAYOUT_CONTENT_FIELDS["ChartPage"]
    assert "note" in LAYOUT_CONTENT_FIELDS["FullVisualPage"]
    # 前端 schema 白名单对齐项（后端本就有）
    assert "bullets" in LAYOUT_CONTENT_FIELDS["CodePage"]
    assert "instructor" in LAYOUT_CONTENT_FIELDS["CoverPage"]


# ── 一单元多图顺延（§3.6，2026-09-11 审查补交付）─────────────────────────


def test_sanitize_units_multi_marker_collects_extras():
    from app.gateway.video_gen.orchestration.course_script import _sanitize_units

    raw = [{"layout": "ImageTextLeft", "narration": "对照图一【图片1】与图二【图片2】的差异。", "content": {}}]
    units = _sanitize_units(raw)
    assert units[0].image_ref == "img_1"
    assert units[0].image_refs_extra == ["img_2"]
    assert "【图片" not in units[0].narration


def test_defer_extra_images_forward_and_note():
    from app.gateway.video_gen.orchestration.course_script import defer_extra_images

    units = [
        CourseUnit(layout="ImageTextLeft", narration="多图单元。", image_ref="img_1", image_refs_extra=["img_2", "img_3"]),
        CourseUnit(layout="ImageTextLeft", narration="次单元已有图。", image_ref="img_9"),
        CourseUnit(layout="ConceptPage", narration="无图单元一。"),
        CourseUnit(layout="ConceptPage", narration="无图单元二。"),
    ]
    units, deferrals = defer_extra_images(units)
    # 顺延目标 = 源之后第一个无图单元（跳过已有 img_9 的单元 1）
    assert units[2].image_ref == "img_2"
    assert units[2].image_note == "来自邻近单元的文档配图"
    assert units[3].image_ref == "img_3"
    assert units[3].image_note == "来自邻近单元的文档配图"
    # 源单元 extras 清空（防重复顺延）
    assert units[0].image_refs_extra == []
    assert deferrals == [
        {"ref": "img_2", "from_pos": 0, "to_pos": 2},
        {"ref": "img_3", "from_pos": 0, "to_pos": 3},
    ]


def test_defer_extra_images_no_target_dropped_with_trace():
    from app.gateway.video_gen.orchestration.course_script import defer_extra_images

    units = [
        CourseUnit(layout="ConceptPage", narration="末单元。", image_refs_extra=["img_7"]),
    ]
    units, deferrals = defer_extra_images(units)
    assert units[0].image_ref is None
    assert deferrals == [{"ref": "img_7", "from_pos": 0, "to_pos": None}]


def test_units_from_paragraphs_multi_marker_no_silent_drop():
    text = "第一段正文内容完整。\n\n【图片1】【图片2】"
    units = units_from_paragraphs(text)
    # 首图归并前一单元，第二张进 extras（修前静默丢弃）
    assert units[0].image_ref == "img_1"
    assert units[0].image_refs_extra == ["img_2"]


def test_units_to_frames_carries_image_note():
    units = [
        CourseUnit(layout="ConceptPage", narration="多图单元。", image_ref="img_1", image_refs_extra=["img_2"]),
        CourseUnit(layout="ConceptPage", narration="顺延目标。", image_ref="img_2", image_note="来自邻近单元的文档配图"),
    ]
    frames, _ = units_to_frames(units, image_paths={"img_1": "visuals/embedded/img_1.png", "img_2": "visuals/embedded/img_2.png"})
    assert frames[1].image_note == "来自邻近单元的文档配图"
    assert frames[1].local_visual_path == "visuals/embedded/img_2.png"


def test_units_from_paragraphs_single_newline_fallback():
    """M2（2026-09-11 二轮审查）：docx 提取的段落以单 \\n 连接（无空行）——修前
    整脚本塌缩成 1 个巨型单元（叠加 P4 跳拆帧守卫 → 单帧巨段 TTS）。"""
    text = "第一行讲概念定义。\n第二行讲应用场景，含【图片1】标记。\n第三行总结要点。\n第四行收尾。"
    units = units_from_paragraphs(text)
    assert len(units) == 4  # 修前 = 1
    assert units[1].image_ref == "img_1"
    # 空行分段（多段）行为不变
    text2 = "第一段完整内容。\n\n第二段完整内容。"
    assert len(units_from_paragraphs(text2)) == 2
    # 无换行单段不变
    assert len(units_from_paragraphs("只有一句话的脚本。")) == 1


# ── 2026-09-12 缺陷批条目 15/16/17 ─────────────────────────────────────────


def test_split_overflow_units_clears_subsequent_image_fields():
    """条目 15：拆分后续子单元清 image_ref/image_note/visual_prompt（修前全字段继承——
    N 个连续子帧同一 local_visual_path 多帧同图、图槽版式每子帧重复生图 N 次）。"""
    unit = CourseUnit(
        layout="ConceptPage",
        narration="九条要点逐一展开：第一条讲延迟；第二条讲精度；第三条讲压缩；第四条讲安全；第五条讲体验；第六条讲成本；第七条讲吞吐；第八条讲可靠；第九条讲运维。",
        content={"title": "要点", "bullets": [f"要点{i}" for i in range(1, 10)]},
        image_ref="img_1",
        image_note="来自邻近单元的文档配图",
        visual_prompt="示意图提示词",
    )
    out, _overflow = split_overflow_units([unit])
    assert len(out) == 3  # 9 bullets / 4 = 3 子单元
    first, *rest = out
    assert first.image_ref == "img_1"
    assert first.image_note == "来自邻近单元的文档配图"
    assert first.visual_prompt == "示意图提示词"
    for u in rest:
        assert u.image_ref is None
        assert u.image_note is None
        assert u.visual_prompt is None


def test_units_from_paragraphs_multi_paragraph_intra_newline_split():
    """条目 16：≥2 段 + 段内单 \n → 逐段判定，段内行数 >1 按行级+句末标点切分
    （修前整段并一个巨型单元——docx 混合结构 3 段×5 行塌成 3 个巨型单元）。"""
    text = "第一段第一句。第一段第二句！\n第一段第三句？\n\n第二段整段单行，两句连排。\n\n第三段单行。"
    units = units_from_paragraphs(text)
    narrations = [u.narration for u in units]
    # 多行段：行级+句末标点 → 3 个单元
    assert "第一段第一句。" in narrations
    assert "第一段第二句！" in narrations
    assert "第一段第三句？" in narrations
    # 单行段不动（逐段判定，不引入句级碎帧）
    assert "第二段整段单行，两句连排。" in narrations
    assert "第三段单行。" in narrations
    assert len(units) == 5


def test_strip_image_markers_structural_matrix():
    """条目 17：strip 矩阵扩展——【…】全量（含【画面：…】）、行首 场景N：/镜头：/旁白：/
    画面描述： 剥离；正文句保留。「【…】包围内容一律不进旁白」是既定产品语义。"""
    from app.gateway.video_gen.orchestration.course_script import strip_image_markers

    text = "【画面：城市夜景航拍】开场介绍。\n场景 1：镜头缓缓推进。\n镜头 2：特写主角面部。\n旁白：这里是正文旁白。\n画面描述：街道全景。\n解说词：也剥。\n画外音：同样剥。\n场景描述：一并剥。\n保持这一句完整。"
    out = strip_image_markers(text)
    # 【…】整体剥离（含内容）；行首标签仅剥前缀（冒号后正文保留——与
    # _strip_visual_markers 同口径：标签是标记，标签后的句子是台词）
    assert "【" not in out and "】" not in out
    assert "城市夜景" not in out
    for label in ("场景 1：", "镜头 2：", "旁白：", "画面描述：", "解说词：", "画外音：", "场景描述："):
        assert label not in out, f"标签前缀 {label} 不应残留: {out}"
    assert "开场介绍。" in out
    assert "镜头缓缓推进。" in out
    assert "特写主角面部。" in out
    assert "这里是正文旁白。" in out
    assert "街道全景。" in out
    assert "保持这一句完整。" in out


def test_units_from_paragraphs_strips_structural_markers():
    """条目 17 兜底：直贴脚本结构性标记不进旁白/编辑器；纯标记段落消失。"""
    text = "【画面：夜景航拍】开场白第一句。\n\n正文第二段。"
    units = units_from_paragraphs(text)
    assert units[0].narration == "开场白第一句。"
    text2 = "【画面：只有标记】\n\n正文段落。"
    units2 = units_from_paragraphs(text2)
    assert len(units2) == 1 and units2[0].narration == "正文段落。"


def test_sanitize_units_strips_structural_markers_keeps_image_ref():
    """条目 17 parser 路径：【图片N】先提取（是【…】子集，顺序不可反），再剥结构性标记。"""
    from app.gateway.video_gen.orchestration.course_script import _sanitize_units

    units = _sanitize_units([{"narration": "【画面：图示】要点讲解。【图片1】收尾。", "layout": "ConceptPage"}])
    assert units[0].narration == "要点讲解。收尾。"
    assert units[0].image_ref == "img_1"


def test_baseline_symmetric_strip_marker_numbers_not_missing():
    """条目 17 基线对称剥离：产出侧剥了【2024年报】标记，基线同剥——标记内数字不判
    缺失（修前会误杀重试/落兜底）；【课程目标】被剥离是既定产品语义（对齐上传路径）。"""
    from app.gateway.video_gen.orchestration.course_script import _narrative_baseline, validate_faithfulness

    source = "【课程目标】掌握 2024 年报分析方法。\n【画面：图表特写】核心指标解读完成。"
    units = [CourseUnit(layout=None, narration="掌握 2024 年报分析方法。核心指标解读完成。", content={})]
    assert validate_faithfulness(units, source, "faithful") is True
    baseline = _narrative_baseline(source)
    assert "课程目标" not in baseline and "图表特写" not in baseline
    assert "2024" in baseline


def test_strip_image_markers_review2_aligned_matrix():
    """复审二轮（2026-09-13）：补齐与上传路径 _VISUAL_MARKER_PATTERNS 的子集差——
    括号镜头标记（全/半角）、**粗体**、行首 [标签]、markdown 标题、公众号元信息
    行、裸页码不进旁白；正文句保留。"""
    from app.gateway.video_gen.orchestration.course_script import strip_image_markers

    text = "（特写镜头：主角面部）正文第一句。\n(close-up shot of the product)英文括号同样剥。\n**画面说明**：图表展示要点。\n[场景标注]\n## 第二章 知识要点\n作者：张三\n请翻到第 12 页查看。\n保留这一句完整。"
    out = strip_image_markers(text)
    assert "特写镜头" not in out and "主角面部" not in out
    assert "close-up" not in out and "英文括号同样剥" in out
    assert "画面说明" not in out and "图表展示要点" in out  # 粗体标签剥、其后正文保留
    assert "场景标注" not in out
    assert "第二章" not in out
    assert "作者：张三" not in out
    assert "第 12 页" not in out and "查看" in out
    assert "正文第一句。" in out and "保留这一句完整。" in out
    # 三轮复审（2026-09-13）三个边角形态：
    # ① \d* 放宽分支——行首无编号裸「场景：」标签同样剥（内容保留）
    out_bare = strip_image_markers("前置句。\n场景：开场白内容。\n镜头：特写旁白内容。")
    assert "场景：" not in out_bare and "镜头：" not in out_bare
    assert "开场白内容。" in out_bare and "特写旁白内容。" in out_bare
    # ② 裸页码无空格变体「第12页」剥净（\s* 两端）
    assert "第12页" not in strip_image_markers("见第12页附注。")
    # ③ 【图片N】与新增标记形态同行组合——先 findall 提取图槽、再剥离，
    # narration 无任何标记残留（提取顺序不可反）
    units_combo = units_from_paragraphs("【图片1】（特写镜头）组合正文句。")
    assert units_combo[0].image_ref == "img_1"
    assert units_combo[0].narration == "组合正文句。"
    assert "【" not in units_combo[0].narration and "特写镜头" not in units_combo[0].narration


def test_units_from_paragraphs_strips_review2_markers():
    """直贴主路径兜底同集：括号镜头标记/粗体/标题不进旁白单元。"""
    text = "（全景：城市夜景）开场介绍。\n\n**镜头1**：推进叙述。\n\n正文段落保留。"
    units = units_from_paragraphs(text)
    assert units[0].narration == "开场介绍。"
    assert units[1].narration == "推进叙述。"
    assert units[2].narration == "正文段落保留。"


# ── P3-2 / BUG-002（2026-09-14）：解析产物标记剥离（按类型递归版） ────────────


def test_sanitize_units_recursive_content_marker_strip():
    """LLM 产出 content（title/bullets/steps 嵌套 dict/list）含【图片N】/【画面：x】
    → 白名单过滤后递归剥离干净——修前 narration 剥了、content 字段值没剥，
    标记进 CoverPage 顶栏/图文版式正文（vg_c17cb95fd5ff 残留形态的代码缺口）。"""
    units = _sanitize_units_public(
        [
            {
                "narration": "第一单元旁白。",
                "layout": "ProcessPage",  # steps 白名单在 ProcessPage；instructor 在 CoverPage
                "content": {
                    "title": "课程标题【图片1】",
                    "bullets": ["要点一【图片2】", "要点二"],  # CodePage 才有 bullets——Process 无，此处顺带钉白名单先行语义
                    "steps": [
                        {"title": "步骤【画面：全景】甲", "desc": "描述【图片3】乙"},
                        "纯字符串步骤【图片4】",
                    ],
                },
                "image_ref": None,
            },
            {
                "narration": "封面旁白。",
                "layout": "CoverPage",
                "content": {
                    "title": "课程封面【图片6】",
                    "instructor": {"name": "讲师【图片5】", "title": "职称"},
                },
            },
        ]
    )
    c = units[0].content
    assert c["title"] == "课程标题"
    assert "bullets" not in c  # 白名单先行：非白名单键在剥离前已滤
    assert c["steps"][0]["title"] == "步骤甲" and c["steps"][0]["desc"] == "描述乙"
    assert c["steps"][1] == "纯字符串步骤"
    c2 = units[1].content
    assert c2["title"] == "课程封面"
    assert c2["instructor"]["name"] == "讲师" and c2["instructor"]["title"] == "职称"


def test_sanitize_units_code_field_exempt():
    """code 键整键豁免（CodePage 代码内容合法含【】）——递归剥离不得误伤。"""
    units = _sanitize_units_public(
        [
            {
                "narration": "代码单元旁白。",
                "layout": "CodePage",
                "content": {
                    "language": "python",
                    "code": "x = arr[0]  # 【图片1】占位注释",
                },
            }
        ]
    )
    assert "【图片1】" in units[0].content["code"]


def test_sanitize_units_numeric_values_untouched():
    """number/bool 等非 str/list/dict 值原样（递归只处理可承载文本的类型）。"""
    units = _sanitize_units_public(
        [
            {
                "narration": "图表单元旁白。",
                "layout": "ChartPage",
                "content": {"title": "图表页", "chart": {"values": [3, 7], "labels": ["标签甲", "标签乙"]}},
            }
        ]
    )
    ch = units[0].content["chart"]
    assert ch["values"] == [3, 7] and ch["labels"] == ["标签甲", "标签乙"]


def test_strip_content_markers_nested_code_key_in_list_exempt():
    """list[dict] 深层嵌套里的 code 键同样整键豁免（每层 dict 递归均跳过 code）。

    白名单后的 content 走不到这形态（各版式白名单不含此嵌套）——直测递归函数
    本体钉死「整键豁免在任何嵌套深度都成立」（review 7b）。"""
    from app.gateway.video_gen.orchestration.course_script import _strip_content_markers

    src = {
        "steps": [
            {"title": "步骤【图片1】", "desc": "d【图片2】", "code": "keep 【画面：x】"},
        ],
        "notes": ["嵌套【图片3】", {"inner": "i【图片4】", "code": "c【图片5】"}],
    }
    out = _strip_content_markers(src)
    assert out["steps"][0]["title"] == "步骤"
    assert out["steps"][0]["desc"] == "d"
    assert out["steps"][0]["code"] == "keep 【画面：x】"
    assert out["notes"][0] == "嵌套"
    assert out["notes"][1]["inner"] == "i"
    assert out["notes"][1]["code"] == "c【图片5】"


def test_units_to_frames_chapter_title_stripped():
    """章节标题（进 ChapterDivider 版式）同样剥——修前 course_script chapter 未剥，
    AI 解析路径任一处携带标记 → 成片分章页残留。"""
    units = [CourseUnit(layout="ConceptPage", narration="正文。", chapter="第一章 财报解读【图片1】")]
    _, groups = units_to_frames(units)
    assert groups[0].title == "第一章 财报解读"


def test_service_storyboard_title_sites_strip_static_guard():
    """service.py 两处 Storyboard(title=…) 过 _strip_structural_markers（源码静态锚）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app/gateway/video_gen/service.py").read_text(encoding="utf-8")
    assert src.count("Storyboard(title=_cs_strip(title)") == 2


def _sanitize_units_public(raw_units):
    """_sanitize_units 是模块私有——经 parse_course_script 的清洗入口调用同函数。"""
    from app.gateway.video_gen.orchestration.course_script import _sanitize_units

    return _sanitize_units(raw_units)

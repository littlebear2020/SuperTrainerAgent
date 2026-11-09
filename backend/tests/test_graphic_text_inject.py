"""graphic-native-text N1（2026-08-17）：AI 原生文字注入器 + 渲染归并（预览注入断言见 test_graphic_preview.py）。

- inject_graphic_text：spec（已过逐字符校验的原文）→ 图内文字渲染指令（引号原文+风格统一句）
- MixedVisualSource：graphic 帧归并到 ai_image 源（标记保留供护栏/徽标/一键关）
- AIImageVisualSource.fill / _generate_frame_preview：graphic+spec 帧注入后再发 provider
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.graphic_text import _DEFAULT_LAYOUT_CARD, inject_graphic_text


class TestInject:
    def test_big_number_number_in_quotes(self):
        out = inject_graphic_text("现代明亮的咖啡店场景", {"type": "big_number", "number": "137", "unit": "%"})
        assert "「137%」" in out, "数字+单位须引号原文注入"
        assert "现代明亮的咖啡店场景" in out, "基础画面描述保留"
        assert "风格" in out and "统一" in out, "须带风格统一约束"

    def test_text_card_title_and_labels(self):
        out = inject_graphic_text("扁平插画场景", {"type": "text_card", "title": "反馈的核心", "points": ["理解用户", "明确目标", "权衡资源", "敢于删减"]})
        assert "「反馈的核心」" in out
        # 标签 ≤3 处（第 4 点截断——AI 画字处数越多越不可靠）
        assert "「理解用户」" in out and "「敢于删减」" not in out

    def test_mixed_cn_en_title_within_budget_injected_in_full(self):
        """2026-09-02 标题截断修复：≤14 字标题（含中英混合贴边案例「小任务慎用
        Subagent」=13 字）完整注入——修前被误套标签 6 字上限裁成「小任务慎用S」。"""
        title = "小任务慎用Subagent"
        assert len(title) == 13
        out = inject_graphic_text("扁平插画场景", {"type": "text_card", "title": title, "points": ["理解用户", "明确目标"]})
        assert f"「{title}」" in out, "14 字内标题必须完整注入，不得截断"

    def test_over_budget_title_omitted_labels_still_injected(self, caplog):
        """标题 >14 字 = 字段级省略（不画标题、卡片照出）+ warning；标签照常注入。"""
        import logging

        long_title = "十五字以上的超长标题演示字样甲"  # len 15 > 14
        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.visual_sources.graphic_text"):
            out = inject_graphic_text("场景", {"type": "text_card", "title": long_title, "points": ["要点甲", "要点乙"]})
        assert long_title not in out and "标题文字" not in out, "超预算标题整条省略（不画）"
        assert "「要点甲」" in out and "「要点乙」" in out, "标签照常注入（卡片照出）"
        assert any("省略" in r.message and "标题" in r.message for r in caplog.records), "省略须 warning 可观测"

    def test_steps_labels_with_sequence(self):
        out = inject_graphic_text("工作流程场景", {"type": "steps", "title": "流程", "steps": ["收集反馈", "归类分析", "排定优先级", "验证改进"]})
        assert "「收集反馈」" in out and "「验证改进」" not in out
        assert "「流程」" in out, "steps 标题必须注入（修前提取校验了却从未进 prompt）"
        assert "顶部呈现标题文字" in out, "steps 标题走顶部标题位（与 text_card 同版式语义）"
        assert "序号" in out or "编号" in out

    def test_over_six_label_omitted_not_truncated(self, caplog):
        """标签 >6 字 = 整条省略（2026-09-02 拍板）——截断已彻底移除，F-B12-006
        随之关闭：修前截断点可落在数字内部（「全年营收3.1415万亿元」→「全年营收3.14」
        幻觉数字）。本用例即原 test_long_label_truncated_to_six 的替换。"""
        import logging

        label = "全年营收3.1415万亿元"
        assert len(label) == 13
        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.visual_sources.graphic_text"):
            out = inject_graphic_text("场景", {"type": "text_card", "title": "t", "points": [label, "短"]})
        assert "3.14" not in out, "不得出现截断残缺数字（幻觉数字，F-B12-006）"
        assert label not in out and "「全年营收3" not in out, "超长标签整条省略，无任何前缀残串"
        assert "「短」" in out and "「t」" in out, "合规标签/标题照常注入（卡片照出）"
        assert any("省略" in r.message and "标签" in r.message for r in caplog.records), "省略须 warning 可观测"

    def test_english_term_label_injected_in_full(self):
        """2026-09-03 词折算：术语标签按词计（汉字 1 + 英文词 2，≤6 单位）完整注入
        ——修前按字数罚，术语型内容（vg_aac257fcf7f3 实证 16/36 帧全灭）全被拒。"""
        out = inject_graphic_text("场景", {"type": "text_card", "title": "t", "points": ["直到Session结束", "省Read调用"]})
        assert "「直到Session结束」" in out and "「省Read调用」" in out, "6 单位内术语标签完整注入（不截断）"

    def test_all_fields_omitted_returns_base_prompt(self, caplog):
        """标题+标签全省略（极端：盘上旧 storyboard 遗留全面超限）→ 无文字可画 →
        按既有逻辑返回 base_prompt（普通画面，等价 spec=None）。"""
        import logging

        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.visual_sources.graphic_text"):
            out = inject_graphic_text("纯画面描述", {"type": "text_card", "title": "甲" * 15, "points": ["这是一个长标签", "这是二个长标签"]})
        assert out == "纯画面描述", "全省略 = 无文字可画 → 普通画面"
        assert len(caplog.records) >= 3, "标题 1 条 + 标签 2 条省略均须 warning"

    def test_no_spec_returns_base(self):
        assert inject_graphic_text("纯画面", None) == "纯画面"
        assert inject_graphic_text("纯画面", {}) == "纯画面"

    def test_unknown_type_returns_base(self):
        assert inject_graphic_text("纯画面", {"type": "future"}) == "纯画面"


# ── T5b（2026-08-18 拍板⑦）：graphic 版式卡统一——治「10 张文字卡 10 种风格」──
# 版式锚来源 = AI 现场设计（并入 T5 锚定调用一次产出），全任务逐字复用；
# 缺失时回落确定性默认版式卡（不回落旧 _TAIL 软约束 = 回到逐卡漂移坏现状）。


class TestLayoutCard:
    LAYOUT_PREFIX = "。全片信息图版式统一："

    def test_same_task_multiple_graphic_frames_share_identical_layout_instruction(self):
        """同任务多 graphic 帧（spec 类型各异）版式指令**逐字一致**——跨帧统一是
        T5b 的全部意义，逐卡漂移即回归。"""
        card = "米白底深蓝主色，无衬线加粗黑体，等距网格骨架"
        outs = [
            inject_graphic_text("画面A", {"type": "big_number", "number": "137", "unit": "%"}, card),
            inject_graphic_text("画面B", {"type": "text_card", "title": "要点", "points": ["甲", "乙"]}, card),
            inject_graphic_text("画面C", {"type": "steps", "steps": ["先", "后"]}, card),
        ]
        suffixes = [out.split(self.LAYOUT_PREFIX, 1)[1] for out in outs]
        assert len(set(suffixes)) == 1, f"版式指令必须逐字一致，实际 {suffixes!r}"
        assert suffixes[0] == card

    def test_missing_layout_card_falls_back_to_deterministic_default(self):
        """版式卡缺失（锚定失败/无字段）→ 确定性默认版式卡（等距信息图基座常量），
        不回落旧 _TAIL 的「与画面风格和配色统一」软约束（评审修订）。"""
        out = inject_graphic_text("画面", {"type": "big_number", "number": "830", "unit": "亿"})
        assert f"{self.LAYOUT_PREFIX}{_DEFAULT_LAYOUT_CARD}" in out, "回落目标=确定性默认版式卡常量"
        assert "与画面风格和配色统一" not in out, "旧 _TAIL 软约束不得回归（=逐卡漂移坏现状）"

    def test_explicit_empty_layout_string_equals_missing(self):
        spec = {"type": "big_number", "number": "1", "unit": "%"}
        assert inject_graphic_text("p", spec, "") == inject_graphic_text("p", spec)


# ── 审查偏离项 4（2026-08-19）：注入后 prompt 长度守护 ──
# Ark seedream 官方口径「prompt ≤300 汉字 / 600 英文单词」（BYTEDANCE_ARK_API_
# REFERENCE.md）。极端组合（base 180 字 + 版式卡 60 字 + 文字指令 + 渲染尾缀）
# 会越过预算，修前无截断/告警——Ark 端行为未定义（可能 400 / 可能静默截断）。
# 守护 = 以 Ark 混合口径计长（汉字 1 单位、英文词 0.5 单位），超预算时裁
# **base 描述尾部**（保头部主体层），图内文字指令+版式卡逐字保留（graphic 帧
# 的全部意义所在），并 warning 可观测。


class TestPromptBudget:
    def test_long_base_trimmed_to_seedream_budget_tail_intact(self):
        """超预算 → 总长 ≤300 单位；base 从尾部裁；文字指令+版式卡逐字保留。"""
        from app.gateway.video_gen.visual_sources.graphic_text import _prompt_units

        base = "微距镜头下精密的机械腕表机芯特写" * 20  # 300 汉字的 base，本身已超
        card = "米" * 60  # 版式卡上限 60 字
        spec = {"type": "steps", "steps": ["收集反馈", "归类分析", "排定优先级"]}
        out = inject_graphic_text(base, spec, card)
        assert _prompt_units(out) <= 300, "注入后总长必须守住 Ark seedream ≤300 汉字预算"
        # graphic 指令逐字保留（裁的是 base，不是版式卡/文字指令）
        assert "。全片信息图版式统一：" + card in out
        assert "画面呈现带数字序号的分步标签" in out
        # base 保头部（主体在前的分层模板：裁尾不裁头）
        assert out.startswith("微距镜头下精密的机械腕表机芯特写")

    def test_trim_logs_warning(self, caplog):
        """发生裁剪必须 warning 可观测（无守护=静默超发/静默失败）。"""
        import logging

        base = "很长的画面描述" * 40
        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.visual_sources.graphic_text"):
            inject_graphic_text(base, {"type": "big_number", "number": "830", "unit": "亿"})
        assert any("seedream" in r.message and "预算" in r.message for r in caplog.records), "裁剪须留 warning（含 seedream/预算关键词，便于日志检索）"

    def test_normal_length_prompt_untouched_no_warning(self, caplog):
        """常规长度（base 180 字内）不裁不警——守护只管极端组合，不改主路径。"""
        import logging

        from app.gateway.video_gen.visual_sources.graphic_text import _prompt_units

        base = "明亮现代的办公场景，人物在白板前讲解流程，柔和的自然窗光，浅景深虚化背景，写实商务风格"
        out = inject_graphic_text(base, {"type": "text_card", "title": "要点", "points": ["甲", "乙"]})
        assert out.startswith(base), "未超预算时 base 必须原样（含后续注入部分）"
        assert _prompt_units(out) <= 300
        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.visual_sources.graphic_text"):
            inject_graphic_text(base, {"type": "text_card", "title": "要点", "points": ["甲", "乙"]})
        assert not caplog.records, "未超预算不得告警"

    def test_worst_case_tail_alone_stays_under_budget(self):
        """结构性护栏：14 字标题 + 3×6 字标签的最长文字指令（steps 带标题为最长
        形态，2026-09-02 补标题注入后就地更新）+ 版式卡 60 字 + 尾缀的固有开销
        必须 < 300——否则极端组合下 base 会被裁到零（守护自身失效）。"""
        from app.gateway.video_gen.visual_sources import graphic_text as gt

        longest_text_part = "画面顶部呈现标题文字「" + "甲" * 14 + "」，下方呈现带数字序号的分步标签：「乙乙乙乙乙乙」、「丙丙丙丙丙丙」、「丁丁丁丁丁丁」，各步以 1/2/3 序号标注"
        tail = f"。{longest_text_part}{gt._RENDER_TAIL}{gt._LAYOUT_PREFIX}{'米' * 60}"
        assert gt._prompt_units(tail) < 300, "graphic 尾缀固有开销须留出 base 余量"

    def test_layout_card_roundtrips_through_storyboard_json(self):
        """版式卡随 storyboard.json 持久化——渲染/预览在别的时点从盘上重读 storyboard
        （from_dict），不落盘任务级版式就断了。"""
        sb = _sb([{"visual_source": "graphic"}])
        sb.graphic_layout_card = "深蓝主色，加粗黑体，等距网格"
        restored = Storyboard.from_dict(json.loads(json.dumps(sb.to_dict(), ensure_ascii=False)))
        assert restored.graphic_layout_card == "深蓝主色，加粗黑体，等距网格"
        # 旧 storyboard（无该字段）反序列化 → None（渲染回落确定性默认）
        legacy = _sb([{"visual_source": "graphic"}])
        assert Storyboard.from_dict(json.loads(json.dumps(legacy.to_dict(), ensure_ascii=False))).graphic_layout_card is None


def _sb(frame_specs: list[dict]) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=640, height=360)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", image_prompt=f"画面{i}", **{k: v for k, v in s.items() if k != "image_prompt"}) for i, s in enumerate(frame_specs)]
    return Storyboard(title="t", config=cfg, frames=frames)


class TestMixedMergeAndFill:
    @pytest.mark.asyncio
    async def test_mixed_routes_graphic_to_ai_image_group(self, tmp_path):
        """graphic 帧在 mixed 分派时归并到 ai_image 源（不再有独立 graphic 源）。"""
        from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

        rendered: list[int] = []

        class _FakeImgSource:
            async def render(self, storyboard, *, only_frames=None, on_frame_done=None, task_dir=None, reference_image_paths=None):  # noqa: ANN003
                for f in storyboard.frames:
                    if only_frames is None or f.index in only_frames:
                        rendered.append(f.index)

        sb = _sb(
            [
                {"visual_source": "ai_image"},
                {"visual_source": "graphic", "graphic_spec": {"type": "big_number", "number": "37", "unit": "%"}},
            ]
        )
        mixed = MixedVisualSource({"ai_image": _FakeImgSource()})
        await mixed.render(sb, task_dir=tmp_path)
        assert rendered == [0, 1], "graphic 帧应与 ai_image 帧同组渲染（归并），不再有独立 graphic 源"

    @pytest.mark.asyncio
    async def test_fill_injects_only_graphic_frames(self, tmp_path, monkeypatch):
        """AIImageVisualSource.fill：graphic+spec 帧注入、普通帧原样。"""
        from app.gateway.video_gen.visual_sources import ai_image as ai_mod

        sb = _sb(
            [
                {"visual_source": "graphic", "graphic_spec": {"type": "big_number", "number": "830亿"}},
                {"visual_source": "ai_image"},
            ]
        )
        prompts: list[str] = []

        async def fake_generate(prompt, size=None, reference_image_paths=None):  # noqa: ANN001
            prompts.append(prompt)
            return SimpleNamespace(url="http://x")

        src = ai_mod.AIImageVisualSource(SimpleNamespace(generate_image=fake_generate), concurrency=2, global_limit=None, budget=None)
        await src.render(sb, task_dir=tmp_path)
        assert any("「830亿」" in p for p in prompts)
        assert any("「830亿」" not in p for p in prompts), "普通帧不注入"

    @pytest.mark.asyncio
    async def test_fill_passes_task_level_layout_to_all_graphic_frames(self, tmp_path):
        """T5b 全链路：storyboard.graphic_layout_card → fill 注入——同任务多个 graphic
        帧发往 provider 的 prompt 版式段逐字一致；无版式卡（旧 storyboard）回落默认常量。"""
        from app.gateway.video_gen.visual_sources import ai_image as ai_mod

        card = "深蓝主色与浅灰辅助，加粗中文黑体，等距网格骨架"
        sb = _sb(
            [
                {"visual_source": "graphic", "graphic_spec": {"type": "big_number", "number": "137", "unit": "%"}},
                {"visual_source": "graphic", "graphic_spec": {"type": "text_card", "title": "要点", "points": ["甲", "乙"]}},
                {"visual_source": "ai_image"},
            ]
        )
        sb.graphic_layout_card = card
        prompts: list[str] = []

        async def fake_generate(prompt, size=None, reference_image_paths=None):  # noqa: ANN001
            prompts.append(prompt)
            return SimpleNamespace(url="http://x")

        src = ai_mod.AIImageVisualSource(SimpleNamespace(generate_image=fake_generate), concurrency=2, global_limit=None, budget=None)
        await src.render(sb, task_dir=tmp_path)
        graphic_prompts = [p for p in prompts if "「137%」" in p or "「要点」" in p]
        assert len(graphic_prompts) == 2
        layout_prefix = "。全片信息图版式统一："
        suffixes = [p.split(layout_prefix, 1)[1] for p in graphic_prompts]
        assert len(set(suffixes)) == 1 and suffixes[0] == card, f"同任务 graphic 帧版式段须逐字一致，实际 {suffixes!r}"
        # 无版式卡的帧（普通帧）不含版式段
        assert all(layout_prefix not in p for p in prompts if p not in graphic_prompts)

    @pytest.mark.asyncio
    async def test_fill_without_layout_card_uses_deterministic_default(self, tmp_path):
        """旧 storyboard（graphic_layout_card=None）→ fill 回落确定性默认版式卡。"""
        from app.gateway.video_gen.visual_sources import ai_image as ai_mod

        sb = _sb([{"visual_source": "graphic", "graphic_spec": {"type": "big_number", "number": "37", "unit": "%"}}])
        prompts: list[str] = []

        async def fake_generate(prompt, size=None, reference_image_paths=None):  # noqa: ANN001
            prompts.append(prompt)
            return SimpleNamespace(url="http://x")

        src = ai_mod.AIImageVisualSource(SimpleNamespace(generate_image=fake_generate), concurrency=2, global_limit=None, budget=None)
        await src.render(sb, task_dir=tmp_path)
        assert prompts and f"。全片信息图版式统一：{_DEFAULT_LAYOUT_CARD}" in prompts[0]

"""content-match T1：graphic_spec schema + 事实校验层（逐字符核对）。

设计（docs/video-gen-content-match-plan-2026-08-14.md §2.2）：
LLM 只负责「提取」，确定性代码负责「核对」——spec 中所有数字必须逐字符出现在
旁白原文或全文稿中（归一化：全半角、%、千分位、空白；单位变体白名单），
任一不出现 → 拒绝 spec → 渲染期降级 text_card(旁白) + sidecar。
"""

from __future__ import annotations

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.graphic_spec import (
    parse_graphic_spec,
    validate_spec_against_narration,
)


class TestParseGraphicSpec:
    """schema 校验：v1 三种类型，形状不对返回 (None, 字段级原因)（不抛）。"""

    def test_valid_text_card(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "免疫三防线", "points": ["物理屏障", "先天免疫"]})
        assert spec is not None and spec["type"] == "text_card"
        assert issues == [], "合规 spec 无 issues"

    def test_valid_big_number(self):
        spec, issues = parse_graphic_spec({"type": "big_number", "number": "37", "unit": "%", "description": "增长"})
        assert spec is not None and spec["type"] == "big_number"
        assert issues == []

    def test_valid_steps(self):
        spec, issues = parse_graphic_spec({"type": "steps", "title": "操作步骤", "steps": ["第一步", "第二步"]})
        assert spec is not None and spec["type"] == "steps"
        assert issues == []

    def test_unknown_type_rejected(self):
        spec, issues = parse_graphic_spec({"type": "bar_chart", "series": []})
        assert spec is None and issues, "非法类型须带字段级原因"

    def test_missing_type_rejected(self):
        spec, issues = parse_graphic_spec({"title": "x"})
        assert spec is None and issues

    def test_text_card_over_four_points_rejected(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["a", "b", "c", "d", "e"]})
        assert spec is None and any("要点" in i and "5" in i for i in issues), f"原因须含具体超限数：{issues}"

    def test_steps_over_six_rejected(self):
        spec, issues = parse_graphic_spec({"type": "steps", "steps": [f"s{i}" for i in range(7)]})
        assert spec is None and issues

    def test_steps_empty_rejected(self):
        spec, issues = parse_graphic_spec({"type": "steps", "steps": []})
        assert spec is None and issues

    def test_big_number_empty_number_rejected(self):
        spec, issues = parse_graphic_spec({"type": "big_number", "number": ""})
        assert spec is None and issues

    def test_non_dict_input_rejected(self):
        assert parse_graphic_spec("text_card")[0] is None
        assert parse_graphic_spec(None)[0] is None


class TestParseRenderBudget:
    """2026-09-02 标题截断修复（产品拍板）：渲染预算=**字段级省略**，不是截断、不是拒帧。

    标题 >14 字 → 置空保留 spec；标签超预算（**2026-09-03 词折算口径**：汉字 1 单位、
    英文词 1 词 2 单位，上限 6——官方指南「短词 3-5 最稳」本就是词数口径；术语型内容
    按字数罚会整帧全灭，vg_aac257fcf7f3 实证 44% 帧拒绝率）→ 丢弃该条；丢弃后 text_card
    要点 <2 / steps 为空才帧级拒绝；其余形状不合法 → (None, 字段级原因)。"""

    def test_title_over_fourteen_omitted_spec_kept_text_card(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "甲" * 15, "points": ["要点甲", "要点乙"]})
        assert spec is not None, "超长标题=字段级省略（卡片照出），不拒帧"
        assert spec["title"] == ""
        assert any("标题超长（15>14）" in i for i in issues), f"issues 须记省略原因：{issues}"

    def test_title_over_fourteen_omitted_spec_kept_steps(self):
        spec, issues = parse_graphic_spec({"type": "steps", "title": "甲" * 15, "steps": ["第一步", "第二步"]})
        assert spec is not None, "steps 同口径：超长标题省略、spec 保留"
        assert "title" not in spec, "steps 标题本就可选（省略即不带该键）"
        assert any("标题超长" in i for i in issues)

    def test_boundary_fourteen_char_title_kept_in_full(self):
        """边界 14 字整（中英混合贴边口径：中、英文字符各计 1 字）完整保留。"""
        title = "小任务慎用Subagent啊"  # len == 14（13 字贴边案例 + 1）
        assert len(title) == 14
        spec, issues = parse_graphic_spec({"type": "text_card", "title": title, "points": ["要点甲", "要点乙"]})
        assert spec is not None and spec["title"] == title
        assert issues == []

    def test_over_six_label_dropped_spec_kept(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["这是一个长标签", "要点甲", "要点乙"]})
        assert spec is not None, "单条标签超长=丢弃该条，不拒帧"
        assert spec["points"] == ["要点甲", "要点乙"], "超长标签整条丢弃（不截断）"
        assert any("要点超长已省略" in i for i in issues)

    def test_drop_leaving_single_point_frame_rejected(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["这是一个长标签", "要点甲"]})
        assert spec is None, "丢弃后仅剩 1 条要点（<2，单点伪归纳）→ 帧级拒绝"
        assert any("要点" in i for i in issues)

    def test_all_steps_dropped_frame_rejected(self):
        spec, issues = parse_graphic_spec({"type": "steps", "steps": ["这是一个长标签", "这是二个长标签"]})
        assert spec is None, "全部步骤被省略 → steps 为空 → 帧级拒绝"
        assert any("步骤" in i for i in issues)


class TestLabelWordFolding:
    """2026-09-03 拍板：标签计数改词折算（汉字 1 单位 + 英文词每词 2 单位 ≤ 6）。

    背景：vg_aac257fcf7f3（Claude Code 教学文稿）按字数口径 16/36 graphic 帧全灭——
    英文术语（Session/Subagent/BASH_MANPAGE）在语义上压不进 6 字，压缩即丢术语、
    卡片白上。渲染可靠性的原始依据（官方指南「短词 3-5 最稳」）是**词数**口径。"""

    def test_english_term_labels_legal_in_full(self):
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["直到Session结束", "每轮消耗token"]})
        assert spec is not None, "术语标签按词计（6/6 单位）→ 合法，不再整帧全灭"
        assert spec["points"] == ["直到Session结束", "每轮消耗token"], "完整保留（不截断）"
        assert issues == []

    def test_multiword_identifier_counts_per_word(self):
        """下划线分隔的标识符按词计：BASH_MANPAGE = 2 词 = 4 单位 ≤ 6。"""
        spec, _ = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["BASH_MANPAGE", "短"]})
        assert spec is not None and "BASH_MANPAGE" in spec["points"]

    def test_pure_chinese_seven_chars_still_over_budget(self):
        """纯中文 7 字=7 单位仍超——词折算只放宽英文，纯中文 ≤6 字口径不变。"""
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["直接附加上下文", "短", "小"]})
        assert spec is not None and spec["points"] == ["短", "小"], "纯中文超长照旧省略"
        assert any("单位" in i for i in issues), "省略原因注明单位口径"

    def test_decimal_number_label_still_omitted(self):
        """数字串按词计：全年营收3.1415万亿元 = 6 汉字 + 2 数字词 = 10 单位 → 省略
        （F-B12-006 用例，词折算不得把它放进标签位）。"""
        spec, issues = parse_graphic_spec({"type": "text_card", "title": "t", "points": ["全年营收3.1415万亿元", "短", "小"]})
        assert spec is not None and spec["points"] == ["短", "小"]
        assert any("要点超长已省略" in i for i in issues)

    def test_steps_labels_same_folding(self):
        spec, _ = parse_graphic_spec({"type": "steps", "steps": ["新开Session", "运行/compact"]})
        assert spec is not None and spec["steps"] == ["新开Session", "运行/compact"]


class TestValidateAgainstNarration:
    """逐字符核对：spec 数字不在旁白/文稿中 → 拒绝；原因可读。"""

    def test_number_present_in_narration_ok(self):
        spec = {"type": "big_number", "number": "37", "unit": "%", "description": "增长"}
        ok, reasons = validate_spec_against_narration(spec, "去年增长了37%，非常可观。", "去年增长了37%，非常可观。")
        assert ok, f"37% 在旁白中，应通过：{reasons}"

    def test_hallucinated_number_rejected(self):
        spec = {"type": "big_number", "number": "48", "unit": "%", "description": "增长"}
        ok, reasons = validate_spec_against_narration(spec, "去年增长了37%，非常可观。", "去年增长了37%，非常可观。")
        assert not ok, "48 是幻觉数字，必须拒绝"
        assert any("48" in r for r in reasons), f"拒绝原因须含具体数字：{reasons}"

    def test_number_in_full_script_ok(self):
        """数字只在全文稿（其他镜旁白）出现也算可信——全文稿是核对池。"""
        spec = {"type": "big_number", "number": "120", "unit": "亿"}
        ok, _ = validate_spec_against_narration(spec, "规模非常庞大。", "开头。规模达120亿。结尾。")
        assert ok

    def test_fullwidth_percent_normalized(self):
        """旁白全角％ vs spec 半角 % → 归一化后命中。"""
        spec = {"type": "big_number", "number": "50", "unit": "%"}
        ok, _ = validate_spec_against_narration(spec, "覆盖率高达５０％以上。", "覆盖率高达５０％以上。")
        assert ok

    def test_thousands_separator_normalized(self):
        """千分位逗号：spec "1,234" vs 旁白 "1234"（及反向）→ 命中。"""
        spec_a = {"type": "big_number", "number": "1,234", "unit": "万"}
        ok_a, _ = validate_spec_against_narration(spec_a, "用户数达1234万。", "用户数达1234万。")
        assert ok_a
        spec_b = {"type": "big_number", "number": "1234", "unit": "万"}
        ok_b, _ = validate_spec_against_narration(spec_b, "用户数达1,234万。", "用户数达1,234万。")
        assert ok_b

    def test_decimal_number(self):
        spec = {"type": "big_number", "number": "1.5", "unit": "倍"}
        ok, _ = validate_spec_against_narration(spec, "效率提升1.5倍。", "效率提升1.5倍。")
        assert ok

    def test_text_card_point_numbers_validated(self):
        """text_card 要点里的数字也要核对——不只 big_number。"""
        spec = {"type": "text_card", "title": "关键数据", "points": ["转化率高达92%"]}
        ok, reasons = validate_spec_against_narration(spec, "实际转化率是29%。", "实际转化率是29%。")
        assert not ok, "要点里的 92% 是幻觉，必须拒绝"

    def test_text_card_pure_prose_ok(self):
        """纯文字要点（无数字）不做逐字核对——标题/要点允许 LLM 概括。"""
        spec = {"type": "text_card", "title": "核心概念", "points": ["免疫力是防线"]}
        ok, _ = validate_spec_against_narration(spec, "免疫力就像身体里的军队一样。", "免疫力就像身体里的军队一样。")
        assert ok

    def test_steps_number_validated(self):
        spec = {"type": "steps", "title": "三步法", "steps": ["预热到180度", "搅拌"]}
        ok, _ = validate_spec_against_narration(spec, "先预热到180度，再搅拌。", "先预热到180度，再搅拌。")
        assert ok
        bad = {"type": "steps", "title": "三步法", "steps": ["预热到200度", "搅拌"]}
        ok2, _ = validate_spec_against_narration(bad, "先预热到180度，再搅拌。", "先预热到180度，再搅拌。")
        assert not ok2

    def test_unit_variant_whitelist(self):
        """单位变体：spec unit 与旁白单位写法不同但在白名单内 → 命中（无单位数字不含 unit）。"""
        # 无 unit 字段的 big_number：只核对 number
        spec = {"type": "big_number", "number": "37"}
        ok, _ = validate_spec_against_narration(spec, "增长了37个百分点。", "增长了37个百分点。")
        assert ok


class TestStoryboardGraphicSpecField:
    """storyboard 持久化：新字段经 to_dict/from_dict 自动兼容（asdict + 白名单）。"""

    def _sb(self) -> Storyboard:
        return Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
            frames=[
                StoryboardFrame(index=0, narration="n0", visual_source="graphic", graphic_spec={"type": "big_number", "number": "37", "unit": "%"}),
                StoryboardFrame(index=1, narration="n1"),
            ],
        )

    def test_roundtrip_preserves_graphic_spec(self):
        sb = self._sb()
        data = sb.to_dict()
        assert data["frames"][0]["graphic_spec"] == {"type": "big_number", "number": "37", "unit": "%"}
        restored = Storyboard.from_dict(data)
        assert restored.frames[0].graphic_spec == {"type": "big_number", "number": "37", "unit": "%"}
        assert restored.frames[1].graphic_spec is None

    def test_unknown_fields_still_ignored_on_from_dict(self):
        """from_dict 白名单：带未知字段的旧格式不崩、新字段能进来。"""
        data = self._sb().to_dict()
        data["frames"][0]["future_field"] = "x"
        restored = Storyboard.from_dict(data)
        assert restored.frames[0].graphic_spec is not None


class TestSplitFrameDropsGraphicSpec:
    """T1 拆帧路径：超长旁白拆帧显式 kwargs 重建 → graphic_spec=None（旁白已改写，
    spec 失效，渲染期按常规画面出图）+ visual_source 继承子帧。"""

    def test_split_graphic_frame_keeps_source_drops_spec(self, tmp_path):
        from app.gateway.video_gen.service import VideoGenService

        svc = VideoGenService.__new__(VideoGenService)
        long_narration = "这是一个非常长的旁白句子用于测试拆分功能是否正常工作。" * 3
        storyboard = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
            frames=[
                StoryboardFrame(
                    index=0,
                    narration=long_narration,
                    image_prompt="p",
                    visual_source="graphic",
                    graphic_spec={"type": "text_card", "title": "t", "points": ["a"]},
                ),
            ],
        )
        import asyncio

        asyncio.run(svc._split_long_narration_frames(storyboard, tmp_path))
        assert len(storyboard.frames) >= 2, "超长旁白应被拆分"
        for f in storyboard.frames:
            assert f.visual_source == "graphic", "visual_source 必须继承子帧"
            assert f.graphic_spec is None, "拆帧后 graphic_spec 必须置空（旁白已改写，旧 spec 失效）"


class TestParseTightenedForExtraction:
    """T6：提取期收紧——text_card 须 ≥2 条要点（单点=伪归纳，形状不合法）。

    保底 text_card_from_narration 已于 2026-08-17 随 Pillow 渲染器退役——spec=None
    现按普通画面渲染（AI 主引擎），收紧只作用于提取期 parse。"""

    def test_parse_rejects_single_point_text_card(self):
        assert parse_graphic_spec({"type": "text_card", "title": "t", "points": ["唯一一条"]})[0] is None

    def test_parse_accepts_two_points(self):
        assert parse_graphic_spec({"type": "text_card", "title": "t", "points": ["a", "b"]})[0] is not None


class TestNumberTokenHelper:
    """T7 复用：文稿级数字存在性判定（graphic_spec 导出，visual_routing 共用）。"""

    def test_has_number_true_false(self):
        from app.gateway.video_gen.visual_sources.graphic_spec import text_has_number

        assert text_has_number("增长37%") is True
        assert text_has_number("达到1.5倍") is True
        assert text_has_number("覆盖１２个城市") is True, "全角数字也算"
        assert text_has_number("纯文字没有任何数字") is False
        assert text_has_number("") is False

"""字幕引擎核心测试（T1/T2/T3/T5/T8）—— subtitle_engine 模块。

覆盖 tokenize→cut→kinsoku→clean→join→align→post→write_ass 全链路。
"""

# ── 辅助 ────────────────────────────────────────────────────────────────────
import re

import pytest

from app.gateway.video_gen.subtitle_engine import (
    Cue,
    Token,
    _format_ass_time,
    _format_srt_time,
    _text_display_width,
    clean,
    compute_style_params,
    cut_chunks,
    join_tokens,
    layout_two_lines,
    llm_resegment,
    normalize_word_cues,
    segment,
    srt_to_ass,
    tokenize,
    write_ass,
    write_srt,
)

_WS = re.compile(r"\s")


def _norm(s: str) -> str:
    """归一化用于不丢字比对：去所有空白和行尾标点（clean 会去除）。"""
    _PUNCT = "。！？!?；;…、，,：:."
    return _WS.sub("", s).translate(str.maketrans("", "", _PUNCT))


# ═══════════════════════════════════════════════════════════════════════════
# T1: tokenize → cut_chunks → clean → join_tokens
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenize:
    def test_cjk_each_char_is_token(self):
        tokens = tokenize("你好世界")
        assert len(tokens) == 4
        assert [t.text for t in tokens] == ["你", "好", "世", "界"]

    def test_latin_word_is_single_token(self):
        tokens = tokenize("Engineering")
        assert len(tokens) == 1
        assert tokens[0].text == "Engineering"

    def test_cjk_latin_mixed(self):
        tokens = tokenize("就是Graph")
        assert [t.text for t in tokens] == ["就", "是", "Graph"]

    def test_dash_is_unbreakable(self):
        tokens = tokenize("——")
        assert len(tokens) == 1
        assert tokens[0].unbreakable is True

    def test_ellipsis_is_unbreakable(self):
        tokens = tokenize("……")
        assert len(tokens) == 1
        assert tokens[0].unbreakable is True

    def test_sentence_end_punct_no_line_start(self):
        tokens = tokenize("。")
        assert tokens[0].no_line_start is True

    def test_clause_punct_no_line_start(self):
        for ch in ("，", "、", "；", "："):
            tokens = tokenize(ch)
            assert tokens[0].no_line_start is True, f"{ch!r} 应标记避头"

    def test_opening_paren_no_line_end(self):
        for ch in ("（", "【", "《"):
            tokens = tokenize(ch)
            assert tokens[0].no_line_end is True, f"{ch!r} 应标记避尾"

    def test_closing_paren_no_line_start(self):
        for ch in ("）", "】", "》"):
            tokens = tokenize(ch)
            assert tokens[0].no_line_start is True, f"{ch!r} 应标记避头"

    def test_empty_string(self):
        assert tokenize("") == []

    def test_digit_sequence_unbreakable(self):
        """多位数字串不拆（CLReq §6.1）。"""
        tokens = tokenize("我们有3.14159个")
        texts = [t.text for t in tokens]
        assert "3.14159" in texts

    def test_space_is_token(self):
        tokens = tokenize("hello world")
        texts = [t.text for t in tokens]
        assert " " in texts or len([t for t in tokens if t.kind == "space"]) >= 1


class TestCutChunks:
    def test_short_text_single_chunk(self):
        tokens = tokenize("短句")
        chunks = cut_chunks(tokens, max_width=16)
        assert len(chunks) == 1
        assert _norm(chunks[0]) == "短句"

    def test_no_text_loss(self):
        text = "首先介绍视频生成的整体架构和核心流程以及各个模块的职责划分然后演示"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=16)
        assert _norm("".join(chunks)) == _norm(text), "切分不得丢字"

    def test_english_word_not_split(self):
        """Engineering 不得被切成 Engineeri/ng。"""
        text = "使用Graph Engineering可以加速"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=16)
        joined = "".join(chunks)
        assert "Engineering" in joined, "英文词不得被拆"

    def test_line_start_no_punctuation(self):
        """R2: 切点后首字符不得为避头标点（，、。等不得跑到行首）。"""
        text = "这个词的起源来自OpenAI联合创始人前特斯拉AI总监Andrej"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=16)
        no_start = "，。、；：？！）》」』】…,.)%:;?]}~"
        for chunk in chunks:
            assert chunk[0] not in no_start, f"行首出现避头标点：{chunk!r}"

    def test_dash_not_split(self):
        """—— 不可拆：不得被分到两个 chunk。"""
        text = "第一段内容很长需要被切分——第二段也很长需要被切分啊"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=16)
        joined = "".join(chunks)
        assert "——" in joined, "—— 不得被拆开"

    def test_oversized_word_hard_cut_no_loss(self):
        """整词超宽时硬切但不丢字。"""
        text = "ainternationalization"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=8)
        assert _norm("".join(chunks)) == _norm(text), "硬切不得丢字"

    # ── 2026-08-14 迁移自 test_subtitle_wrap.py（旧 _split_narration/_adjust_cut 契约）──

    def test_mixed_cjk_latin_breaks_at_word_boundary(self):
        """混排文本西文词不得腰斩（'artificial' 整词保留）。"""
        text = "模式可以加速 artificial intelligence 的训练"
        chunks = cut_chunks(tokenize(text), max_width=16)
        joined = "".join(chunks)
        assert "artificial" in joined, "西文词被腰斩"
        assert _norm(joined) == _norm(text), "切分不得丢字"

    def test_pure_english_breaks_at_spaces(self):
        """纯英文按空格断，不得造新词。"""
        text = "use development mode to speed up artificial intelligence"
        words = set(text.split())
        chunks = cut_chunks(tokenize(text), max_width=16)
        assert _norm(" ".join(chunks)) == _norm(text), "切分不得丢字"
        for chunk in chunks:
            for word in chunk.split():
                assert word in words, f"断出新词 {word!r}"

    def test_cut_at_cjk_boundary_no_regression(self):
        """切点不在西文词内（纯 CJK）→ 按宽度硬切，零回归、不丢字。"""
        text = "这是一个非常长的旁白需要被自动切分成"
        chunks = cut_chunks(tokenize(text), max_width=16)
        assert _norm("".join(chunks)) == _norm(text), "切分不得丢字"
        assert all(len(c) <= 16 for c in chunks), f"chunk 超宽: {chunks}"

    def test_oversized_latin_word_keeps_hard_cut(self):
        """'internationalization'（20 宽 > 16）无边界可退 → 硬切但不丢字。"""
        text = "internationalization"
        chunks = cut_chunks(tokenize(text), max_width=16)
        assert "".join(chunks) == text, f"丢字：{chunks!r}"

    def test_break_at_sentence_end_punctuation(self):
        text = "第一句话。第二句话。第三句话。"
        tokens = tokenize(text)
        chunks = cut_chunks(tokens, max_width=16)
        assert len(chunks) == 3

    def test_pure_punctuation_chunk_cleaned_to_empty(self):
        """纯标点 → clean 后为空（防 libass 崩）。"""
        assert clean("。。。") == ""
        assert clean("，、；") == ""

    def test_trailing_punct_stripped(self):
        assert clean("你好。") == "你好"
        assert clean("测试一下！") == "测试一下"

    def test_leading_punct_stripped(self):
        """R2 修复：行首标点也去除（，它是图的最小单位 → 它是图的最小单位）。"""
        assert clean("，它是图的最小单位") == "它是图的最小单位"

    def test_interior_punct_preserved(self):
        """D5：句内标点保留。"""
        assert clean("因为这样，所以那样。") == "因为这样，所以那样"

    def test_multiline_clean(self):
        assert clean("第一行。\n第二行！") == "第一行\n第二行"


class TestJoinTokens:
    def test_cjk_latin_boundary_inserts_space(self):
        """D2：CJK↔Latin 边界插空格。"""
        tokens = tokenize("就是Graph")
        result = join_tokens(tokens)
        assert " " in result
        assert "就是" in result
        assert "Graph" in result

    def test_cjk_cjk_no_space(self):
        tokens = tokenize("人工智能")
        result = join_tokens(tokens)
        assert " " not in result

    def test_latin_latin_keep_space(self):
        tokens = tokenize("use development")
        result = join_tokens(tokens)
        assert "use development" in result

    def test_fullwidth_punct_no_space(self):
        """全角标点两侧不加空格。"""
        tokens = tokenize("你好，世界")
        result = join_tokens(tokens)
        assert "你好，世界" == result

    def test_cjk_latin_mixed_sentence(self):
        """综合：使用 development 模式可以加速 → 有空格。"""
        tokens = tokenize("使用development模式可以加速")
        result = join_tokens(tokens)
        # D2: CJK↔Latin 交界处应有空格
        assert "使用 development" in result or "使用development" not in result


# ═══════════════════════════════════════════════════════════════════════════
# T2: segment（align + post + 两行折行）
# ═══════════════════════════════════════════════════════════════════════════


class TestSegment:
    def test_basic_segment_landscape(self):
        narration = "首先介绍视频生成的整体架构和核心流程。然后演示如何创建第一个任务并等待草稿完成。"
        cues = segment(narration, audio_duration=10.0, orientation="landscape")
        assert len(cues) >= 2
        # 所有 cue 拼接不丢字
        joined = _norm("".join(c.text.replace("\\N", "") for c in cues))
        assert joined == _norm(narration)

    def test_segment_continuous_timing(self):
        narration = "第一帧的旁白内容比较长需要被切分。第二帧旁白也比较长也需要被切分。"
        cues = segment(narration, audio_duration=8.0, orientation="landscape")
        for i in range(1, len(cues)):
            assert cues[i].start >= cues[i - 1].end - 0.001, "时间区间须连续不重叠"

    def test_segment_total_duration_preserved(self):
        narration = "一段中等长度的旁白需要被切分。"
        cues = segment(narration, audio_duration=5.0, orientation="landscape")
        assert abs(cues[-1].end - 5.0) < 0.01

    def test_tiny_chunk_merged(self):
        """碎条合并：<min_dur 或 ≤3 全角当量的段并入相邻段（修 R4）。"""
        narration = "好的。这是一个正常长度的旁白内容。文。"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        # "文。" 不应独立成 cue
        for c in cues:
            dur = c.end - c.start
            assert dur >= 1.0 - 0.1, f"过短 cue (<1s) 未被合并：{c.text!r} dur={dur:.2f}"

    def test_cps_limit(self):
        """CPS 超标 → 再拆。"""
        # 16全角字 + 1秒 = 16字/秒 >> 9字/秒 → 应被再拆
        narration = "人工智能正在深刻改变我们的生活工作方式和学习方式"
        cues = segment(narration, audio_duration=2.0, orientation="landscape", max_cps=9.0)
        for c in cues:
            dur = max(c.end - c.start, 0.01)
            text_len = len(c.text.replace("\\N", ""))
            # 留余量：实测可能因 max_dur 约束略有出入
            assert text_len / dur <= 9.0 * 2.0, f"CPS={text_len / dur:.1f} 超标太多：{c.text!r}"

    def test_gap_between_cues(self):
        """相邻 cue 间隙 ≥80ms。"""
        narration = "第一段旁白。第二段旁白。第三段旁白。"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        for i in range(1, len(cues)):
            gap = cues[i].start - cues[i - 1].end
            assert gap >= -0.001, f"cue 重叠：{cues[i - 1]} → {cues[i]}"

    def test_empty_narration_returns_empty(self):
        cues = segment("", audio_duration=5.0, orientation="landscape")
        assert cues == []

    def test_pure_punctuation_returns_empty(self):
        """纯标点旁白 → 空（防 libass 崩）。"""
        cues = segment("。。。", audio_duration=3.0, orientation="landscape")
        assert cues == []

    def test_two_line_layout_n_break(self):
        """超单行宽的 cue 含 \\N 折行（合并后宽度超 max_width）。"""
        # 啊。被 clean 成 啊（1 字），cut 后与相邻 chunk 合并 → 合并后 >16 全角当量 → \\N 折行
        narration = "啊。这是一个非常长的字幕需要被自动断行成多行显示在画面底部"
        cues = segment(narration, audio_duration=10.0, orientation="landscape")
        has_n = any("\\N" in c.text for c in cues)
        assert has_n, "超宽 cue 应含 \\N 折行标记"

    def test_pyramid_layout(self):
        """D1: 下重金字塔——上行短下行长。"""
        narration = "这是一个非常长的旁白需要被自动断行成多行显示在画面底部"
        cues = segment(narration, audio_duration=4.0, orientation="landscape")
        for c in cues:
            if "\\N" in c.text:
                lines = c.text.split("\\N")
                if len(lines) == 2:
                    top_w = len(lines[0])
                    bot_w = len(lines[1])
                    # 上行≤下行（金字塔），或上行≤2字（无解时例外）
                    if top_w > 2:
                        assert top_w <= bot_w, f"非金字塔：{c.text!r} (上{top_w} > 下{bot_w})"

    def test_word_cues_preserve_timestamps(self):
        """有 word_cues 时保留真实时间戳——逐词粒度（P1-2 修复）。"""
        narration = "人工智能正在改变世界"
        word_cues = [
            ("人工", 0.0, 0.5),
            ("智能", 0.5, 1.0),
            ("正在", 1.0, 1.5),
            ("改变", 1.5, 2.0),
            ("世界", 2.0, 2.5),
        ]
        cues = segment(narration, word_cues=word_cues, orientation="landscape")
        assert cues[0].start == pytest.approx(0.0)
        assert cues[-1].end == pytest.approx(2.5)
        # P1-2: 中间 cue 的时间戳应该反映真实 word_cue 边界，
        # 不是简单按宽度均分（修前均分会在无标点长句中产生 3.5s 切点偏离真实 5.0s）
        for i in range(1, len(cues)):
            assert cues[i].start >= cues[i - 1].end - 0.01, f"cue 不连续：{cues[i - 1]} → {cues[i]}"

    def test_word_cues_per_word_timestamp_resolution(self):
        """P1-2: edge 逐词时间戳必须保留粒度——中间 cue 的 start 落在真实 word 边界。"""
        # 10 个词，每词 1s，切成 2 个 cue（每 cue 含 ~5 词）
        words = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
        word_cues = [(w, float(i), float(i + 1)) for i, w in enumerate(words)]
        narration = "一二三四五六七八九十"
        cues = segment(narration, word_cues=word_cues, orientation="landscape", max_cps=100.0)
        if len(cues) >= 2:
            # 第一个 cue 的 end 应接近某个真实 word 边界（非均分点）
            # words[4] 的 end = 5.0, words[5] 的 end = 6.0
            boundary = cues[0].end
            # 允许 ±0.01 容差
            assert any(abs(boundary - w_end) < 0.5 for _, _, w_end in word_cues), f"cue 边界 {boundary} 偏离所有 word_cue 端点"

    def test_word_cues_none_falls_back_to_width_ratio(self):
        """无 word_cues → 宽度占比分摊时长。"""
        narration = "第一段短句。第二段长句子啊。"
        cues = segment(narration, audio_duration=10.0, orientation="landscape")
        assert len(cues) >= 2
        # 短句占比小、长句占比大
        if len(cues) == 2:
            assert cues[0].end - cues[0].start < cues[1].end - cues[1].start


class TestSegmentTailMerge:
    """P0-1 回归：过短尾段并入前段时文本必须一并合并，绝不丢字。"""

    def test_single_char_tail_not_dropped(self):
        narration = "一二三四五六七八九十，甲乙丙丁，好"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert "好" in joined, f"单字尾段被吞：{cues!r}"

    def test_ascii_tail_not_dropped(self):
        narration = "这是一个非常长的旁白内容测试啊，AI"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert "AI" in joined, f"ASCII 尾段被吞：{cues!r}"

    def test_tail_merge_keeps_cjk_latin_spacing(self):
        """并入前段时走 join_tokens——CJK↔Latin 边界保留空格。"""
        narration = "这是一个非常长的旁白内容测试啊，AI"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert _norm(joined) == _norm(narration)


class TestGlobalInvariants:
    """全局不变量：零丢字 / 无空文本 cue / 时间单调不重叠 / 时间戳合法。"""

    @pytest.mark.parametrize(
        "narration",
        [
            "这是一个非常长的旁白内容测试啊，好",  # 单字尾
            "这是一个非常长的旁白内容测试啊，AI",  # 双字母尾
            "视频生成字幕渲染流程介绍，结合 AI 技术",  # 中英混排
            "这是前半部分内容——后半部分也很长需要显示",  # 破折号
            "这是前半部分内容……后半部分也很长需要显示",  # 省略号
            "这是一个正常长度的旁白内容。",  # 纯标点结尾
        ],
    )
    def test_zero_text_loss(self, narration):
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert _norm(joined) == _norm(narration), f"零丢字不变量破坏：{cues!r}"

    @pytest.mark.parametrize("audio_duration", [3.0, 6.0, 10.0, 60.0])
    def test_invariants_across_durations(self, audio_duration):
        narration = "这是一个非常长的旁白内容测试啊，好。第二部分旁白内容，AI 技术的结合——结尾"
        cues = segment(narration, audio_duration=audio_duration, orientation="landscape")
        assert cues, "非空旁白应产出 cue"
        for c in cues:
            assert c.text.strip(), f"空文本 cue：{c!r}"
        for i in range(1, len(cues)):
            assert cues[i].start >= cues[i - 1].end - 1e-3, f"时间重叠：{cues[i - 1]} → {cues[i]}"
        # 时间戳格式合法：格式化后秒/分不得为 60
        for c in cues:
            for ts in (_format_srt_time(c.start), _format_srt_time(c.end)):
                h, m, rest = ts.split(":")
                s, _ = rest.split(",")
                assert int(m) < 60 and int(s) < 60, f"非法时间戳：{ts}"


class TestSplitGuardRails:
    """T3（P1-6）：拆分产物必须再过护栏。"""

    def test_split_products_no_slivers(self):
        """拆分产物不得出现 <0.5s 闪条（并回前段），且不丢字。"""
        narration = "这是第一段旁白内容，这是第二段旁白内容"
        cues = segment(narration, audio_duration=0.8, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert _norm(joined) == _norm(narration), f"丢字：{cues!r}"
        for c in cues:
            assert c.end - c.start >= 0.5 - 1e-6, f"<0.5s 闪条：{c!r} dur={c.end - c.start:.3f}"

    def test_long_cue_split_no_slivers_no_loss(self):
        """30 字 30s：长 cue 被拆为 ≤max_dur 片，无 <0.5s 片、无丢字。"""
        narration = "一二三四五六七八九十" * 3
        cues = segment(narration, audio_duration=30.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert _norm(joined) == _norm(narration), f"丢字：{cues!r}"
        for c in cues:
            assert c.end - c.start >= 0.5 - 1e-6, f"<0.5s 片：{c!r}"


class TestMaxDurGate:
    """T4（P0-6）：max_dur/CPS 门槛去除宽度前置。"""

    def test_no_cue_exceeds_max_dur(self):
        """100 字 @100s：不存在时长 >7s（±0.1 容差）的 cue。"""
        narration = "一二三四五六七八九十" * 10
        cues = segment(narration, audio_duration=100.0, orientation="landscape")
        for c in cues:
            assert c.end - c.start <= 7.0 + 0.1, f"超长 cue：{c.text[:12]!r} dur={c.end - c.start:.2f}"

    def test_narrow_cue_exceeds_max_dur_is_split(self):
        """窄 cue（≤max_width）超时同样拆——旧代码 width 前置使其豁免。"""
        narration = "这是第一段旁白内容，这是第二段旁白内容"
        cues = segment(narration, audio_duration=20.0, orientation="landscape")
        for c in cues:
            assert c.end - c.start <= 7.0 + 0.1, f"窄超时 cue 未拆：{c.text[:12]!r} dur={c.end - c.start:.2f}"

    def test_cps_sane_after_split(self):
        """拆分后 CPS ≤ max_cps（±容差）。"""
        narration = "一二三四五六七八九十" * 10
        cues = segment(narration, audio_duration=100.0, orientation="landscape")
        for c in cues:
            dur = max(c.end - c.start, 0.001)
            w = _text_display_width(c.text.replace("\\N", ""))
            assert w / dur <= 9.0 * 1.5, f"CPS 超标：{c.text[:12]!r} {w / dur:.1f}"


class TestLayoutTwoLines:
    def test_short_text_no_break(self):
        result = layout_two_lines("短句", max_width=16)
        assert "\\N" not in result

    def test_long_text_breaks(self):
        text = "这是一个非常长的字幕需要被自动断行成多行"
        result = layout_two_lines(text, max_width=16)
        assert "\\N" in result
        lines = result.split("\\N")
        assert len(lines) == 2

    def test_break_at_punctuation(self):
        text = "第一部分内容，第二部分也很长需要显示"
        result = layout_two_lines(text, max_width=16)
        lines = result.split("\\N")
        # 断点应在标点附近
        assert len(lines) == 2

    def test_pyramid_shape(self):
        text = "这是一个非常长的字幕需要被自动断行成多行显示在画面底部"
        result = layout_two_lines(text, max_width=16)
        if "\\N" in result:
            lines = result.split("\\N")
            if len(lines) == 2 and len(lines[0]) > 2:
                assert len(lines[0]) <= len(lines[1])


# ═══════════════════════════════════════════════════════════════════════════
# T3: ASS writer + SRT 双写
# ═══════════════════════════════════════════════════════════════════════════


class TestFormatTime:
    """时间戳格式合法性（P0-4 进位修复）——秒/分不得出现 60。"""

    def test_ass_cs_carry_into_seconds(self):
        assert _format_ass_time(59.9996) == "0:01:00.00"

    def test_ass_carry_into_hours(self):
        assert _format_ass_time(3599.9996) == "1:00:00.00"

    def test_srt_ms_carry_into_seconds(self):
        assert _format_srt_time(59.9999) == "00:01:00,000"

    def test_srt_carry_into_minutes(self):
        assert _format_srt_time(119.9996) == "00:02:00,000"

    def test_normal_values_unchanged(self):
        """既有正常值断言保持。"""
        assert _format_ass_time(1.5) == "0:00:01.50"
        assert _format_srt_time(0.0) == "00:00:00,000"
        assert _format_srt_time(5.2) == "00:00:05,200"
        assert _format_srt_time(65.5) == "00:01:05,500"
        assert _format_srt_time(3661.123) == "01:01:01,123"


class TestComputeStyleParams:
    def test_portrait_fontsize(self):
        params = compute_style_params(1080, 1920, "default")
        assert params["fontsize"] == 70  # 1920 × 3% ≈ 58，×1.2 ≈ 70（2026-08-20 上调）

    def test_landscape_1080p_fontsize(self):
        params = compute_style_params(1920, 1080, "default")
        assert params["fontsize"] == 54  # 1080 × 4% ≈ 44，×1.2 ≈ 54（2026-08-20 上调）

    def test_landscape_720p_fontsize(self):
        params = compute_style_params(1280, 720, "default")
        assert params["fontsize"] == 36  # 720 × 4% ≈ 30，×1.2 = 36（2026-08-20 上调）

    def test_portrait_bold_freed_from_width_clamp(self):
        """竖屏 cap 修复回归：cap 从 0.9×w/16=60 修正为 0.9×w/13=74——
        bold 不再被钳到 60，解放到 72（1920×3%≈58 ×1.25≈72）。"""
        params = compute_style_params(1080, 1920, "bold")
        assert params["fontsize"] == 72

    def test_outline_proportional(self):
        params = compute_style_params(1080, 1920, "default")
        assert params["outline"] >= 2  # ≈ Fontsize × 0.06

    def test_marginv_portrait(self):
        params = compute_style_params(1080, 1920, "default")
        assert abs(params["marginv"] - 192) < 10  # 1920 × 0.10 ≈ 192

    def test_marginv_landscape(self):
        params = compute_style_params(1920, 1080, "default")
        assert abs(params["marginv"] - 65) < 10  # 1080 × 0.06 ≈ 65

    def test_all_styles_present(self):
        for style in ("default", "bold", "minimal", "outline"):
            params = compute_style_params(1920, 1080, style)
            assert "fontsize" in params
            assert "outline" in params
            assert "marginv" in params


class TestStyleDifferentiation:
    """T5（P0-3）：四样式参数两两可区分，default 数值冻结。"""

    def test_four_styles_mutually_distinct(self):
        params = [compute_style_params(1920, 1080, s) for s in ("default", "bold", "minimal", "outline")]
        for i in range(len(params)):
            for j in range(i + 1, len(params)):
                assert params[i] != params[j], f"样式参数坍缩：{params[i]} == {params[j]}"

    def test_fontsize_ordering(self):
        sizes = {s: compute_style_params(1920, 1080, s)["fontsize"] for s in ("default", "bold", "minimal", "outline")}
        # 2026-08-20：outline 与 bold 同号（1.25，用户拍板——1.3 会全面倒挂
        # outline>bold，且 default/bold 间仅 2px 缝隙塞不下严格居中值）
        assert sizes["bold"] >= sizes["outline"] > sizes["default"] > sizes["minimal"], sizes

    def test_outline_ordering(self):
        outlines = {s: compute_style_params(1920, 1080, s)["outline"] for s in ("default", "bold", "minimal", "outline")}
        assert outlines["outline"] > outlines["default"] > outlines["minimal"], outlines

    def test_styles_distinct_even_under_portrait_clamp(self):
        """竖屏 cap 修复（0.9×w/16=60 → 0.9×w/13=74）后 bold/outline fontsize 同为 72
        （同号，2026-08-20 拍板）——靠 outline 宽度与 bold 标志组合区分。"""
        params = [compute_style_params(1080, 1920, s) for s in ("default", "bold", "minimal", "outline")]
        for i in range(len(params)):
            for j in range(i + 1, len(params)):
                assert params[i] != params[j], f"竖屏样式参数坍缩：{params[i]} == {params[j]}"


class TestWriteAss:
    def test_playres_matches_resolution(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1080, video_h=1920)
        content = out.read_text(encoding="utf-8")
        assert "PlayResX: 1080" in content
        assert "PlayResY: 1920" in content

    def test_four_styles_present(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        for style in ("Default", "Bold", "Minimal", "Outline"):
            assert f"Style: {style}" in content, f"缺少样式 {style}"

    def test_fontname_noto(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "Noto Sans CJK SC" in content

    def test_wrap_style_2(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "WrapStyle: 2" in content

    def test_scaled_border_and_shadow(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "ScaledBorderAndShadow: yes" in content

    def test_dialogue_time_format(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        # ASS time format: H:MM:SS.cc
        assert "0:00:00.00" in content
        assert "0:00:02.00" in content

    def test_n_preserved_in_dialogue(self, tmp_path):
        cues = [Cue(text="第一行\\N第二行", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "\\N" in content

    def test_style_param_applied(self, tmp_path):
        cues = [Cue(text="测试", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080, style="bold")
        content = out.read_text(encoding="utf-8")
        # bold 样式应被使用
        assert "Bold" in content

    def test_empty_cues_writes_valid_header(self, tmp_path):
        """空 cue 列表不得写出破坏性 ASS（libass 崩溃回归）。"""
        out = tmp_path / "test.ass"
        write_ass([], out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "[Script Info]" in content
        assert "[V4+ Styles]" in content


class TestWriteSrt:
    def test_basic_srt(self, tmp_path):
        cues = [Cue(text="你好", start=0.0, end=2.0)]
        out = tmp_path / "test.srt"
        write_srt(cues, out_path=str(out))
        content = out.read_text(encoding="utf-8")
        assert "00:00:00,000 --> 00:00:02,000" in content
        assert "你好" in content

    def test_n_in_srt_becomes_newline(self, tmp_path):
        cues = [Cue(text="第一行\\N第二行", start=0.0, end=2.0)]
        out = tmp_path / "test.srt"
        write_srt(cues, out_path=str(out))
        content = out.read_text(encoding="utf-8")
        # SRT uses real newlines, not \N
        assert "第一行" in content
        assert "第二行" in content


class TestSrtToAss:
    def test_round_trip(self, tmp_path):
        """locked SRT → ASS 派生：PlayRes + 样式 + \\N 保留。"""
        srt_content = "1\n00:00:00,000 --> 00:00:02,000\n你好世界\n\n2\n00:00:02,000 --> 00:00:05,000\n第二段\n"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        ass_path = tmp_path / "subtitle.ass"
        srt_to_ass(str(srt_path), out_path=str(ass_path), video_w=1080, video_h=1920)
        ass_content = ass_path.read_text(encoding="utf-8")
        assert "PlayResX: 1080" in ass_content
        assert "PlayResY: 1920" in ass_content
        assert "你好世界" in ass_content
        assert "第二段" in ass_content

    def test_locked_srt_preserves_user_text(self, tmp_path):
        """locked SRT 的用户换行/文本原样保留，不做 kinsoku 再处理。"""
        srt_content = "1\n00:00:00,000 --> 00:00:03,000\n用户手写的字幕文本\n\n"
        srt_path = tmp_path / "subtitle.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        ass_path = tmp_path / "subtitle.ass"
        srt_to_ass(str(srt_path), out_path=str(ass_path), video_w=1920, video_h=1080)
        ass_content = ass_path.read_text(encoding="utf-8")
        assert "用户手写的字幕文本" in ass_content


# ═══════════════════════════════════════════════════════════════════════════
# T5: normalize_word_cues (edge-tts token 重对齐)
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeWordCues:
    def test_basic_merge(self):
        """相邻 token 合并 + 时间戳保留。"""
        ref = "人工智能正在改变"
        cues = [("人工", 0.0, 0.5), ("智能", 0.5, 1.0), ("正在", 1.0, 1.5), ("改变", 1.5, 2.0)]
        result = normalize_word_cues(cues, ref)
        assert result is not None
        assert len(result) >= 1

    def test_mismatch_returns_none(self):
        """失配 → None（调用方回退宽度占比）。"""
        ref = "完全不同的文本内容"
        cues = [("hello", 0.0, 1.0), ("world", 1.0, 2.0)]
        result = normalize_word_cues(cues, ref)
        assert result is None

    def test_empty_cues_returns_none(self):
        assert normalize_word_cues([], "text") is None

    def test_punctuation_adsorbed(self):
        """标点吸附：游离标点贴到相邻词。"""
        ref = "你好世界。"
        cues = [("你好", 0.0, 1.0), ("世界", 1.0, 2.0), ("。", 2.0, 2.1)]
        result = normalize_word_cues(cues, ref)
        assert result is not None
        # 句号应被吸附到"世界"
        joined = "".join(t for t, _, _ in result)
        assert "。" in joined


class TestNormalizeWordCuesDoubaoShapes:
    """T-A3b（2026-09-09 feedback-round1 D4）：豆包 event-364 词级数据四形态。

    D4 拍板后豆包任务从「srt 空 → width-ratio 估算」升级为「实测词级」，
    走与 edge 相同的 word_cues → normalize_word_cues → segment 路径。本类锁定
    豆包实际产出的 cue 形态全部归一成功、失配形态回退（None），防「修好
    remotion 字幕、坏掉老管线字幕」。
    """

    def test_per_character_cues_normalize(self):
        """形态①：豆包中文按字/二字词切分（语言无关设计的核心兼容面）。"""
        ref = "信任是团队协作的基石"
        cues = [("信任", 0.0, 0.4), ("是", 0.4, 0.6), ("团队", 0.6, 1.0), ("协作", 1.0, 1.4), ("的", 1.4, 1.6), ("基石", 1.6, 2.0)]
        result = normalize_word_cues(cues, ref)
        assert result is not None
        assert "".join(t for t, _, _ in result) == ref

    def test_digit_percent_tokens_normalize(self):
        """形态②：含数字"37%"（数字与百分号常被切成独立 token）。"""
        ref = "转化率提升37%。"
        cues = [("转化率", 0.0, 0.6), ("提升", 0.6, 1.0), ("37", 1.0, 1.3), ("%", 1.3, 1.45), ("。", 1.45, 1.5)]
        result = normalize_word_cues(cues, ref)
        assert result is not None
        joined = "".join(t for t, _, _ in result)
        assert "37%" in joined

    def test_inter_punctuated_cues_normalize(self):
        """形态③：旁白含标点（逗号/顿号随词或独立 cue 均可归一）。"""
        ref = "第一，准备素材；第二，开始拍摄。"
        cues = [("第一", 0.0, 0.4), ("，", 0.4, 0.45), ("准备", 0.45, 0.8), ("素材", 0.8, 1.2), ("；", 1.2, 1.25), ("第二", 1.25, 1.6), ("，", 1.6, 1.65), ("开始", 1.65, 2.0), ("拍摄", 2.0, 2.4), ("。", 2.4, 2.45)]
        result = normalize_word_cues(cues, ref)
        assert result is not None

    def test_narration_mismatch_returns_none_fallback(self):
        """形态④：cue 与旁白失配（LLM 改写后旧 srt、跨帧串扰等）→ None 回退
        width-ratio 估算路径（最差回到改前行为，不炸、不错位）。"""
        ref = "本集讲解质量护栏的设计思路"
        cues = [("完全", 0.0, 0.5), ("无关", 0.5, 1.0), ("的旧", 1.0, 1.5), ("字幕", 1.5, 2.0)]
        assert normalize_word_cues(cues, ref) is None


# ═══════════════════════════════════════════════════════════════════════════
# T8: LLM 语义重切层 (D3)
# ═══════════════════════════════════════════════════════════════════════════


class _MockLLMResponse:
    """模拟 LLM 响应对象。"""

    def __init__(self, content: str):
        self.content = content


class _MockLLM:
    """模拟 LLM（支持 ainvoke）。"""

    def __init__(self, responses: list[str]):
        self._responses = responses
        self._call_count = 0

    async def ainvoke(self, messages):
        if self._call_count >= len(self._responses):
            raise RuntimeError("No more mock responses")
        resp = self._responses[self._call_count]
        self._call_count += 1
        if resp.startswith("RAISE:"):
            raise RuntimeError(resp[6:])
        return _MockLLMResponse(resp)


class TestLLMResegment:
    @pytest.mark.asyncio
    async def test_normal_resegment(self):
        narration = "人工智能正在深刻改变我们的生活方式和工作方式并且持续演进"
        llm = _MockLLM(['["人工智能正在深刻改变", "我们的生活方式和工作方式", "并且持续演进"]'])
        chunks = await llm_resegment(narration, max_width=16.0, llm=llm)
        assert chunks is not None
        assert len(chunks) == 3
        # 不变量校验：拼接去空白 == 原文
        assert _norm("".join(chunks)) == _norm(narration)

    @pytest.mark.asyncio
    async def test_bad_json_returns_none(self):
        narration = "测试文本"
        llm = _MockLLM(["这不是JSON"])
        chunks = await llm_resegment(narration, max_width=16.0, llm=llm)
        assert chunks is None

    @pytest.mark.asyncio
    async def test_content_mismatch_returns_none(self):
        narration = "原文内容ABC"
        # LLM 返回了与原文不匹配的切分
        llm = _MockLLM(['["完全不同的内容XYZ"]'])
        chunks = await llm_resegment(narration, max_width=16.0, llm=llm)
        assert chunks is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        narration = "测试文本"
        llm = _MockLLM(["RAISE:timeout"])
        chunks = await llm_resegment(narration, max_width=16.0, llm=llm)
        assert chunks is None

    @pytest.mark.asyncio
    async def test_cache_hit(self, tmp_path):
        narration = "缓存测试文本"
        cache_path = str(tmp_path / "subtitle_llm_cache.json")
        # 第一次调用
        llm1 = _MockLLM(['["缓存测试", "文本"]'])
        chunks1 = await llm_resegment(narration, max_width=16.0, llm=llm1, cache_path=cache_path)
        assert chunks1 is not None
        # 第二次应命中缓存（不调用 LLM）
        llm2 = _MockLLM([])  # 没有更多响应 → 若调用会抛
        chunks2 = await llm_resegment(narration, max_width=16.0, llm=llm2, cache_path=cache_path)
        assert chunks2 == chunks1

    @pytest.mark.asyncio
    async def test_cache_multi_frame_no_overwrite(self, tmp_path):
        """N2 回归：多帧共享 cache 文件——帧 2 不得覆盖帧 1 的缓存。"""
        cache_path = str(tmp_path / "subtitle_llm_cache.json")
        narration1 = "第一帧旁白"
        narration2 = "第二帧不同的旁白"
        # 帧 1
        llm1 = _MockLLM(['["第一帧", "旁白"]'])
        c1 = await llm_resegment(narration1, max_width=16.0, llm=llm1, cache_path=cache_path)
        assert c1 is not None
        # 帧 2（不同 narration → 不同 cache_key）
        llm2 = _MockLLM(['["第二帧", "不同的旁白"]'])
        c2 = await llm_resegment(narration2, max_width=16.0, llm=llm2, cache_path=cache_path)
        assert c2 is not None
        # re-render 帧 1：应命中缓存（不调 LLM）
        llm3 = _MockLLM([])  # 无响应 → 调 LLM 会抛
        c1_rerender = await llm_resegment(narration1, max_width=16.0, llm=llm3, cache_path=cache_path)
        assert c1_rerender == c1, "帧 1 缓存被帧 2 覆盖（N2 回归）"

    @pytest.mark.asyncio
    async def test_empty_narration_returns_none(self):
        chunks = await llm_resegment("", max_width=16.0, llm=_MockLLM([]))
        assert chunks is None


class TestAlignWordCuesFallback:
    """T6（P1-1）：词耗尽回退分支防重叠。"""

    def test_word_exhaustion_fallback_no_overlap(self):
        """word_cues 数 < chunk 数 → 回退 start 不得早于前条 end。"""
        from app.gateway.video_gen.subtitle_engine import _align_word_cues

        chunks = ["一二三四", "五六七八", "九十一二", "十三十四"]
        word_cues = [("一二三四", 0.0, 2.0), ("五六七八", 2.0, 4.0), ("九十一二", 4.0, 6.0)]
        cues = _align_word_cues(chunks, word_cues, max_width=16.0)
        for i in range(1, len(cues)):
            assert cues[i].start >= cues[i - 1].end - 1e-3, f"词耗尽回退重叠：{cues[i - 1]} → {cues[i]}"
        assert cues[-1].end >= cues[-1].start - 1e-3, "末条时间倒挂"

    def test_word_exhaustion_segment_level_monotonic(self):
        """segment 级验收：word_cues 数 < chunk 数 → 最终列表严格单调、零丢字。"""
        narration = "一二三四。五六七八。九十一二。十三十四。"
        word_cues = [("一二三四", 0.0, 2.0), ("五六七八", 2.0, 4.0), ("九十一二", 4.0, 6.0)]
        cues = segment(narration, word_cues=word_cues, orientation="landscape", max_cps=100.0)
        for i in range(1, len(cues)):
            assert cues[i].start >= cues[i - 1].end - 1e-3, f"重叠：{cues[i - 1]} → {cues[i]}"
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert _norm(joined) == _norm(narration), f"丢字：{cues!r}"

    def test_gap_fallback_pushes_next_start(self):
        """超密 cue：前条无法让步（<0.1s）时推迟后条 start（保单调优先于保 gap）。"""
        from app.gateway.video_gen.subtitle_engine import _apply_gap

        cues = [Cue(text="甲", start=0.0, end=0.05), Cue(text="乙", start=0.02, end=5.0)]
        out = _apply_gap(cues)
        assert out[0].end <= out[1].start + 1e-3, f"重叠保留：{out!r}"
        assert out[1].start >= out[0].end + 0.08 - 1e-3, f"间隙兜底未生效：{out!r}"


class TestMergeMaxDurRecheck:
    """T7（P1-2）：合并后复查 max_dur（并入收尾循环）。"""

    def test_merge_into_overlong_cue_is_resplit(self):
        """6.6s cue 并入 0.7s 尾段 → 合并后 7.4s > max_dur → 收尾复查再拆。"""
        narration = "这是第一段旁白内容。好"
        cues = segment(narration, audio_duration=7.4, orientation="landscape")
        for c in cues:
            assert c.end - c.start <= 7.0 + 0.1, f"合并后超 max_dur 未复查：{c.text!r} dur={c.end - c.start:.2f}"


class TestAssBracesEscape:
    """T8（P1-3）：ASS 花括号转义（全角），SRT 原文不动。"""

    def test_ass_escapes_braces(self, tmp_path):
        cues = [Cue(text="使用{test}语法", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "｛test｝" in content, "ASS 未转义花括号"
        assert "使用{test}语法" not in content, "ASS 出现裸花括号"

    def test_srt_keeps_original_braces(self, tmp_path):
        """SRT 是 canonical——花括号原文保留（subtitle_locked/PUT 语义）。"""
        cues = [Cue(text="使用{test}语法", start=0.0, end=2.0)]
        out = tmp_path / "test.srt"
        write_srt(cues, out_path=str(out))
        content = out.read_text(encoding="utf-8")
        assert "使用{test}语法" in content

    def test_n_break_not_escaped(self, tmp_path):
        """只动 cue 正文，不动 \\N 折行标记。"""
        cues = [Cue(text="第一行\\N第二{行}", start=0.0, end=2.0)]
        out = tmp_path / "test.ass"
        write_ass(cues, out_path=str(out), video_w=1920, video_h=1080)
        content = out.read_text(encoding="utf-8")
        assert "\\N" in content
        assert "第二｛行｝" in content


class TestAsciiWordBoundarySpace:
    """T9（P1-4）：ASCII↔ASCII 词边界补空格。"""

    def test_join_tokens_ascii_boundary_space(self):
        result = join_tokens(tokenize("abc") + tokenize("def"))
        assert " " in result, f"ASCII 词边界未插空格：{result!r}"
        assert result == "abc def"

    def test_full_chain_preserves_english_spacing(self):
        """全链路 segment 后拼接（去换行）仍含 "development mode" 原空格。"""
        narration = "这是一个测试，use development mode 可以加速"
        cues = segment(narration, audio_duration=8.0, orientation="landscape")
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert "development mode" in joined.replace("\\N", " "), f"英文词间空格丢失：{joined!r}"

    def test_cjk_cjk_no_space_regression(self):
        assert " " not in join_tokens(tokenize("人工智能"))

    def test_no_double_space(self):
        """prev 已以空格结尾 → 不重复插空格。"""
        result = join_tokens(tokenize("abc") + [Token(" ", kind="space")] + tokenize("def"))
        assert "  " not in result, f"双空格：{result!r}"


class TestDashNoLineStart:
    """T10（P1-5）："——"/"--" 避头 flag。"""

    @pytest.mark.parametrize("dash", ["——", "--"])
    def test_no_chunk_starts_with_dash(self, dash):
        narration = "一二三四五六七八九十一二三四五六" + dash + "这是后面的内容"
        cues = segment(narration, audio_duration=6.0, orientation="landscape")
        for c in cues:
            text = c.text.replace("\\N", "")
            assert not text.startswith(dash), f"cue 以 {dash!r} 开头：{text!r}"
        joined = "".join(c.text.replace("\\N", "") for c in cues)
        assert dash in joined, f"丢字：{joined!r}"


class TestPyramidThreshold:
    """T11（P1-7）：上行 ≤2 字惩罚。"""

    def test_no_two_char_top_when_alternative_exists(self):
        """2 字上行候选与更优候选并存 → 不选 2 字上行。

        候选：bp=2（上行 2 宽，方差 625）；bp=27（上行 15 宽 vs 下行 14 宽，
        金字塔罚 1000 + 方差 1）。"，" 作锚点杀 bp=26（避头）。旧评分选 bp=2；
        +500 惩罚后选 bp=27。
        """
        text = "一二" + "A" * 24 + "，" + "B" * 28
        result = layout_two_lines(text, max_width=16)
        assert "\\N" in result, "应折行"
        top = result.split("\\N")[0]
        assert top != "一二", f"仍选 2 字上行：{result!r}"

    def test_fallback_when_only_two_char_breakpoint(self):
        """确实无解（唯一断点=2 字上行）时仍能折行。"""
        text = "一二" + "A" * 30
        result = layout_two_lines(text, max_width=16)
        assert "\\N" in result, "无解时应仍折行"
        assert result.split("\\N")[0] == "一二"


class TestLine1TrailingPunctStrip:
    """T12（P1-8）：行 1 尾标点剥离。"""

    def test_line1_no_trailing_clause_punct(self):
        """在 "，" 后折行 → 行 1 不以 "，" 结尾。"""
        text = "一二三四五六七八，一二三四五六七八九"
        result = layout_two_lines(text, max_width=16)
        lines = result.split("\\N")
        assert len(lines) == 2, f"未折行：{result!r}"
        assert not lines[0].endswith("，"), f"行 1 尾标点保留：{result!r}"

    def test_line1_no_trailing_sentence_punct(self):
        """在 "。" 后折行 → 行 1 不以 "。" 结尾。"""
        text = "一二三四五六七八。一二三四五六七八九"
        result = layout_two_lines(text, max_width=16)
        lines = result.split("\\N")
        assert len(lines) == 2, f"未折行：{result!r}"
        assert not lines[0].endswith("。"), f"行 1 尾标点保留：{result!r}"

    def test_zero_loss_compat(self):
        """剥标点属 normalized 豁免集——零丢字回归仍过。"""
        text = "一二三四五六七八，一二三四五六七八九"
        result = layout_two_lines(text, max_width=16)
        assert _norm(result.replace("\\N", "")) == _norm(text)


class TestSegmentWithLLMChunks:
    def test_llm_chunks_skip_rule_segmentation(self):
        """llm_chunks 提供时跳过规则切分，直接进入 align+post。"""
        narration = "人工智能正在深刻改变我们的生活"
        llm_chunks = ["人工智能正在深刻", "改变我们的生活"]
        cues = segment(narration, audio_duration=4.0, orientation="landscape", llm_chunks=llm_chunks)
        assert len(cues) == 2
        assert _norm("".join(c.text.replace("\\N", "") for c in cues)) == _norm(narration)

    def test_no_llm_chunks_falls_back_to_rules(self):
        """未提供 llm_chunks → 规则链切分。"""
        narration = "人工智能正在深刻改变我们的生活"
        cues = segment(narration, audio_duration=4.0, orientation="landscape")
        assert len(cues) >= 1


# ── P2-A（2026-08-18）：无标点长句连词/语气词断点层 ──────────────────────────


def test_conjunction_break_no_punct_long_sentence():
    """P2-A: 无标点长句断点不落在连词内部，连词归属后句。"""
    from app.gateway.video_gen.subtitle_engine import cut_chunks, tokenize

    text = "人工智能正在深刻改变我们的生活和工作方式并且持续演进催生全新的产业形态"
    chunks = cut_chunks(tokenize(text), max_width=10.0)
    assert len(chunks) >= 3
    assert "".join(chunks).replace(" ", "") == text, "零丢字不变量"
    # 断点不得落在「并且」内部
    for i in range(len(chunks) - 1):
        assert not (chunks[i].endswith("并") and chunks[i + 1].startswith("且")), "断点落在连词「并且」内部"
    # 连词归属后句：某 chunk 以「并且」开头
    assert any(c.startswith("并且") for c in chunks), f"连词应归属后句：{chunks}"


def test_conjunction_mid_buffer_attaches_to_following_clause():
    """P2-A 判别用例：连词对处于 buf 中段（j≥1）时断在连词**前**。

    计划原样本（上例）里「并且」恰好落在 buf[0:2]（j=0 被扫描范围排除），
    修前修后切分相同——本用例把「然后」放到 buf 末两位，修前硬切把
    「然后」整体留在上段尾，修后断在连词前、连词领起后句。
    """
    from app.gateway.video_gen.subtitle_engine import cut_chunks, tokenize

    text = "甲乙丙丁戊己庚辛然后继续演进创造全新格局"
    chunks = cut_chunks(tokenize(text), max_width=10.0)
    assert "".join(chunks).replace(" ", "") == text, "零丢字不变量"
    assert any(c.startswith("然后") for c in chunks), f"连词应归属后句：{chunks}"
    for i in range(len(chunks) - 1):
        assert not (chunks[i].endswith("然") and chunks[i + 1].startswith("后")), "断点落在连词「然后」内部"


def test_conjunction_layer_below_punct_priority():
    """P2-A: 有标点时标点断点仍优先（连词层只兜底无标点场景）。"""
    from app.gateway.video_gen.subtitle_engine import cut_chunks, tokenize

    text = "先做这部分，然后我们再继续扩展新的功能模块并且保持兼容"
    chunks = cut_chunks(tokenize(text), max_width=8.0)
    assert chunks[0] == "先做这部分，", f"「，」子句断点必须优先于连词层：{chunks}"
    assert "".join(chunks).replace(" ", "") == text, "零丢字不变量"


# ── P2-B（2026-08-18，二审分流版）：显示宽度 EAW 化 ─────────────────────────


def test_display_width_mixed_script():
    """P2-B: é/西里尔按半角、emoji 按全角、组合记号 0；CJK/ASCII/全角标点/破折号
    省略号既有数值不回归。"""
    from app.gateway.video_gen.subtitle_engine import _display_width, _text_display_width

    assert _display_width("中") == 1.0
    assert _display_width("a") == 0.5
    assert _display_width("，") == 1.0  # EAW=F 全角
    assert _display_width("—") == 1.0  # EAW=A，CJK 语境按宽（——组成字）
    assert _display_width("…") == 1.0  # 同上（……组成字）
    assert _display_width("×") == 1.0  # EAW=A + Sm → 两可符号按宽（CJK 语境）
    assert _display_width("é") == 0.5  # 修前 1.0
    assert _display_width("т") == 0.5  # 西里尔，修前 1.0
    assert _display_width("α") == 0.5  # 希腊字母，EAW=A + L* → 按窄（与 é/т 同类）
    assert _display_width("🎉") == 1.0  # EAW=W
    assert _display_width("́") == 0.0  # 组合尖音符（U+0301）
    assert _text_display_width("café 🎉 тест") == 6.0  # 2.0+0.5+1.0+0.5+2.0


# ── P2-D（2026-08-18）：零时长 cue 出口过滤 ─────────────────────────────────


def test_segment_zero_audio_duration_yields_no_zero_length_cues():
    """P2-D: 无有效时长 → 不产出 00:00:00,000 --> 00:00:00,000 的零长 cue
    （libass 静默丢弃，字幕不可见且难排查）。全部零长 → 空列表。"""
    from app.gateway.video_gen.subtitle_engine import segment

    cues = segment("这是一段旁白。第二段旁白。", audio_duration=0.0)
    assert all(c.end - c.start >= 0.01 for c in cues)
    assert cues == [], "全零长过滤后为空（调用方已有空列表处理）"


def test_segment_normal_duration_unaffected():
    """P2-D: 正常用例不受出口过滤影响（阈值 0.01s << min_dur=1.0）。"""
    from app.gateway.video_gen.subtitle_engine import segment

    cues = segment("这是一段旁白。第二段旁白。", audio_duration=8.0)
    assert cues and all(c.end - c.start >= 1.0 for c in cues)

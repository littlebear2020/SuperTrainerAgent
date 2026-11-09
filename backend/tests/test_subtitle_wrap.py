"""管线级回归（2026-08-14 B1 清理后）：_write_storyboard_srt / build_worker_payload 经新字幕引擎的行为守护。

旧函数（_wrap_subtitle / _adjust_cut_for_word_boundary / _merge_edge_word_cues /
_clean_subtitle_text / _split_narration / _distribute_duration）已随 B1 删除；
引擎级契约见 test_subtitle_engine.py（含迁移过去的西文词不腰斩用例）。
"""

import re
from pathlib import Path

import pytest

from app.gateway.video_gen.service import _write_storyboard_srt, build_worker_payload
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

_WS = re.compile(r"\s")
_PUNCT = "。！？!?；;…、，,：:"


def _strip_ws_punct(s: str) -> str:
    """D2 口径：正文零丢失比对时忽略空白与标点（行尾标点由引擎 clean 去除）。"""
    return _WS.sub("", s).translate(str.maketrans("", "", _PUNCT))


# ---------------------------------------------------------------------------
# _write_storyboard_srt：多条字幕 + speech_duration 基准（新引擎路径）
# ---------------------------------------------------------------------------


def _to_sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_entries(content: str) -> list[tuple[int, float, float, str]]:
    entries = []
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue  # 空 SRT（全部 cue 被清洗守卫跳过）→ 无条目
        start_s, end_s = lines[1].split(" --> ")
        entries.append((int(lines[0]), _to_sec(start_s), _to_sec(end_s), "\n".join(lines[2:])))
    return entries


def _make_sb(frames: list[StoryboardFrame]) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=frames,
    )


def test_fallback_splits_long_narration_into_multiple_entries(tmp_path):
    narration = "首先介绍视频生成的整体架构和核心流程。然后演示如何创建第一个任务并等待草稿完成。最后讲解分镜编辑技巧。"
    sb = _make_sb([StoryboardFrame(index=0, narration=narration, duration=8.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert len(entries) > 1, "长旁白应切成多条字幕（修前整帧一条）"
    joined = "".join(_strip_ws_punct(text) for _, _, _, text in entries)
    assert joined == _strip_ws_punct(narration), "所有字幕拼接须 == 原文（不丢字；行尾标点已按 D1 去除）"


def test_fallback_uses_speech_duration_as_basis(tmp_path):
    """spf 场景：duration=8（含 5s 静音 padding）、speech_duration=3 → 字幕排在 [0,3]。"""
    sb = _make_sb(
        [
            StoryboardFrame(index=0, narration="第一帧的旁白内容比较长需要被切分。", duration=8.0, speech_duration=3.0),
            StoryboardFrame(index=1, narration="第二帧旁白。", duration=2.0, speech_duration=2.0),
        ]
    )
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    frame1_entries = [e for e in entries if "第二帧" in e[3]]
    frame0_entries = [e for e in entries if "第二帧" not in e[3]]
    assert frame0_entries, "第一帧应有字幕"
    assert frame0_entries[-1][2] == pytest.approx(3.0, abs=0.002), "末条字幕须结束于 speech_duration，不摊到静音段"
    assert frame1_entries and frame1_entries[0][1] == pytest.approx(8.0, abs=0.002), "帧偏移仍按 duration 推进"


def test_fallback_without_speech_duration_uses_full_duration(tmp_path):
    """旧草稿无 speech_duration（None）→ 退回 frame.duration 配比（旧行为，无回归）。"""
    sb = _make_sb([StoryboardFrame(index=0, narration="这一段旁白内容需要被拆分成多条字幕来显示，否则观众会看不完整的信息。", duration=6.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert len(entries) > 1
    assert entries[-1][2] == pytest.approx(6.0, abs=0.002)


def test_fallback_empty_narration_writes_no_entry(tmp_path):
    """空旁白（静音帧）→ 不产出字幕条目，后续帧偏移不受影响。"""
    sb = _make_sb(
        [
            StoryboardFrame(index=0, narration="", duration=1.0, speech_duration=0.0),
            StoryboardFrame(index=1, narration="有内容的旁白。", duration=2.0, speech_duration=2.0),
        ]
    )
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries, "有旁白的帧仍须有字幕"
    assert all("有内容" in e[3] for e in entries), "空旁白帧不得产出字幕条目"
    assert entries[0][0] == 1, "条目序号须从 1 连续"
    assert entries[0][1] == pytest.approx(1.0, abs=0.002), "空帧占位时长仍须推进偏移"


def test_fallback_entries_within_line_limits(tmp_path):
    narration = "首先介绍视频生成的整体架构和核心流程以及各个模块的职责划分。然后演示如何创建第一个任务并等待草稿完成。最后讲解分镜编辑技巧。"
    sb = _make_sb([StoryboardFrame(index=0, narration=narration, duration=12.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    for _, _, _, text in entries:
        # D1（2026-08-13）：允许两行，每行 ≤16 全角当量
        lines = text.split("\n")
        assert len(lines) <= 2, f"最多两行: {text!r}"
        for line in lines:
            assert len(line) <= 20, f"单行超宽: {line!r} ({len(line)}字)"


def test_fallback_entries_do_not_overflow_frame_bounds(tmp_path):
    narration = "这一帧的旁白内容特别长，需要被拆分成很多条短字幕才能完整显示出来而不丢字。"
    sb = _make_sb(
        [
            StoryboardFrame(index=0, narration=narration, duration=5.0, speech_duration=5.0),
            StoryboardFrame(index=1, narration="下一帧。", duration=2.0, speech_duration=2.0),
        ]
    )
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    frame0_end = max(e[2] for e in entries if "下一帧" not in e[3])
    assert frame0_end <= 5.0 + 0.002, "字幕不得溢出到下一帧"


def test_fallback_no_text_loss_after_wrap_for_pathological_merge(tmp_path):
    """全程断言：病态合并场景下字幕拼接 == 原文（修前丢"最后收尾。"5 字）。"""
    narration = "嗯。好。这是一个很长的句子，然后这是更多内容啊，最后收尾。"
    sb = _make_sb([StoryboardFrame(index=0, narration=narration, duration=1.0, speech_duration=1.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries
    joined = "".join(_strip_ws_punct(text) for _, _, _, text in entries)
    assert joined == _strip_ws_punct(narration), f"字幕丢字: 缺 {_strip_ws_punct(narration)[len(joined) :]!r}"


def test_fallback_srt_entries_are_two_lines_max(tmp_path):
    """端到端：fallback SRT 每条 cue 最多两行（D1 放宽 2026-08-13）。"""
    narration = "嗯。好。首先介绍视频生成的整体架构和核心流程以及各个模块的职责划分。然后演示如何创建第一个任务并等待草稿完成。最后收尾。"
    sb = _make_sb([StoryboardFrame(index=0, narration=narration, duration=12.0, speech_duration=12.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert len(entries) > 1
    for _, _, _, text in entries:
        lines = text.split("\n")
        assert len(lines) <= 2, f"最多两行: {text!r}"
        for line in lines:
            assert len(line) <= 20, f"单行超宽: {line!r}"


# ---------------------------------------------------------------------------
# 纯标点防护（libass 空 cue 崩溃回归，2026-07-21）
# ---------------------------------------------------------------------------


def test_fallback_pure_punctuation_narration_writes_no_cue(tmp_path):
    """纯标点旁白：清洗后全空 → 不得写出空文本 cue（libass 崩溃回归）。"""
    sb = _make_sb([StoryboardFrame(index=0, narration="。。。", duration=3.0, speech_duration=3.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries == [], f"纯标点旁白不应产出 cue，实得 {entries!r}"


def test_fallback_pure_punctuation_comma_variant_writes_no_cue(tmp_path):
    """「，、；」实证会产出 1 个空 cue —— 守卫必须拦住。"""
    sb = _make_sb([StoryboardFrame(index=0, narration="，、；", duration=3.0, speech_duration=3.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries == [], f"纯标点旁白不应产出 cue，实得 {entries!r}"


def test_fallback_cues_have_no_trailing_punctuation(tmp_path):
    """生成侧 cue 行尾不得留标点（D1）。"""
    narration = "首先介绍整体架构。然后演示如何创建任务。最后讲解编辑技巧。"
    sb = _make_sb([StoryboardFrame(index=0, narration=narration, duration=9.0, speech_duration=9.0)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries
    for _, _, _, text in entries:
        assert text, "不得有空文本 cue"
        assert text[-1] not in "。！？!?；;…、，,：:.", f"cue 行尾残留标点：{text!r}"


def test_edge_path_pure_punctuation_cue_skipped(tmp_path):
    """纯标点词级 cue 清洗后为空 → 跳过，不写空文本 cue。"""
    word_srt = "1\n00:00:00,000 --> 00:00:01,000\n。\n\n2\n00:00:01,000 --> 00:00:02,000\n。"
    sb = _make_sb([StoryboardFrame(index=0, narration="。。", duration=2.0, speech_duration=2.0, srt=word_srt)])
    out = tmp_path / "t.srt"
    _write_storyboard_srt(sb, str(out))
    entries = _parse_entries(Path(out).read_text(encoding="utf-8"))
    assert entries == [], f"纯标点词级 cue 不应产出条目，实得 {entries!r}"


# ---------------------------------------------------------------------------
# P0 回归（2026-08-13 代码核查）：「无字幕」必须同时置 None subtitle_path 和 subtitle_ass
# ---------------------------------------------------------------------------


def test_build_worker_payload_none_style_zeros_both_subtitle_fields():
    """P0: subtitle_style="none" 时 payload 不得带 subtitle_path 也不得带 subtitle_ass。

    修前只置 subtitle_path=None，subtitle_ass 仍带路径 → worker 优先看 ASS → 照烧字幕。
    """
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, subtitle_style="none"),
        frames=[StoryboardFrame(index=0, narration="测试", duration=2.0)],
    )
    payload = build_worker_payload(
        sb,
        subtitle_path="/tmp/subtitle.srt",
        subtitle_ass="/tmp/subtitle.ass",
        audio_concat_path="/tmp/audio.mp3",
    )
    assert payload["subtitle_path"] is None, "none 闸必须置 subtitle_path=None"
    assert payload["subtitle_ass"] is None, "P0 回归：none 闸必须同时置 subtitle_ass=None"


def test_build_worker_payload_normal_style_keeps_both():
    """正常样式时 subtitle_path 和 subtitle_ass 都保留。"""
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, subtitle_style="default"),
        frames=[StoryboardFrame(index=0, narration="测试", duration=2.0)],
    )
    payload = build_worker_payload(
        sb,
        subtitle_path="/tmp/subtitle.srt",
        subtitle_ass="/tmp/subtitle.ass",
        audio_concat_path="/tmp/audio.mp3",
    )
    assert payload["subtitle_path"] == "/tmp/subtitle.srt"
    assert payload["subtitle_ass"] == "/tmp/subtitle.ass"

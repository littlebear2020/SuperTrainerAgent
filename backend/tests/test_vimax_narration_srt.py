"""Tests for ViMax narration SRT generation (Task 8 — no more missing subtitles)."""

from pathlib import Path

from app.gateway.video_gen.engines.vimax.narration_generator import (
    _format_srt_time,
    _write_srt,
)


def test_format_srt_time():
    assert _format_srt_time(0.0) == "00:00:00,000"
    assert _format_srt_time(5.2) == "00:00:05,200"
    assert _format_srt_time(65.5) == "00:01:05,500"
    assert _format_srt_time(3661.123) == "01:01:01,123"


def test_write_srt_formats_valid_cues(tmp_path: Path):
    """_write_srt writes valid SRT: index, HH:MM:SS,mmm --> HH:MM:SS,mmm, text, blank line."""
    entries = [("第一句旁白", 0.0, 5.2), ("第二句旁白", 5.2, 10.4)]
    srt_path = tmp_path / "subtitle.srt"
    _write_srt(entries, srt_path)
    content = srt_path.read_text(encoding="utf-8")
    assert "1\n00:00:00,000 --> 00:00:05,200\n第一句旁白\n" in content
    assert "2\n00:00:05,200 --> 00:00:10,400\n第二句旁白\n" in content
    # cues separated by a blank line
    assert content.strip().endswith("第二句旁白")


def test_write_srt_empty_entries_skips_file(tmp_path: Path, caplog):
    """srt_entries 为空 → 不写 0 字节 SRT（下游 libass 'Unable to open' 会崩整任务）+ WARNING 可观测。"""
    import logging

    srt_path = tmp_path / "subtitle.srt"
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.engines.vimax.narration_generator"):
        _write_srt([], srt_path)
    assert not srt_path.exists()
    assert any("cue" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# 2026-07-31 T5：长台词切成 ≤16 字单行 cue（C1——整句台词一条 cue 压满画面）
# ---------------------------------------------------------------------------


def _parse_cues(content: str) -> list[tuple[int, str, str, str]]:
    out = []
    for block in content.strip().split("\n\n"):
        lines = [ln for ln in block.strip().split("\n") if ln]
        if len(lines) < 3:
            continue
        start, end = lines[1].split(" --> ")
        out.append((int(lines[0]), start, end, "\n".join(lines[2:])))
    return out


def _to_sec(ts: str) -> float:
    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def test_write_srt_splits_long_dialogue_into_multiple_cues(tmp_path: Path):
    """长台词 shot → 多条 ≤16 字单行 cue，且不越出本 shot 区间。"""
    long_line = "这是一段特别长的台词内容需要被切分成多条字幕才能在竖屏上单行显示清楚"
    entries = [(long_line, 0.0, 12.0)]
    srt_path = tmp_path / "subtitle.srt"
    _write_srt(entries, srt_path)
    cues = _parse_cues(srt_path.read_text(encoding="utf-8"))
    assert len(cues) > 1, f"长台词应切成多条，实得 {len(cues)} 条"
    for _, start, end, text in cues:
        assert "\n" not in text, f"cue 不得多行：{text!r}"
        assert len(text) <= 16, f"cue 超 16 字：{text!r}（{len(text)} 字）"
        assert 0.0 <= _to_sec(start) <= 12.0
        assert 0.0 <= _to_sec(end) <= 12.0
    assert [c[0] for c in cues] == list(range(1, len(cues) + 1)), "序号须从 1 连续"


def test_write_srt_cues_do_not_overlap_across_shots(tmp_path: Path):
    """相邻 shot 的 cue 不重叠：前一 shot 末条 end ≤ 后一 shot 首条 start。"""
    entries = [
        ("这是第一个镜头的台词内容比较长需要切分成好几条来显示", 0.0, 10.0),
        ("这是第二个镜头的台词内容同样也比较长需要切分显示", 10.0, 20.0),
    ]
    srt_path = tmp_path / "subtitle.srt"
    _write_srt(entries, srt_path)
    cues = _parse_cues(srt_path.read_text(encoding="utf-8"))
    shot1 = [c for c in cues if _to_sec(c[1]) < 10.0]
    shot2 = [c for c in cues if _to_sec(c[1]) >= 10.0]
    assert shot1 and shot2, "两个 shot 都应有 cue"
    assert _to_sec(shot1[-1][2]) <= _to_sec(shot2[0][1]) + 0.002, "相邻 shot cue 重叠"
    for _, start, end, _text in cues:
        assert _to_sec(start) < _to_sec(end) or _to_sec(end) - _to_sec(start) >= 0, "时间轴须递增"


def test_write_srt_zero_duration_shot_produces_no_cue(tmp_path: Path):
    """零时长 shot（end == start）→ 不产出 cue（_distribute_duration 会退化成 0.0-0.0 脏 cue）。"""
    entries = [("零时长镜头的台词", 5.0, 5.0), ("正常镜头的台词", 5.0, 10.0)]
    srt_path = tmp_path / "subtitle.srt"
    _write_srt(entries, srt_path)
    cues = _parse_cues(srt_path.read_text(encoding="utf-8"))
    assert cues, "正常 shot 仍须有 cue"
    assert all("零时长" not in c[3] for c in cues), f"零时长 shot 不应产出 cue：{cues!r}"
    for _, start, end, _text in cues:
        assert _to_sec(end) > _to_sec(start), "不得有 start == end 的退化 cue"


def test_write_srt_negative_duration_shot_produces_no_cue(tmp_path: Path):
    entries = [("负时长镜头", 8.0, 3.0), ("正常镜头的台词", 8.0, 12.0)]
    srt_path = tmp_path / "subtitle.srt"
    _write_srt(entries, srt_path)
    cues = _parse_cues(srt_path.read_text(encoding="utf-8"))
    assert all("负时长" not in c[3] for c in cues)


def test_write_srt_all_punctuation_dialogue_writes_no_file(tmp_path: Path, caplog):
    """台词全是标点 → 清洗后全空 → 不得产出 0 字节 SRT（libass 崩溃回归）。"""
    import logging

    srt_path = tmp_path / "subtitle.srt"
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.engines.vimax.narration_generator"):
        _write_srt([("。。。", 0.0, 5.0), ("，、；", 5.0, 10.0)], srt_path)
    assert not srt_path.exists(), "全标点台词不得写出 SRT 文件"
    assert any("cue" in r.message.lower() for r in caplog.records)


def test_write_srt_strips_trailing_punctuation(tmp_path: Path):
    """D1：生成侧 cue 行尾标点去除。"""
    srt_path = tmp_path / "subtitle.srt"
    _write_srt([("你好世界。", 0.0, 5.0)], srt_path)
    cues = _parse_cues(srt_path.read_text(encoding="utf-8"))
    assert cues
    assert cues[0][3] == "你好世界", f"行尾标点未去除：{cues[0][3]!r}"


def test_write_srt_output_passes_srt_has_cues(tmp_path: Path):
    """产出必须能被 audio_overlay.srt_has_cues 认作有效（避免 burn_subtitles 崩）。"""
    from app.gateway.video_gen.engines.vimax.audio_overlay import srt_has_cues

    srt_path = tmp_path / "subtitle.srt"
    _write_srt([("这是一段需要切分的较长台词内容用于验证有效性", 0.0, 8.0)], srt_path)
    assert srt_has_cues(srt_path) is True

"""Tests for PPT-to-video subtitle fix.

Covers:
  1. Subtitle text matches TTS narration text (text source consistency)
  2. _split_sentences() sentence splitting logic
  3. _generate_srt_for_slide() SRT generation (timing, format, long-sentence splitting)
"""

import json
import sys
from pathlib import Path

import pytest

# Add docker/production-worker to path for pipeline module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docker" / "production-worker"))

from pipeline import _generate_srt_for_slide, _split_sentences, _srt_time

# ---------------------------------------------------------------------------
# 1. Subtitle text consistency with TTS narration
# ---------------------------------------------------------------------------


class TestSubtitleTextConsistency:
    """Verify subtitles.json text matches TTS narration for all slide combinations.

    Drives the REAL production consumption point — ProductionService._run_ppt_to_video
    (voice_twin/production_service.py:719-725 的兜底链 notes→body→title→"(无内容)"，
    TTS 与字幕共用同一份 slide_narrations) — with real SlideData; only the
    extraction/TTS boundary (ppt_extract functions + _do_tts_synthesis) is mocked.
    Asserting both the TTS input texts and subtitles.json "text" fields pins the
    consistency property: 两者由生产代码里同一个列表喂出。
    """

    def _make_slide(self, slide_number, title, body, notes):
        from app.gateway.voice_twin.ppt_extract import SlideData

        return SlideData(slide_number, title, body, notes)

    async def _run_ppt_pipeline(self, tmp_path, monkeypatch, slides):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from app.gateway.voice_twin import ppt_extract
        from app.gateway.voice_twin.production_service import ProductionService

        monkeypatch.setattr(ppt_extract, "extract_pptx_content", lambda _path: slides)
        monkeypatch.setattr(ppt_extract, "analyze_notes", lambda _slides: SimpleNamespace(is_sufficient=True))
        monkeypatch.setattr(ppt_extract, "analyze_fonts", lambda _path: SimpleNamespace(risky=False))
        monkeypatch.setattr(
            ppt_extract,
            "convert_pptx_to_images_auto",
            lambda _path, _dir, _width: [f"slide-{i + 1}.png" for i in range(len(slides))],
        )

        svc = ProductionService.__new__(ProductionService)
        tts_spy = AsyncMock(return_value=b"ID3\x00fake-mp3")  # 非 RIFF → 按 mp3 直接落盘
        monkeypatch.setattr(svc, "_do_tts_synthesis", tts_spy)

        class _Progress:
            async def __call__(self, **kwargs): ...

            async def set_result(self, **kwargs): ...

        payload = {"voice_id": "preset:146", "pptx_path": "fake.pptx", "subtitle": True, "preset": "none"}
        await svc._run_ppt_to_video("task1", "user1", payload, tmp_path, _Progress())
        return tts_spy

    def _read_subtitles(self, tmp_path):
        return json.loads((tmp_path / "subtitles.json").read_text(encoding="utf-8"))

    @pytest.mark.asyncio
    async def test_narration_fallback_chain_drives_tts_and_subtitles(self, tmp_path, monkeypatch):
        """备注→正文→标题→占位 四分支各一页：TTS 输入与 subtitles.json 的 text
        必须同为该页选中的 narration。"""
        slides = [
            self._make_slide(1, "标题一", "正文一", "这是备注文字用于讲解"),
            self._make_slide(2, "第二章 概述", "这是正文的详细内容，包含了讲解所需的信息。", ""),
            self._make_slide(3, "只有标题", "", ""),
            self._make_slide(4, "", "", ""),
        ]
        tts_spy = await self._run_ppt_pipeline(tmp_path, monkeypatch, slides)

        expected = [
            "这是备注文字用于讲解",  # notes 优先于 body
            "这是正文的详细内容，包含了讲解所需的信息。",  # 无备注 → body（不得用标题）
            "只有标题",  # 备注正文皆空 → title
            "(无内容)",  # 全空 → 占位
        ]
        tts_texts = [call.kwargs["text"] for call in tts_spy.call_args_list]
        assert tts_texts == expected, "TTS 输入未走生产兜底链"

        subtitles = self._read_subtitles(tmp_path)
        assert [item["slide"] for item in subtitles] == [1, 2, 3, 4]
        for item, want in zip(subtitles, expected):
            assert item["text"] == want  # "text" 字段名是 Worker 消费契约


# ---------------------------------------------------------------------------
# 2. _split_sentences() tests
# ---------------------------------------------------------------------------


class TestSplitSentences:
    def test_chinese_punctuation(self):
        text = "这是第一句。这是第二句。这是第三句。"
        result = _split_sentences(text)
        assert len(result) == 3
        assert result[0] == "这是第一句。"
        assert result[1] == "这是第二句。"
        assert result[2] == "这是第三句。"

    def test_mixed_punctuation(self):
        text = "中文句子。English sentence! Mixed？最后。"
        result = _split_sentences(text)
        assert len(result) == 4

    def test_no_punctuation(self):
        text = "这是一段没有标点的长文字"
        result = _split_sentences(text)
        assert len(result) == 1
        assert result[0] == text

    def test_empty_string(self):
        result = _split_sentences("")
        assert result == []

    def test_single_sentence(self):
        text = "只有一句。"
        result = _split_sentences(text)
        assert len(result) == 1
        assert result[0] == "只有一句。"

    def test_semicolon_split(self):
        text = "第一部分；第二部分；第三部分"
        result = _split_sentences(text)
        assert len(result) == 3

    def test_whitespace_only(self):
        result = _split_sentences("   \n\t  ")
        assert result == []

    def test_english_punctuation(self):
        text = "First sentence! Second sentence? Third sentence!"
        result = _split_sentences(text)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# 3. _srt_time() tests
# ---------------------------------------------------------------------------


class TestSrtTime:
    def test_zero(self):
        assert _srt_time(0.0) == "00:00:00,000"

    def test_simple_seconds(self):
        assert _srt_time(5.5) == "00:00:05,500"

    def test_minutes(self):
        assert _srt_time(125.123) == "00:02:05,123"

    def test_hours(self):
        assert _srt_time(3661.0) == "01:01:01,000"


# ---------------------------------------------------------------------------
# 4. _generate_srt_for_slide() tests
# ---------------------------------------------------------------------------


class TestGenerateSrtForSlide:
    def test_basic_output(self, tmp_path):
        sentences = ["第一句话。", "第二句话。", "第三句话。"]
        duration = 9.0
        output = tmp_path / "test.srt"
        _generate_srt_for_slide(sentences, duration, output)

        assert output.exists()
        content = output.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        # 3 sentences → 3 SRT entries
        # Each entry: idx, timestamp, text, blank separator
        # Last entry's trailing blank is stripped
        assert len(lines) == 11  # (4 * 3) - 1 (trailing blank stripped)

    def test_timing_continuous(self, tmp_path):
        sentences = ["句一。", "句二。"]
        duration = 10.0
        output = tmp_path / "cont.srt"
        _generate_srt_for_slide(sentences, duration, output)

        content = output.read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")
        assert len(blocks) == 2

        # Parse times
        times = []
        for block in blocks:
            lines = block.split("\n")
            time_line = lines[1]
            start_str, end_str = time_line.split(" --> ")
            start = _parse_srt_time(start_str)
            end = _parse_srt_time(end_str)
            times.append((start, end))

        # Each block's start should equal previous block's end
        assert times[0][1] == pytest.approx(times[1][0], abs=0.01)

        # Total duration should be approximately `duration`
        total = times[-1][1]
        assert total == pytest.approx(duration, abs=0.01)

    def test_long_sentence_split(self, tmp_path):
        long_sent = "这是一段非常长的句子，需要被切分成多行字幕来显示，因为它的长度超过了默认的每行字符限制。"
        sentences = [long_sent]
        duration = 10.0
        output = tmp_path / "long.srt"
        _generate_srt_for_slide(sentences, duration, output, line_max_chars=20)

        content = output.read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")

        # Long sentence should be split into multiple entries
        assert len(blocks) > 1

        # Each text line should be <= line_max_chars
        for block in blocks:
            lines = block.split("\n")
            text = lines[2]
            assert len(text) <= 20

    def test_empty_sentences(self, tmp_path):
        output = tmp_path / "empty.srt"
        _generate_srt_for_slide([], 10.0, output)
        assert not output.exists()

    def test_total_duration(self, tmp_path):
        """Verify total SRT duration matches audio duration."""
        sentences = ["短句。", "这是一段比较长一些的句子。", "中。"]
        duration = 15.0
        output = tmp_path / "dur.srt"
        _generate_srt_for_slide(sentences, duration, output)

        content = output.read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")

        last_block = blocks[-1].split("\n")
        _, end_str = last_block[1].split(" --> ")
        last_end = _parse_srt_time(end_str)

        assert last_end == pytest.approx(duration, abs=0.05)

    def test_srt_format_valid(self, tmp_path):
        """Verify SRT format: sequential indices, valid timestamps."""
        sentences = ["第一句。", "第二句。"]
        duration = 6.0
        output = tmp_path / "fmt.srt"
        _generate_srt_for_slide(sentences, duration, output)

        content = output.read_text(encoding="utf-8")
        blocks = content.strip().split("\n\n")

        for i, block in enumerate(blocks):
            lines = block.split("\n")
            assert lines[0] == str(i + 1), f"Expected index {i + 1}, got {lines[0]}"
            assert " --> " in lines[1]
            # Validate timestamp format
            parts = lines[1].split(" --> ")
            for ts in parts:
                assert len(ts) == 12  # HH:MM:SS,MMM


def _parse_srt_time(srt_str: str) -> float:
    """Parse SRT timestamp to seconds."""
    h, m, rest = srt_str.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

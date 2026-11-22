import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "ppt-master" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tts_backends.subtitle_timing import DEFAULT_SUBTITLE_MAX_CHARS, format_word_timed_srt  # noqa: E402


def test_word_timed_srt_is_provider_neutral_and_text_faithful():
    boundaries = [
        {"text": "你好。", "offset": 0, "duration": 10_000_000},
        {"text": "世界。", "offset": 10_000_000, "duration": 20_000_000},
    ]
    result = format_word_timed_srt("你好。世界。", boundaries, DEFAULT_SUBTITLE_MAX_CHARS, provider_label="CosyVoice")
    assert result == "1\n00:00:00,000 --> 00:00:01,000\n你好。\n\n2\n00:00:01,000 --> 00:00:03,000\n世界。\n"


def test_word_timed_srt_rejects_mismatched_text():
    with pytest.raises(RuntimeError, match="CosyVoice.*could not be aligned"):
        format_word_timed_srt("不同文本", [{"text": "你好", "offset": 0, "duration": 1}], provider_label="CosyVoice")


@pytest.mark.parametrize("module_name", ["backend_cosyvoice", "backend_elevenlabs", "backend_minimax"])
def test_other_ppt_tts_backends_import_neutral_subtitle_module(module_name):
    __import__(f"tts_backends.{module_name}")

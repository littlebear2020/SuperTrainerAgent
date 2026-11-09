"""Task 3p: TTS 情感/重音 SSML——按句型标注，去平淡匀速念白 AI 味。"""

from app.gateway.video_gen.orchestration.script import (
    _annotate_emotion,
    detect_sentence_type,
)


def test_question_gets_rising_pitch():
    r = _annotate_emotion("你知道吗？", "question")
    assert "prosody" in r["ssml"] and "high" in r["ssml"]


def test_emphasis_gets_strong():
    r = _annotate_emotion("这点非常重要。", "emphasis")
    assert "emphasis" in r["ssml"] and "strong" in r["ssml"]


def test_conclusion_gets_slow():
    r = _annotate_emotion("总之，这就是答案。", "conclusion")
    assert "rate" in r["ssml"] and "slow" in r["ssml"]


def test_statement_default():
    r = _annotate_emotion("这是一个句子。", "statement")
    assert r["sentence_type"] == "statement"


def test_detect_question():
    assert detect_sentence_type("为什么？") == "question"


def test_detect_emphasis():
    assert detect_sentence_type("这很重要") == "emphasis"


def test_detect_conclusion():
    assert detect_sentence_type("总之，结束了") == "conclusion"

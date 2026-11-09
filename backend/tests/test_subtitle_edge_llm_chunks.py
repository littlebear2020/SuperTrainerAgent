"""T13（P0-2）：edge 帧（带 word_cues 的 srt）也消费 llm_chunks——文本边界来自 LLM 重切、时间戳来自词区间。

edge 分支原本只传 word_cues → 预计算的 llm_chunks 白付 LLM 费用（_precompute_llm_chunks
对带 srt 的帧照常预计算）。修复后 edge 分支两个 segment() 调用均传 llm_chunks。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.service import _write_storyboard_srt
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

_WORD_SRT = """1
00:00:00,000 --> 00:00:00,900
人工

2
00:00:01,000 --> 00:00:01,900
智能

3
00:00:02,000 --> 00:00:02,900
正在

4
00:00:03,000 --> 00:00:03,900
改变

5
00:00:04,000 --> 00:00:04,900
世界
"""


def _make_edge_storyboard() -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    return Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(
                index=0,
                narration="人工智能正在改变世界",
                duration=6.0,
                srt=_WORD_SRT,
            )
        ],
    )


def test_edge_frame_consumes_llm_chunks(tmp_path):
    """edge 帧 + llm_chunks_map 非空 → cue 文本边界与 LLM chunks 一致、时间戳仍来自词区间。"""
    srt_path = str(tmp_path / "subtitle.srt")
    llm_chunks_map = {0: ["人工智能正在", "改变世界"]}
    _write_storyboard_srt(_make_edge_storyboard(), srt_path, llm_chunks_map=llm_chunks_map)

    content = (tmp_path / "subtitle.srt").read_text(encoding="utf-8")
    blocks = [b.strip() for b in content.strip().split("\n\n")]
    assert len(blocks) == 2, f"cue 数应与 llm chunks 一致：{blocks!r}"
    texts = ["\n".join(b.split("\n")[2:]) for b in blocks]
    assert texts == ["人工智能正在", "改变世界"], f"cue 文本边界未采用 llm chunks：{texts!r}"
    # 时间戳仍来自 word_cues 区间：首 cue start == 首词 start
    assert blocks[0].split("\n")[1].startswith("00:00:00,000"), f"首 cue 时间戳偏离词区间：{blocks[0]!r}"


@pytest.mark.parametrize("llm_chunks_map", [None, {}])
def test_edge_frame_without_llm_chunks_rules_chain(tmp_path, llm_chunks_map):
    """无 llm_chunks_map → edge 分支走规则链（既有语义不变）。"""
    srt_path = str(tmp_path / "subtitle.srt")
    _write_storyboard_srt(_make_edge_storyboard(), srt_path, llm_chunks_map=llm_chunks_map)

    content = (tmp_path / "subtitle.srt").read_text(encoding="utf-8")
    blocks = [b.strip() for b in content.strip().split("\n\n")]
    texts = ["\n".join(b.split("\n")[2:]) for b in blocks]
    joined = "".join(texts)
    assert joined == "人工智能正在改变世界", f"规则链丢字：{texts!r}"

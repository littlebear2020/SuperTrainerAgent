"""行为锁：手改字幕必须解除「无字幕」锁定。

线上实测（2026-07-29）：创建时选「无字幕」→ subtitle_style="none" 落进
storyboard.config → build_worker_payload 永久不下发 subtitle_path
（5 个调用点无一例外）→ worker has_sub=False → 永不烧录。

四重欺骗：编辑器可用 → 保存返 {"locked": true} → toast「点重新合成烧入新字幕」
→ subtitle_locked 还让「重新合成」按钮变可点（resume-actions.ts）。
而 subtitle_locked 同时抑制 _write_storyboard_srt，用户的 SRT 在磁盘完好、
GET /subtitle 一直回显——每层看起来都对，只有成片里没字幕。

手改字幕这个动作本身就表达了「我要字幕」，故自动把 none 改回 default。
"""

import json

from app.gateway.routers.video_gen import _restore_subtitle_style_if_none
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb_dict(subtitle_style: str) -> dict:
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1152, height=2048, subtitle_style=subtitle_style),
        frames=[StoryboardFrame(index=0, narration="a")],
    )
    return json.loads(json.dumps(sb.to_dict(), ensure_ascii=False))


def test_none_is_restored_to_default():
    data = _sb_dict("none")
    assert _restore_subtitle_style_if_none(data) == "default"
    assert data["config"]["subtitle_style"] == "default"


def test_existing_style_untouched():
    """用户选了 bold 就保持 bold。"""
    data = _sb_dict("bold")
    assert _restore_subtitle_style_if_none(data) is None
    assert data["config"]["subtitle_style"] == "bold"


def test_missing_config_is_safe():
    assert _restore_subtitle_style_if_none({}) is None


def test_non_dict_config_is_safe():
    assert _restore_subtitle_style_if_none({"config": "oops"}) is None


def test_payload_ships_subtitle_after_restore():
    """端到端语义：改回 default 后 build_worker_payload 会下发 subtitle_path。"""
    data = _sb_dict("none")
    _restore_subtitle_style_if_none(data)
    sb = Storyboard.from_dict(data)
    # 复现 service.py 的判据
    assert sb.config.subtitle_style != "none"


def test_restore_survives_roundtrip():
    """改完能被 Storyboard.from_dict 正常读回（config 未被破坏）。"""
    data = _sb_dict("none")
    _restore_subtitle_style_if_none(data)
    sb = Storyboard.from_dict(data)
    assert sb.config.subtitle_style == "default"
    assert len(sb.frames) == 1


def test_worker_payload_actually_ships_subtitle_path(tmp_path):
    """真正的端到端判据：跑 build_worker_payload 本体，确认 subtitle_path 不再被置 None。

    只断言 `subtitle_style != "none"` 等于复述实现；这里直接验被修的那个下游。
    """
    from app.gateway.video_gen.service import build_worker_payload

    srt = str(tmp_path / "subtitle.srt")

    data = _sb_dict("none")
    before = build_worker_payload(Storyboard.from_dict(data), subtitle_path=srt, audio_concat_path=None)
    assert before.get("subtitle_path") is None, "前提：none 时本就不下发字幕"

    _restore_subtitle_style_if_none(data)
    after = build_worker_payload(Storyboard.from_dict(data), subtitle_path=srt, audio_concat_path=None)
    assert after.get("subtitle_path") == srt, "解除锁定后必须下发 subtitle_path，否则字幕仍烧不进成片"

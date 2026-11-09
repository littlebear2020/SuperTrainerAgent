"""字幕编辑端点 + subtitle_locked 语义（2026-07-27）。

锁语义：用户编辑置锁（PUT）；仅 narration 变更清锁（帧旁白编辑 / 音频重生 CAS miss）
→ SRT 从新旁白重新生成；re-render 不清锁（reuse_blueprint 保持 narration 不变）。
"""

import asyncio
import json

import pytest
from fastapi import HTTPException


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _write_storyboard(tmp_path, task_id="vg_sub", config_extra=None):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "video_type": "ai_image",
        "width": 1080,
        "height": 1920,
        "voice_id": "preset:146",
        "tts_speed": 1.2,
        "bgm_volume": 0.15,
    }
    cfg.update(config_extra or {})
    sb = {
        "title": "t",
        "config": cfg,
        "frames": [{"index": 0, "narration": "n0", "image_prompt": "p0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")
    return task_dir


class _Svc:
    def __init__(self, tmp_path, status="ready"):
        self._storage_dir = str(tmp_path)
        self._status = status
        self._sb_write_lock = asyncio.Lock()

    async def get_task(self, task_id, user_id):
        from types import SimpleNamespace

        return SimpleNamespace(status=self._status)

    async def touch_task(self, task_id):
        pass

    async def _update_storyboard(self, task_dir, updater):
        from app.gateway.video_gen.service import VideoGenService

        return await VideoGenService._update_storyboard(self, task_dir, updater)


@pytest.mark.asyncio
async def test_put_subtitle_then_get_roundtrip(tmp_path):
    from app.gateway.routers import video_gen as vg

    task_dir = _write_storyboard(tmp_path, "vg_sub")
    (task_dir / "subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\n原始字幕\n", encoding="utf-8")
    body = vg.UpdateSubtitleRequest(
        cues=[
            vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="改过的第一句"),
            vg.SubtitleCue(start="00:00:02,000", end="00:00:04,500", text="改过的第二句"),
        ]
    )
    resp = await vg.update_subtitle(_make_request(_Svc(tmp_path)), "vg_sub", body)
    assert resp["locked"] is True
    assert [c["text"] for c in resp["cues"]] == ["改过的第一句", "改过的第二句"]
    got = await vg.get_subtitle(_make_request(_Svc(tmp_path)), "vg_sub")
    assert got["locked"] is True
    assert got["cues"][0]["text"] == "改过的第一句"


def test_locked_subtitle_survives_write_storyboard_srt(tmp_path):
    """置锁后 _write_storyboard_srt 必须是 no-op（recompose 不覆盖用户编辑）。"""
    from app.gateway.video_gen.service import _write_storyboard_srt
    from app.gateway.video_gen.storyboard import Storyboard

    task_dir = _write_storyboard(tmp_path, "vg_sub")
    srt_path = task_dir / "subtitle.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:02,000\n用户改的字幕\n", encoding="utf-8")
    sb = Storyboard.from_dict(json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8")))
    sb.subtitle_locked = True

    _write_storyboard_srt(sb, str(srt_path))
    assert "用户改的字幕" in srt_path.read_text(encoding="utf-8"), "置锁后 SRT 被 narration 重写覆盖"


@pytest.mark.asyncio
async def test_put_subtitle_rejects_draft(tmp_path):
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_sub")
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start="00:00:00,000", end="00:00:01,000", text="x")])
    with pytest.raises(HTTPException) as exc:
        await vg.update_subtitle(_make_request(_Svc(tmp_path, status="draft")), "vg_sub", body)
    assert exc.value.status_code == 409


def test_subtitle_cue_validates_input():
    from app.gateway.routers import video_gen as vg

    with pytest.raises(Exception):
        vg.UpdateSubtitleRequest(cues=[])  # min_length=1
    with pytest.raises(Exception):
        vg.SubtitleCue(start="00:00:00,000", end="00:00:01,000", text="x" * 300)  # max 200


@pytest.mark.asyncio
async def test_put_subtitle_rejects_inverted_timestamps(tmp_path):
    """2026-07-27 评审 D-F3：_SRT_TS_RE 只查格式——start>=end、mm/ss 超 59 的
    非法 SRT 会延迟到 worker 烧录才炸，必须在入口 422。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_sub")
    svc = _Svc(tmp_path)

    # start == end / start > end
    for start, end in [("00:00:02,000", "00:00:02,000"), ("00:00:03,000", "00:00:01,000")]:
        body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start=start, end=end, text="x")])
        with pytest.raises(HTTPException) as exc:
            await vg.update_subtitle(_make_request(svc), "vg_sub", body)
        assert exc.value.status_code == 422, f"{start} --> {end} 应被拒绝"

    # 分钟/秒超出 59（格式正则挡不住）
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start="00:99:00,000", end="00:99:01,000", text="x")])
    with pytest.raises(HTTPException) as exc:
        await vg.update_subtitle(_make_request(svc), "vg_sub", body)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_narration_edit_clears_lock(tmp_path):
    from app.gateway.routers import video_gen as vg

    task_dir = _write_storyboard(tmp_path, "vg_sub")
    # 先置锁（模拟用户已编辑字幕）
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    data["subtitle_locked"] = True
    (task_dir / "storyboard.json").write_text(json.dumps(data), encoding="utf-8")

    body = vg.UpdateFrameRequest(narration="用户又改了旁白")
    await vg.update_storyboard_frame(_make_request(_Svc(tmp_path)), "vg_sub", 0, body)

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data.get("subtitle_locked") is False, "旁白变更后锁必须清除"


# ---------------------------------------------------------------------------
# 2026-07-31 T6：单行约束——换行折叠而非拒绝（历史双行 cue 兼容）
# ---------------------------------------------------------------------------


def test_subtitle_cue_max_length_alone_does_not_block_newlines():
    """反证既有 max_length=200 不足：'abc\\ndef'(7 字) 通过长度校验却是两行。"""
    from app.gateway.routers import video_gen as vg

    cue = vg.SubtitleCue(start="00:00:00,000", end="00:00:01,000", text="abc\ndef")
    assert "\n" not in cue.text, "换行必须被折叠（否则多行 cue 压画面）"
    assert cue.text == "abc def"


def test_subtitle_cue_collapses_crlf_variants():
    from app.gateway.routers import video_gen as vg

    for raw, expected in [
        ("第一行\n第二行", "第一行 第二行"),
        ("第一行\r\n第二行", "第一行 第二行"),
        ("第一行\r第二行", "第一行 第二行"),
        ("第一行\n\n\n第二行", "第一行 第二行"),
        ("  前后空白  ", "前后空白"),
    ]:
        cue = vg.SubtitleCue(start="00:00:00,000", end="00:00:01,000", text=raw)
        assert cue.text == expected, f"{raw!r} → {cue.text!r}，期望 {expected!r}"


def test_subtitle_cue_rejects_whitespace_only_text():
    """纯换行/纯空白折叠后为空串 —— 必须显式报错，不得绕过 min_length。"""
    from app.gateway.routers import video_gen as vg

    for raw in ("\n", "\n\n", "   ", "\r\n", " \n "):
        with pytest.raises(Exception):
            vg.SubtitleCue(start="00:00:00,000", end="00:00:01,000", text=raw)


@pytest.mark.asyncio
async def test_legacy_two_line_srt_get_put_roundtrip_does_not_422(tmp_path):
    """2026-07-27 前 _wrap_subtitle 产的双行 cue：GET 回传含 \\n，用户不改任何字
    直接 PUT 必须成功（旧实现会 422 → 旧任务字幕编辑功能不可用）。"""
    from app.gateway.routers import video_gen as vg

    task_dir = _write_storyboard(tmp_path, "vg_sub")
    # 历史双行 cue（实跑产物：26 字文本 max_chars=16 → 双行）
    (task_dir / "subtitle.srt").write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n这是一段很长的旁白文本需要断成两\n行显示因为超过了限制\n",
        encoding="utf-8",
    )
    got = await vg.get_subtitle(_make_request(_Svc(tmp_path)), "vg_sub")
    assert "\n" in got["cues"][0]["text"], "GET 应原样回传历史双行文本（前提成立才有本测试）"

    # 用户不改任何字，原样 PUT
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start=c["start"], end=c["end"], text=c["text"]) for c in got["cues"]])
    resp = await vg.update_subtitle(_make_request(_Svc(tmp_path)), "vg_sub", body)
    assert resp["locked"] is True
    assert resp["cues"][0]["text"] == "这是一段很长的旁白文本需要断成两 行显示因为超过了限制"
    assert "\n" not in resp["cues"][0]["text"], "折叠后写盘不得再含换行"


@pytest.mark.asyncio
async def test_put_subtitle_written_srt_has_no_multiline_cue(tmp_path):
    """写盘的 SRT 每条 cue 恒单行——多行会让 _read_srt_cues 与 libass 语义漂移。"""
    from app.gateway.routers import video_gen as vg

    task_dir = _write_storyboard(tmp_path, "vg_sub")
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="上一行\n下一行")])
    await vg.update_subtitle(_make_request(_Svc(tmp_path)), "vg_sub", body)
    content = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        assert len(lines) == 3, f"cue 块应恰为 3 行（序号/时间/文本），实得 {lines!r}"


@pytest.mark.asyncio
async def test_put_subtitle_preserves_user_punctuation(tmp_path):
    """D4：手改不清洗——用户输入的标点原样保留（与生成侧去尾标点有意不同）。"""
    from app.gateway.routers import video_gen as vg

    _write_storyboard(tmp_path, "vg_sub")
    body = vg.UpdateSubtitleRequest(cues=[vg.SubtitleCue(start="00:00:00,000", end="00:00:02,000", text="你好。")])
    resp = await vg.update_subtitle(_make_request(_Svc(tmp_path)), "vg_sub", body)
    assert resp["cues"][0]["text"] == "你好。", "手改路径不得改写用户文本（D4）"

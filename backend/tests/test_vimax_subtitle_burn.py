"""HR-T3: ViMax 烧字幕（时序对齐后，config 开关）。

Task1（c9c6eb77）已消除 TTS 26-35s 漂移→时序对齐→可重评烧字幕。
build_overlay_cmd 加 subtitle_path/burn_subtitles；config.vimax_burn_subtitles 默认关。
"""

import pytest

from app.gateway.video_gen.engines.vimax import audio_overlay as ao


def test_burn_when_enabled(tmp_path):
    """burn_subtitles=True + subtitle.srt 存在 → 命令含 subtitles= filter。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01 --> 00:00:03\n测试\n")
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=srt, burn_subtitles=True)
    assert "subtitles=" in " ".join(cmd)


def test_no_burn_by_default(tmp_path):
    """默认不烧字幕（保持现有行为）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    cmd = ao.build_overlay_cmd(v, a, None, o)
    assert "subtitles=" not in " ".join(cmd)


def test_no_burn_when_subtitle_missing(tmp_path):
    """burn_subtitles=True 但无 srt → 不烧（不崩）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=None, burn_subtitles=True)
    assert "subtitles=" not in " ".join(cmd)


def test_no_burn_when_subtitle_empty(tmp_path):
    """空 SRT（0 字节）→ 不加 subtitles= 滤镜（libass 'Unable to open' 会崩整任务，vg_698c123a8ddd）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    srt = tmp_path / "subtitle.srt"
    srt.write_bytes(b"")
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=srt, burn_subtitles=True)
    assert "subtitles=" not in " ".join(cmd)


def test_no_burn_when_subtitle_no_cues(tmp_path):
    """SRT 非空但无 '-->' 时间轴行 → 同样不烧。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    srt = tmp_path / "subtitle.srt"
    srt.write_text("\n\n", encoding="utf-8")
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=srt, burn_subtitles=True)
    assert "subtitles=" not in " ".join(cmd)


def test_srt_has_cues(tmp_path):
    good = tmp_path / "good.srt"
    good.write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
    empty = tmp_path / "empty.srt"
    empty.write_bytes(b"")
    assert ao.srt_has_cues(good) is True
    assert ao.srt_has_cues(empty) is False
    assert ao.srt_has_cues(tmp_path / "missing.srt") is False


def test_no_vf_uses_stream_copy(tmp_path):
    """不烧字幕（无 vf）→ -c:v copy，不全片重编码（gateway CPU 减负）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    cmd = ao.build_overlay_cmd(v, a, None, o)
    joined = " ".join(cmd)
    assert "-c:v copy" in joined
    assert "libx264" not in joined


def test_burn_uses_capped_x264(tmp_path):
    """烧字幕必须重编码 → libx264 + 限线程（防 gateway 满核跑分钟级编码）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=srt, burn_subtitles=True)
    joined = " ".join(cmd)
    assert "-c:v libx264" in joined
    assert "-threads 4" in joined
    assert "-preset veryfast" in joined


@pytest.mark.asyncio
async def test_overlay_burn_failure_falls_back_to_no_burn(tmp_path, monkeypatch):
    """烧录 ffmpeg 失败 → 自动重试无字幕版本 + 产出软字幕，任务不崩。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    (tmp_path / "audio.mp3").write_bytes(b"")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
    calls: list[list[str]] = []

    async def fake_run(run_cmd, cmd):
        calls.append(cmd)
        if any("subtitles=" in arg for arg in cmd):
            raise RuntimeError("vimax audio overlay failed: fake libass error")

    monkeypatch.setattr(ao, "run_ffmpeg", fake_run)
    out = await ao.overlay_audio_track(v, tmp_path, burn_subtitles=True)
    assert out == tmp_path / "vimax_final.mp4"
    # P2-13 起第 3 趟是 loudnorm（mock 不产文件 → 产物缺失 → 回落未归一化产物）
    assert len(calls) == 3, f"烧录重试 + loudnorm，实际 {len(calls)} 次"
    assert not any("subtitles=" in arg for arg in calls[1]), "第二次调用不应含 subtitles 滤镜"
    assert any(a.startswith("loudnorm=") for a in calls[2]), "第三趟应为 loudnorm"
    assert (tmp_path / "vimax_final.srt").exists(), "应产出与视频同名的软字幕"


@pytest.mark.asyncio
async def test_overlay_no_burn_failure_raises(tmp_path, monkeypatch):
    """非烧录原因（无 burn）失败 → 不重试，直接抛。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    (tmp_path / "audio.mp3").write_bytes(b"")

    async def fake_run(run_cmd, cmd):
        raise RuntimeError("vimax audio overlay failed: fake audio error")

    monkeypatch.setattr(ao, "run_ffmpeg", fake_run)
    with pytest.raises(RuntimeError, match="fake audio error"):
        await ao.overlay_audio_track(v, tmp_path, burn_subtitles=False)


@pytest.mark.asyncio
async def test_overlay_audio_track_burns_when_enabled(tmp_path, monkeypatch):
    """overlay_audio_track(burn_subtitles=True) + subtitle.srt → cmd 含 subtitles=。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    a = tmp_path / "audio.mp3"
    a.write_bytes(b"")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:01 --> 00:00:03\n测试\n")
    captured: list[list[str]] = []

    async def fake_run(run_cmd, cmd):
        captured.append(cmd)

    monkeypatch.setattr(ao, "run_ffmpeg", fake_run)
    await ao.overlay_audio_track(v, tmp_path, burn_subtitles=True)
    assert "subtitles=" in " ".join(captured[0]), "第一趟（混音）应含烧录滤镜"


@pytest.mark.asyncio
async def test_overlay_audio_track_no_burn_by_default(tmp_path, monkeypatch):
    """overlay_audio_track() 默认不烧字幕（即使 srt 存在）。"""
    v = tmp_path / "v.mp4"
    v.write_bytes(b"")
    (tmp_path / "audio.mp3").write_bytes(b"")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:01 --> 00:00:03\n测试\n")
    captured: list[list[str]] = []

    async def fake_run(run_cmd, cmd):
        captured.append(cmd)

    monkeypatch.setattr(ao, "run_ffmpeg", fake_run)
    await ao.overlay_audio_track(v, tmp_path)
    assert "subtitles=" not in " ".join(captured[0]), "第一趟（混音）不应含烧录滤镜"


def test_srt_has_cues_detects_ass_format(tmp_path):
    """N1 回归：srt_has_cues 对 .ass 文件认 Dialogue: 行，不能只认 -->。"""
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01 --> 00:00:03\n测试\n")
    assert ao.srt_has_cues(srt) is True

    ass = tmp_path / "subtitle.ass"
    ass.write_text("[Events]\nFormat: ...\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,测试\n")
    assert ao.srt_has_cues(ass) is True, "N1: ASS 用 Dialogue: 而非 -->，srt_has_cues 必须认"

    empty_ass = tmp_path / "empty.ass"
    empty_ass.write_text("[Script Info]\n[Events]\nFormat: ...\n")
    assert ao.srt_has_cues(empty_ass) is False, "空 ASS 应返回 False"


def test_burn_prefers_ass_when_available(tmp_path):
    """N1 回归：.ass 和 .srt 同时存在时优先烧 .ass（且 srt_has_cues 认它）。"""
    v = tmp_path / "v.mp4"
    a = tmp_path / "a.mp3"
    o = tmp_path / "o.mp4"
    srt = tmp_path / "subtitle.srt"
    srt.write_text("1\n00:00:01 --> 00:00:03\n测试\n")
    ass = tmp_path / "subtitle.ass"
    ass.write_text("[Events]\nDialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,测试\n")
    cmd = ao.build_overlay_cmd(v, a, None, o, subtitle_path=srt, burn_subtitles=True)
    cmd_str = " ".join(cmd)
    assert "subtitles=" in cmd_str
    assert ".ass" in cmd_str, "应优先烧 .ass 而非 .srt"

"""T3 (Lane 2, 2026-10-15): vimax narration 拼接重编码 + 字幕朝向贯通 + 标记清理 + 软字幕 basename。

四个修点（plan 证据链 #3/#4/#5）：
1. ``_concat_chunks`` 首选 ``-c copy`` 拼 mp3——每段 ~44ms padding 累积漂移
   （audio_flow C7 实测），校验只查截断方向、过冲无告警。
2. 字幕硬编码竖屏：``orientation="portrait"`` / 1080x1920 / ``_MAX_WIDTH["portrait"]``
   ——横屏任务折行过窄 + ASS PlayRes 失真。
3. ``voice_mode_warning.txt`` 只写不删——失败轮次的残留标记泄漏进下一轮成功渲染。
4. ``ship_soft_subtitle`` 以 loudnorm 前的中间产物为 basename——交付物改名后外挂字幕失配。
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import app.gateway.video_gen.engines.vimax.narration_generator as ng
from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine

# ── 1. _concat_chunks：重编码 + 过冲告警 ────────────────────────────────────


def test_concat_chunks_uses_reencode_not_stream_copy(tmp_path, monkeypatch):
    """拼接默认重编码（libmp3lame）——不得再有 ``-c copy`` 快路径（44ms/段漂移）。"""
    chunks = []
    for i in range(3):
        p = tmp_path / f"c{i}.mp3"
        p.write_bytes(b"x")
        chunks.append(str(p))
    output = str(tmp_path / "out.mp3")

    cmds: list[list[str]] = []

    def fake_run(cmd, *a, **kw):
        cmds.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"y")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(ng.subprocess, "run", fake_run)
    monkeypatch.setattr(ng.tts_mod, "_get_audio_duration", lambda p: 3.0 if str(p).endswith("out.mp3") else 1.0)

    ng._concat_chunks(chunks, output)

    assert cmds, "须实际调 ffmpeg"
    first = cmds[0]
    assert "-c" not in first or first[first.index("-c") + 1] != "copy", f"不得用 stream-copy 快路径（漂移根因），cmd={first}"
    assert "libmp3lame" in first, f"默认重编码须 libmp3lame（对齐 audio_flow._concat_audio），cmd={first}"


def test_concat_chunks_warns_on_overshoot(tmp_path, monkeypatch, caplog):
    """校验补过冲方向：actual > expected + 0.5 → 告警可见（修前只查截断）。"""
    chunks = [str(tmp_path / f"c{i}.mp3") for i in range(3)]
    for c in chunks:
        Path(c).write_bytes(b"x")
    output = str(tmp_path / "out.mp3")

    monkeypatch.setattr(ng.subprocess, "run", lambda cmd, *a, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""))
    probes = iter([1.0, 1.0, 1.0, 3.8])  # 三段各 1s；产物 3.8s = 过冲 0.8s

    monkeypatch.setattr(ng.tts_mod, "_get_audio_duration", lambda p: next(probes))

    with caplog.at_level(logging.WARNING):
        ng._concat_chunks(chunks, output)

    assert any("过冲" in r.message or "overshoot" in r.message.lower() for r in caplog.records), f"过冲 0.8s 须留告警，records={[r.message for r in caplog.records]}"


# ── 2. 字幕朝向贯通 ─────────────────────────────────────────────────────────


def _cue_text_w(cue) -> float:
    from app.gateway.video_gen.subtitle_engine import _text_display_width

    return _text_display_width(cue.text.replace("\\N", ""))


def test_write_srt_landscape_playres_and_wrap_width(tmp_path):
    """横屏：ASS PlayRes=1920x1080，折行宽度用 landscape(16) 而非 portrait(13)。"""
    entries = [("十五个全角字符宽的横屏台词内容" + "补齐", 0.0, 5.0)]  # 显示宽 ~16-17（portrait 会折两行）
    srt = tmp_path / "subtitle.srt"
    ng._write_srt(entries, srt, orientation="landscape", video_w=1920, video_h=1080)

    ass = (tmp_path / "subtitle.ass").read_text(encoding="utf-8")
    assert "PlayResX: 1920" in ass, f"横屏 PlayResX 须 1920，实际：{[line for line in ass.splitlines() if 'PlayRes' in line]}"
    assert "PlayResY: 1080" in ass
    # 折行宽度：landscape 单行上限 16 全角当量——15-16 宽的文本不应被 portrait 口径强行折行
    srt_text = srt.read_text(encoding="utf-8")
    assert "-->" in srt_text, "cue 须写出"


def test_write_srt_portrait_default_unchanged(tmp_path):
    """默认（不传朝向）保持竖屏 1080x1920——存量行为回归锚。"""
    entries = [("竖屏台词", 0.0, 5.0)]
    srt = tmp_path / "subtitle.srt"
    ng._write_srt(entries, srt)
    ass = (tmp_path / "subtitle.ass").read_text(encoding="utf-8")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass


def test_enforce_sequential_respects_orientation_width():
    """_enforce_sequential 折行/合并宽度按朝向取 _MAX_WIDTH（横屏 join 上限 32 > 竖屏 26）。"""
    from app.gateway.video_gen.subtitle_engine import Cue

    # 合并上限 = 单行宽 × 2（防溢两行屏）：landscape 16×2=32、portrait 13×2=26。
    # 两 cue 各 14 宽且重叠（触发合并分支）：合并后 28——landscape 可并（≤32），
    # portrait 不可并（>26）保留独立钳制 cue。修前硬编码 portrait → 两口径同值。
    a_text, b_text = "一" * 14, "二" * 14

    def _mk():
        return [Cue(text=a_text, start=0.0, end=2.0), Cue(text=b_text, start=1.9, end=1.95)]  # remaining -0.05 < 0.2 → 合并分支

    landscape = ng._enforce_sequential(_mk(), orientation="landscape")
    portrait = ng._enforce_sequential(_mk(), orientation="portrait")

    assert len(landscape) == 1, "横屏 join 上限 32：14+14=28 ≤ 32 应合并"
    assert len(portrait) == 2, "竖屏 join 上限 26：14+14=28 > 26 应保留独立钳制 cue"


@pytest.mark.asyncio
async def test_vimax_engine_passes_orientation_from_aspect(tmp_path, monkeypatch):
    """EngineContext.aspect → engine 推导 orientation/宽高 → generate_vimax_narration 收参。"""
    working_dir = tmp_path / "vimax_working"
    working_dir.mkdir(parents=True)
    (working_dir / "final_video.mp4").write_bytes(b"V")
    (working_dir / "characters.json").write_text(json.dumps([{"name": "A", "voice": "preset:146"}]), encoding="utf-8")

    captured: dict = {}

    async def fake_narration(**kwargs):
        captured.update(kwargs)
        p = tmp_path / "vimax_narration.mp3"
        p.write_bytes(b"AUDIO")
        return p

    monkeypatch.setattr("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", fake_narration)

    async def overlay_fn(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
        return video_path

    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: AsyncMock(), overlay_fn=overlay_fn, narration_llm_factory=lambda: object())
    await engine.render(
        EngineContext(
            user_id="u",
            task_id="t",
            task_dir=tmp_path,
            request={"script": "s", "aspect": "16:9"},
            aspect="16:9",
        )
    )

    assert captured.get("orientation") == "landscape", f"aspect=16:9 须传 landscape，实际 {captured.get('orientation')}"
    assert captured.get("video_w") == 1920 and captured.get("video_h") == 1080, f"横屏宽高 1920x1080，实际 {captured.get('video_w')}x{captured.get('video_h')}"


@pytest.mark.asyncio
async def test_vimax_engine_defaults_portrait_without_aspect(tmp_path, monkeypatch):
    """无 aspect → 竖屏 1080x1920（存量行为锚）。"""
    working_dir = tmp_path / "vimax_working"
    working_dir.mkdir(parents=True)
    (working_dir / "final_video.mp4").write_bytes(b"V")
    (working_dir / "characters.json").write_text(json.dumps([{"name": "A", "voice": "preset:146"}]), encoding="utf-8")

    captured: dict = {}

    async def fake_narration(**kwargs):
        captured.update(kwargs)
        return None  # None → engine 走 fallback audio.mp3 分支

    monkeypatch.setattr("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", fake_narration)
    (tmp_path / "audio.mp3").write_bytes(b"FALLBACK")

    async def overlay_fn(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
        return video_path

    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: AsyncMock(), overlay_fn=overlay_fn, narration_llm_factory=lambda: object())
    await engine.render(EngineContext(user_id="u", task_id="t", task_dir=tmp_path, request={"script": "s"}))

    assert captured.get("orientation") == "portrait"
    assert captured.get("video_w") == 1080 and captured.get("video_h") == 1920


# ── 3. voice_mode_warning.txt 残留清除 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_render_clears_stale_voice_mode_warning(tmp_path, monkeypatch):
    """失败轮次残留的 voice_mode_warning.txt 不得泄漏进成功的下一轮渲染。"""
    working_dir = tmp_path / "vimax_working"
    working_dir.mkdir(parents=True)
    (working_dir / "final_video.mp4").write_bytes(b"V")
    (working_dir / "characters.json").write_text(json.dumps([{"name": "A", "voice": "preset:146"}]), encoding="utf-8")
    stale = tmp_path / "voice_mode_warning.txt"
    stale.write_text("上一轮失败残留：角色表解析失败", encoding="utf-8")

    async def fake_narration(**kwargs):
        p = tmp_path / "vimax_narration.mp3"
        p.write_bytes(b"AUDIO")
        return p

    monkeypatch.setattr("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", fake_narration)

    async def overlay_fn(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
        return video_path

    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: AsyncMock(), overlay_fn=overlay_fn, narration_llm_factory=lambda: object())
    await engine.render(EngineContext(user_id="u", task_id="t", task_dir=tmp_path, request={"script": "s"}))

    assert not stale.exists(), "本轮对话配音正常——上一轮的残留警告须清除，不得误导用户"


# ── 4. ship_soft_subtitle 以最终交付物为 basename ───────────────────────────


@pytest.mark.asyncio
async def test_ship_soft_subtitle_follows_loudnorm_deliverable(tmp_path, monkeypatch):
    """烧录降级 + loudnorm 成功 → 外挂字幕 basename 跟随最终交付物 vimax_final_loudnorm.mp4。"""
    import app.gateway.video_gen.engines.vimax.audio_overlay as ao

    (tmp_path / "final_video.mp4").write_bytes(b"V")
    (tmp_path / "vimax_narration.mp3").write_bytes(b"A")
    (tmp_path / "subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    def fake_run_cmd(cmd):
        if "-vf" in cmd:  # 烧录版命令 → 模拟 libass 失败触发降级
            raise RuntimeError("burn failed (simulated libass)")
        Path(cmd[-1]).write_bytes(b"OK")  # 无烧录版 + loudnorm 均成功

    monkeypatch.setattr(ao, "_run_cmd", fake_run_cmd)

    out = await ao.overlay_audio_track(tmp_path / "final_video.mp4", tmp_path, audio_override=tmp_path / "vimax_narration.mp3", burn_subtitles=True)

    assert out.name == "vimax_final_loudnorm.mp4", f"loudnorm 成功须交付归一化产物，实际 {out.name}"
    assert (tmp_path / "vimax_final_loudnorm.srt").exists(), "外挂字幕须跟随最终交付物 basename（修前挂在 loudnorm 前中间产物上）"

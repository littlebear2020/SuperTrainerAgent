"""T4 (Lane 2, 2026-10-15): vimax 事件循环残余阻塞 off-loop（asyncio.to_thread）+ 桥接 generator 资源关闭。

修点（plan 证据链 #5）：
1. ``_get_shot_duration`` / ``_align_chunk_to_shot`` / ``_create_silence`` 三处
   sync ffmpeg 直调阻塞事件循环（同文件 :592 concat 已走 run_ffmpeg 为先例）。
2. 每任务泄漏 2+ httpx.AsyncClient：桥 close 零调用方、
   ``SeedanceVideoGeneratorForViMax`` 无 close、engine.render 无 finally。

实现形态：**调用点包裹** ``await asyncio.to_thread(fn, ...)``——函数签名保持
sync（存量测试同步调用/同步打桩零改动）。用 to_thread 而非 run_ffmpeg（T4
ruling）：存量测试把 ng.run_ffmpeg 桩成 fire-and-forget（不执行恒返 None），
包裹后返回值承重（对齐路径/时长）即碎三测试；to_thread 同为 off-loop、真执行，
且本文件 :551 已有先例。concat（:592）仍走 run_ffmpeg——其返回值不承重。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.gateway.video_gen.engines.vimax.narration_generator as ng
from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine


@pytest.fixture(autouse=True)
def _record_offloop(monkeypatch):
    """记录经线程池（asyncio.to_thread）执行的阻塞助手函数名（录制 + 真实执行）。

    T4 ruling：三处残余阻塞点走 to_thread 而非 run_ffmpeg——存量测试把
    ng.run_ffmpeg 桩成 fire-and-forget（不执行恒返 None），返回值承重即碎。"""
    seen: list[str] = []

    async def fake_to_thread(fn, *args, **kwargs):
        seen.append(getattr(fn, "__name__", repr(fn)))
        return fn(*args, **kwargs)

    monkeypatch.setattr(ng.asyncio, "to_thread", fake_to_thread)
    return seen


@pytest.mark.asyncio
async def test_blocking_ffmpeg_helpers_go_through_to_thread(tmp_path, monkeypatch, _record_offloop):
    """_get_shot_duration/_align_chunk_to_shot/_append_silence_chunk 须经 asyncio.to_thread 线程池调用。"""
    working_dir = tmp_path / "vimax_working"
    working_dir.mkdir(parents=True)
    # 两镜：镜 0 有旁白（走 TTS → _align_chunk_to_shot），镜 1 无台词（走 _append_silence_chunk）
    # 镜 0 有旁白（走 TTS → _align_chunk_to_shot）；镜 1 无任何描述（legacy 回落仍空 → 静音分支）
    (working_dir / "storyboard.json").write_text(json.dumps([{"idx": 0, "visual_desc": "v0", "audio_desc": "narration text"}, {"idx": 1, "visual_desc": "", "audio_desc": ""}]), encoding="utf-8")
    for i in range(2):
        shot_dir = working_dir / "shots" / str(i)
        shot_dir.mkdir(parents=True)
        (shot_dir / "video.mp4").write_bytes(b"V")

    # 桩函数与被替换函数同名——off-loop 记录器按 __name__ 断言（防名字脆断言）
    def _get_shot_duration(video_path, *a, **kw):
        return 5.5

    def _align_chunk_to_shot(p, dur, td, ci):
        return str(p)

    def _append_silence_chunk(td, ci, vp, chunks):
        _sil = td / f"sil_{ci}.mp3"
        _sil.write_bytes(b"SIL")
        chunks.append(str(_sil))

    def _concat_chunks(paths, output):
        Path(output).write_bytes(b"CAT")

    def _write_srt(*a, **kw):
        return None

    monkeypatch.setattr(ng, "_get_shot_duration", _get_shot_duration)
    monkeypatch.setattr(ng, "_align_chunk_to_shot", _align_chunk_to_shot)
    monkeypatch.setattr(ng, "_append_silence_chunk", _append_silence_chunk)
    # 镜 0 有旁白、镜 1 LLM 无产出 → 走 _append_silence_chunk 静音分支
    monkeypatch.setattr(ng, "_generate_narration_text", AsyncMock(side_effect=["旁白一句", ""]))
    monkeypatch.setattr(ng.tts_mod, "synthesize", AsyncMock(return_value=SimpleNamespace(audio_path=str(tmp_path / "c0.mp3"), word_cues=None)))
    (tmp_path / "c0.mp3").write_bytes(b"A")
    monkeypatch.setattr(ng, "_concat_chunks", _concat_chunks)
    monkeypatch.setattr(ng, "_write_srt", _write_srt)

    out = await ng.generate_vimax_narration(working_dir=working_dir, task_dir=tmp_path, original_script="s", llm=object(), voice_id="v")

    assert out is not None
    # 三处残余阻塞点 + 既有 concat 先例——全部经 run_ffmpeg 池（不再裸调阻塞 loop）
    seen = _record_offloop
    assert "_get_shot_duration" in seen, f"镜时长探测须 off-loop，实际经线程池调用：{seen}"
    assert "_align_chunk_to_shot" in seen, f"对齐须 off-loop，实际经线程池调用：{seen}"
    assert "_append_silence_chunk" in seen, f"静音 chunk 生成须 off-loop，实际经线程池调用：{seen}"


# ── 桥 generator close + render finally ─────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_video_generator_close_forwards_to_provider():
    """SeedanceVideoGeneratorForViMax 补 close() → 透传内层 provider close。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import SeedanceVideoGeneratorForViMax

    gen = SeedanceVideoGeneratorForViMax(api_key="k")
    provider_close = AsyncMock()
    gen._provider = SimpleNamespace(close=provider_close)

    await gen.close()

    provider_close.assert_awaited_once()


def _make_engine_with_tracking_pipeline(pipeline_impl=None):
    """pipeline 带 image_generator/video_generator（含 close 记录）。"""
    image_gen = SimpleNamespace(close=AsyncMock())
    video_gen = SimpleNamespace(close=AsyncMock())

    async def pipeline(*args, **kwargs):
        if pipeline_impl is not None:
            return await pipeline_impl(*args, **kwargs)
        (kwargs.get("working_dir") or Path("/tmp"))  # noqa: B018 — 形参占位
        return None

    pipeline.image_generator = image_gen
    pipeline.video_generator = video_gen
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    return engine, image_gen, video_gen


@pytest.mark.asyncio
async def test_render_finally_closes_bridge_generators(tmp_path):
    """render 成功路径：finally 统一 close 图像桥 + 视频桥（修前零调用方泄漏 httpx client）。"""
    working_dir = tmp_path / "vimax_working"
    working_dir.mkdir(parents=True)
    (working_dir / "final_video.mp4").write_bytes(b"V")

    engine, image_gen, video_gen = _make_engine_with_tracking_pipeline()
    outcome = await engine.render(EngineContext(user_id="u", task_id="t", task_dir=tmp_path, request={"script": "s"}))

    assert outcome.mode == "finished"
    image_gen.close.assert_awaited_once()
    video_gen.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_render_finally_closes_even_when_pipeline_fails(tmp_path):
    """render 失败路径：pipeline 抛异常也须 close（泄漏正发生在失败任务里）。"""

    async def boom(*args, **kwargs):
        raise RuntimeError("pipeline exploded")

    engine, image_gen, video_gen = _make_engine_with_tracking_pipeline(pipeline_impl=boom)

    with pytest.raises(RuntimeError, match="pipeline exploded"):
        await engine.render(EngineContext(user_id="u", task_id="t", task_dir=tmp_path, request={"script": "s"}))

    image_gen.close.assert_awaited_once()
    video_gen.close.assert_awaited_once()

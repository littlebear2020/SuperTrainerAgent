"""Task 11 (N-1): ViMax 全静音旁白不得标「已完成」。

``generate_vimax_narration`` 里每镜 TTS 失败会 ``_append_silence_chunk`` 塞一段静音
（保 A/V 同步，本身是对的），但**全部镜头都失败时**：

    :372  if not audio_chunks:  → 才走 _fallback_trainerai_audio → 可能 None → 响亮失败
    :388  if spoken == 0 and srt_entries: logger.warning(...)   ← 只是一条日志
    :391  return output_path                                    ← 照样返回成功

静音 chunk 让 ``audio_chunks`` **非空**，于是 :372 的守卫放行；engine.py:334 只在
``narration_audio is None`` 时打失败 → 用户拿到一个**完全无声**的视频、状态「已完成」。

修法：全静音（spoken == 0 且确有镜头）视为失败，返回 None 交给 engine 已有的
loud-fail 分支。部分静音仍交付（保留既有降级语义）。
"""

from pathlib import Path

import pytest

from app.gateway.video_gen.engines.vimax import narration_generator as ng


def _write_storyboard(working_dir: Path, n_shots: int):
    import json

    working_dir.mkdir(parents=True, exist_ok=True)
    shots = [{"idx": i, "visual_desc": f"镜头{i}画面", "audio_desc": f"小明：「台词{i}」"} for i in range(n_shots)]
    (working_dir / "storyboard.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    # characters.json 触发对话模式（不走 LLM 旁白）
    (working_dir / "characters.json").write_text(
        json.dumps([{"name": "小明", "static_features": "男，20岁"}], ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_all_shots_tts_failure_returns_none(tmp_path, monkeypatch):
    """全部镜头 TTS 失败 → 返回 None（让 engine 打成失败），不得返回无声成品。"""
    working_dir = tmp_path / "work"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    _write_storyboard(working_dir, 3)

    async def always_fail(*a, **kw):
        raise RuntimeError("TTS down")

    monkeypatch.setattr(ng.tts_mod, "synthesize", always_fail)
    monkeypatch.setattr(ng, "_append_silence_chunk", lambda td, ci, vp, chunks: chunks.append(str(td / f"sil_{ci}.mp3")))

    async def fake_run_ffmpeg(fn, *a, **kw):
        return None

    monkeypatch.setattr(ng, "run_ffmpeg", fake_run_ffmpeg)

    result = await ng.generate_vimax_narration(
        working_dir=working_dir,
        task_dir=task_dir,
        voice_id="zh-CN-YunjianNeural",
        speed=1.0,
        llm=None,
        original_script="剧本",
    )

    assert result is None, "全静音仍返回音频路径 —— 用户会拿到完全无声的视频且状态「已完成」"


@pytest.mark.asyncio
async def test_partial_silence_still_delivers(tmp_path, monkeypatch):
    """部分镜头失败 → 仍交付（保留既有降级语义，不得因本 Task 变严）。"""
    working_dir = tmp_path / "work"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    _write_storyboard(working_dir, 3)

    calls = {"n": 0}

    async def flaky(text, *, voice_id, speed, output_path, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("TTS hiccup")
        Path(output_path).write_bytes(b"mp3")

        class _R:
            audio_path = output_path
            srt = ""
            duration = 2.0

        return _R()

    monkeypatch.setattr(ng.tts_mod, "synthesize", flaky)
    monkeypatch.setattr(ng, "_append_silence_chunk", lambda td, ci, vp, chunks: chunks.append(str(td / f"sil_{ci}.mp3")))
    monkeypatch.setattr(ng, "_align_chunk_to_shot", lambda p, dur, td, ci: str(p))

    async def fake_run_ffmpeg(fn, *a, **kw):
        return None

    monkeypatch.setattr(ng, "run_ffmpeg", fake_run_ffmpeg)

    result = await ng.generate_vimax_narration(
        working_dir=working_dir,
        task_dir=task_dir,
        voice_id="zh-CN-YunjianNeural",
        speed=1.0,
        llm=None,
        original_script="剧本",
    )

    assert result is not None, "部分静音应仍交付（3 镜里 2 镜有声）"


@pytest.mark.asyncio
async def test_sound_effect_only_storyboard_is_not_a_failure(tmp_path, monkeypatch, hermetic_tts):
    """整片只有音效标记（本就无台词）→ 不算失败，避免误伤纯音效片。"""
    import json

    working_dir = tmp_path / "work"
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    working_dir.mkdir(parents=True)
    shots = [{"idx": i, "visual_desc": f"画面{i}", "audio_desc": "[音效] 风声"} for i in range(2)]
    (working_dir / "storyboard.json").write_text(json.dumps(shots, ensure_ascii=False), encoding="utf-8")
    (working_dir / "characters.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(ng, "_append_silence_chunk", lambda td, ci, vp, chunks: chunks.append(str(td / f"sil_{ci}.mp3")))

    async def fake_run_ffmpeg(fn, *a, **kw):
        return None

    monkeypatch.setattr(ng, "run_ffmpeg", fake_run_ffmpeg)

    result = await ng.generate_vimax_narration(
        working_dir=working_dir,
        task_dir=task_dir,
        voice_id="zh-CN-YunjianNeural",
        speed=1.0,
        llm=None,
        original_script="剧本",
    )

    assert result is not None, "纯音效片没有台词是正常的，不该被判失败"

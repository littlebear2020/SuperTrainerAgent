"""Task 8b: TTS 降级产出的音频按实际音色 CAS 键落盘（修前按请求音色 → 错误音色永久缓存）。

P2-14：doubao 请求降级 360 preset:146 产出时，音频写在 md5(narration|doubao|speed)
键下——之后所有同文本同 doubao 音色的请求永久命中这份 360 音色的音频。修复：
TTSResult.actual_voice_id 标记降级链末端实际音色，两处 synthesize 调用点
（_synthesize_audio / _regenerate_frame）按实际音色 CAS 键归位。
"""

from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod


def _ts_adapter(fn):
    """T-A6（R6，2026-09-09 feedback-round1）：T10 桩适配——把既有 synthesize_sse
    桩包成 with_timestamps 形态 (bytes, [])。从 test_orchestration_tts.py 复制
    （修前本文件 :81 用而未定义 → NameError 新红）。"""

    async def _wrapped(self, *a, **kw):
        return await fn(self, *a, **kw), []

    return _wrapped


def test_tts_result_has_actual_voice_id():
    r = tts_mod.TTSResult(audio_path="/tmp/a.mp3", srt="", duration=1.0)
    assert getattr(r, "actual_voice_id", None) is None  # 默认 None = 与请求一致


def test_qihu_fallback_marks_actual_voice(tmp_path, monkeypatch):
    """doubao 失败降级 360 → actual_voice_id='preset:146'。"""

    async def fake_qihu(text, voice_id, speed, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0, actual_voice_id="preset:146")

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "")  # 触发 access_key 空 → 直接降级
    out = str(tmp_path / "o.mp3")
    r = asyncio.run(tts_mod._synthesize_doubao("你好", "doubao:speaker1", 1.0, out))
    assert r.actual_voice_id == "preset:146"


@pytest.mark.asyncio
async def test_provider_endpoints_mark_actual_voice(tmp_path, monkeypatch):
    """三末端成功路径各自标记实际音色（谁实际产出谁标记）。"""

    # edge 末端
    class _FakeCommunicate:
        def __init__(self, text, voice, rate="+0%"):
            pass

        async def stream(self):
            yield {"type": "audio", "data": b"AUDIO"}
            yield {"type": "WordBoundary", "offset": 0, "duration": 10_000_000, "text": "你"}

    monkeypatch.setattr(tts_mod.edge_tts, "Communicate", _FakeCommunicate)
    out = str(tmp_path / "e.mp3")
    r = await tts_mod._synthesize_edge("你好", "zh-CN-YunjianNeural", 1.0, out)
    assert r.actual_voice_id == "zh-CN-YunjianNeural"

    # qihu 末端（stub 掉 360 调用 + ffprobe）
    monkeypatch.setenv("QIHU_API_KEY", "k")

    async def fake_360(*, client, text, preset_id, output_path, speed=None, **kwargs):
        Path(output_path).write_bytes(b"QIHU")

    async def fake_run_ffmpeg(func, *args, **kwargs):
        return func(*args)

    monkeypatch.setattr(tts_mod, "_call_360_with_retry", fake_360)
    monkeypatch.setattr(tts_mod, "_get_audio_duration", lambda p: 1.5)
    monkeypatch.setattr(tts_mod, "run_ffmpeg", fake_run_ffmpeg)
    out = str(tmp_path / "q.mp3")
    r = await tts_mod._synthesize_qihu("你好", "preset:146", 1.0, out)
    assert r.actual_voice_id == "preset:146"

    # doubao 末端
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")

    async def fake_doubao_sse(*a, **kw):
        return b"DOUBAO"

    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse", fake_doubao_sse)
    monkeypatch.setattr("app.gateway.voice_twin.doubao_tts_client.DoubaoTTSClient.synthesize_sse_with_timestamps", _ts_adapter(fake_doubao_sse))
    out = str(tmp_path / "d.mp3")
    r = await tts_mod._synthesize_doubao("你好", "doubao:spk1", 1.0, out)
    assert r.actual_voice_id == "doubao:spk1"


# ---------------------------------------------------------------------------
# 调用点归位：降级产出按实际音色 CAS 键落盘
# ---------------------------------------------------------------------------


def _make_bare_service(tmp_path, monkeypatch):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._config = types.SimpleNamespace(qihu_api_key="", ai_image_provider="seedream", tts_fallback_voice="preset:146")
    svc._sb_write_lock = asyncio.Lock()
    return svc


def _patch_ffmpeg_pipeline(monkeypatch):
    """_synthesize_audio / _regenerate_frame 尾部公共 patch：不真跑 ffmpeg/SRT。"""

    async def fake_run_ffmpeg(func, *args, **kwargs):
        out = args[-1]
        Path(out).write_bytes(b"FFMPEG_OUT")
        return None

    monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr("app.gateway.video_gen.service._write_storyboard_srt", lambda sb, path, **kw: Path(path).write_text(""))


@pytest.mark.asyncio
async def test_synthesize_audio_relocates_degraded_voice(tmp_path, monkeypatch):
    """_synthesize_audio：doubao 请求降级 360 产出 → 音频落 preset:146 的 CAS 键，
    请求音色（doubao）键下不留文件（防错误音色永久缓存）。"""
    from app.gateway.video_gen.service import _audio_cache_key
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_bare_service(tmp_path, monkeypatch)
    _patch_ffmpeg_pipeline(monkeypatch)

    async def fake_synth(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"DEGRADED_360")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0, actual_voice_id="preset:146")

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="doubao:spk1", tts_speed=1.0),
        frames=[StoryboardFrame(index=0, narration="你好", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_t1"
    task_dir.mkdir(parents=True)

    await svc._synthesize_audio(sb, task_dir, "doubao:spk1")

    audio_dir = task_dir / "audio"
    requested = audio_dir / f"{_audio_cache_key('你好', 'doubao:spk1', 1.0)}.mp3"
    actual = audio_dir / f"{_audio_cache_key('你好', 'preset:146', 1.0)}.mp3"
    assert actual.exists() and actual.read_bytes() == b"DEGRADED_360", "降级音频须按实际音色 CAS 键落盘"
    assert not requested.exists(), "请求音色键下不得留降级产出（错误音色永久缓存污染源）"
    assert sb.frames[0].audio_path == str(actual)


@pytest.mark.asyncio
async def test_synthesize_audio_keeps_requested_voice_when_not_degraded(tmp_path, monkeypatch):
    """未降级（actual_voice_id 与请求一致或为 None）→ 仍按请求音色 CAS 键，不做归位。"""
    from app.gateway.video_gen.service import _audio_cache_key
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_bare_service(tmp_path, monkeypatch)
    _patch_ffmpeg_pipeline(monkeypatch)

    async def fake_synth(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"DOUBAO_OK")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0, actual_voice_id=voice_id)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="doubao:spk1", tts_speed=1.0),
        frames=[StoryboardFrame(index=0, narration="你好", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_t2"
    task_dir.mkdir(parents=True)

    await svc._synthesize_audio(sb, task_dir, "doubao:spk1")

    requested = task_dir / "audio" / f"{_audio_cache_key('你好', 'doubao:spk1', 1.0)}.mp3"
    assert requested.exists() and requested.read_bytes() == b"DOUBAO_OK"
    assert sb.frames[0].audio_path == str(requested)


@pytest.mark.asyncio
async def test_synthesize_audio_duplicate_narration_degraded_no_race(tmp_path, monkeypatch):
    """重复旁白帧共享同一 out：并发降级归位不得互踩（一帧 rename 后另一帧 adopt）。

    fake 在写盘后 yield 一拍——确定性复现"A 写完让出 → B 写完让出 → A 归位搬走 out →
    B 续跑发现 src 缺失"的交错；修前（先查存在性/无条件 rename）此处 RuntimeError。
    """
    from app.gateway.video_gen.service import _audio_cache_key
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_bare_service(tmp_path, monkeypatch)
    _patch_ffmpeg_pipeline(monkeypatch)

    async def fake_synth(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"DEGRADED_360")
        await asyncio.sleep(0)  # 写盘后让出——制造归位交错窗口
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0, actual_voice_id="preset:146")

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="doubao:spk1", tts_speed=1.0),
        frames=[
            StoryboardFrame(index=0, narration="重复旁白", image_prompt="p0"),
            StoryboardFrame(index=1, narration="重复旁白", image_prompt="p1"),
        ],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_t4"
    task_dir.mkdir(parents=True)

    await svc._synthesize_audio(sb, task_dir, "doubao:spk1")  # 不得 RuntimeError

    actual = task_dir / "audio" / f"{_audio_cache_key('重复旁白', 'preset:146', 1.0)}.mp3"
    assert actual.exists()
    assert sb.frames[0].audio_path == str(actual)
    assert sb.frames[1].audio_path == str(actual)


@pytest.mark.asyncio
async def test_regenerate_frame_relocates_degraded_voice(tmp_path, monkeypatch):
    """_regenerate_frame：同样的归位逻辑（单帧重生路径——另一处 synthesize 调用点）。"""
    from app.gateway.video_gen.service import _audio_cache_key
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_bare_service(tmp_path, monkeypatch)
    _patch_ffmpeg_pipeline(monkeypatch)
    monkeypatch.setattr("app.gateway.video_gen.service._warn_if_image_provider_not_i2i", lambda *a, **k: None)

    class FakeRepo:
        async def get(self, task_id, user_id=None):
            return types.SimpleNamespace(id=task_id, status="ready", task_type="ai_image", payload="{}")

    svc.repo = FakeRepo()

    class FakeVisual:
        async def render(self, storyboard, **kwargs):
            return None

    monkeypatch.setattr(svc, "_make_visual_source", lambda *a, **k: FakeVisual())

    async def fake_synth(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"DEGRADED_360")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=2.0, actual_voice_id="preset:146")

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="doubao:spk1", tts_speed=1.0),
        frames=[StoryboardFrame(index=0, narration="你好", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_t3"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    await svc._regenerate_frame("vg_t3", "u1", 0, task_dir)

    audio_dir = task_dir / "audio"
    requested = audio_dir / f"{_audio_cache_key('你好', 'doubao:spk1', 1.0)}.mp3"
    actual = audio_dir / f"{_audio_cache_key('你好', 'preset:146', 1.0)}.mp3"
    assert actual.exists() and actual.read_bytes() == b"DEGRADED_360"
    assert not requested.exists()
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["audio_path"] == str(actual)

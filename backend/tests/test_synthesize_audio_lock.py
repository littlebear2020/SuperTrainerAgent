"""同任务整段合成（TTS→concat→SRT）必须互斥——防两代合成撕裂（2026-07-21 漂移根因环 2）。"""

import asyncio
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_synth_locks():
    """模块级 _synth_locks 跨测试清理——锁绑定创建时的事件循环，
    pytest-asyncio 每测试新循环，复用旧锁会 RuntimeError。"""
    from app.gateway.video_gen import service as svc_mod

    svc_mod._synth_locks.clear()
    yield
    svc_mod._synth_locks.clear()


async def _setup(monkeypatch):
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod

    state = {"active": 0, "max_active": 0}

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        state["active"] += 1
        state["max_active"] = max(state["max_active"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=3.0)

    async def fake_run_ffmpeg(*a, **k):
        return None

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(svc_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(svc_mod, "_write_storyboard_srt", lambda *a, **k: None)

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()
    return svc, state


def _sb(narration):
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration=narration)],
    )


@pytest.mark.asyncio
async def test_same_task_dir_synthesizes_serialize(tmp_path, monkeypatch):
    """同 task_dir 两次并发合成：任意时刻在途合成 ≤1（整段互斥，后一个等前一个完整结束）。"""
    svc, state = await _setup(monkeypatch)
    await asyncio.gather(
        svc._synthesize_audio(_sb("旁白A"), tmp_path, "preset:146"),
        svc._synthesize_audio(_sb("旁白B"), tmp_path, "preset:147"),
    )
    assert state["max_active"] == 1, f"同任务并发合成交错（整段未互斥）: max_active={state['max_active']}"


@pytest.mark.asyncio
async def test_different_task_dirs_stay_parallel(tmp_path, monkeypatch):
    """不同 task_dir 不互斥（跨任务并行保留）：在途合成可达 2。守护型：修复前后都应通过。"""
    svc, state = await _setup(monkeypatch)
    (tmp_path / "t1").mkdir()
    (tmp_path / "t2").mkdir()
    await asyncio.gather(
        svc._synthesize_audio(_sb("旁白A"), tmp_path / "t1", "preset:146"),
        svc._synthesize_audio(_sb("旁白B"), tmp_path / "t2", "preset:147"),
    )
    assert state["max_active"] == 2, f"不同任务被错误互斥（并行能力丢失）: max_active={state['max_active']}"


def test_concat_audio_is_atomic_on_failure(monkeypatch, tmp_path):
    """ffmpeg 两段式都失败时：旧 output 原样保留、无 tmp 残留（曾直接覆写目标路径）。"""
    import subprocess as sp

    from app.gateway.video_gen import service as svc

    src = tmp_path / "a.mp3"
    src.write_bytes(b"audio")
    out = tmp_path / "audio.mp3"
    out.write_bytes(b"OLD")

    def fake_run(cmd, *a, **k):
        # 模拟 ffmpeg -y 的真实失败形态：启动即截断/创建输出文件并写入部分内容，
        # 随后失败退出——直接写目标路径时旧文件被撕裂（2026-07-22 审计 P1#2）。
        Path(cmd[-1]).write_bytes(b"PARTIAL")
        return sp.CompletedProcess(args=cmd, returncode=1, stderr=b"boom")

    monkeypatch.setattr(sp, "run", fake_run)
    with pytest.raises(RuntimeError, match="concat failed"):
        svc._concat_audio([str(src)], str(out))
    assert out.read_bytes() == b"OLD", "失败不得破坏既有 output"
    assert not list(tmp_path.glob("*.tmp*")), "失败不得残留 tmp 文件"


@pytest.mark.asyncio
async def test_regenerate_frame_holds_synth_lock_during_concat(monkeypatch, tmp_path):
    """_regenerate_frame 的 concat 必须在 _synth_lock_for(task_dir) 持有态下执行。"""
    import json

    from app.gateway.video_gen import service as svc
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="preset:146", tts_speed=1.0),
        frames=[StoryboardFrame(index=0, narration="你好", duration=1.0)],
    )
    (task_dir / "storyboard.json").write_text(json.dumps(storyboard.to_dict()), encoding="utf-8")

    service = svc.VideoGenService.__new__(svc.VideoGenService)

    class _Repo:
        async def get(self, tid, user_id=None):
            return type("T", (), {"status": "ready", "task_type": "ai_image", "payload": "{}"})()

    class _Visual:
        async def render(self, *a, **k):
            return None

    service.repo = _Repo()
    service._storage_dir = str(tmp_path)
    service._config = type("C", (), {"tts_fallback_voice": "preset:146"})()
    service._sb_write_lock = asyncio.Lock()  # __new__ 绕过 __init__，_update_storyboard 需要
    service._make_visual_source = lambda *a, **k: _Visual()
    service._make_on_frame_done = lambda *a, **k: None
    monkeypatch.setattr(svc, "_inject_project_character_context", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_write_storyboard_srt", lambda *a, **k: None)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    lock_seen: list[bool] = []
    real_run_ffmpeg = svc.run_ffmpeg

    async def spy_run_ffmpeg(fn, *args, **kwargs):
        if fn is svc._concat_audio:
            lock_seen.append(svc._synth_lock_for(str(task_dir)).locked())
            return None  # 不真跑 ffmpeg
        return await real_run_ffmpeg(fn, *args, **kwargs)

    monkeypatch.setattr(svc, "run_ffmpeg", spy_run_ffmpeg)

    # 2026-08-02 死锁修复后：锁由**调用方**持有（真实调用者是
    # routers/video_gen.py:_regenerate_frame_bg，它 `async with _synth_lock_guard`
    # 包住整段）。此处复刻真实调用方式 —— 本用例的不变量「concat 必须在锁内执行」
    # 未变（下面仍断言 `_synth_lock_for(...).locked()`），变的只是谁负责持锁。
    #
    # ⚠️ 必须用 `_synth_lock_guard` 而非裸 `_synth_lock_for`：裸锁不在 ContextVar 里
    # 登记持有者，被调方的 guard 会判定「我没持锁」→ 重新真加锁 → 同协程等自己释放
    # → 永久挂起。本用例曾因此挂死 400s+，那是死锁的镜像症状，不是新 bug。
    async with svc._synth_lock_guard(str(task_dir)):
        await service._regenerate_frame("t1", "u1", 0, task_dir)
    assert lock_seen and all(lock_seen), f"concat 未在锁内执行: {lock_seen}"


@pytest.mark.asyncio
async def test_regenerate_frame_self_locks_when_called_unlocked(monkeypatch, tmp_path):
    """无锁直调时 _regenerate_frame 自己取锁——并发保护不因死锁修复而丢失。

    2026-08-02 死锁修复用 `_synth_lock_guard`（ContextVar 按协程登记持有者）：
    本协程已持锁则复用（不重复获取 → 不死锁），未持有则真加锁（撕裂保护仍在）。
    本用例守后一半，与 test_regenerate_frame_holds_synth_lock_during_concat
    （守前一半）互补。

    ⚠️ 这条也是「别人持锁 ≠ 我持锁」的守护：若 guard 退回 `Lock.locked()` 判断，
    并发的 `_synthesize_audio` 持锁会让本路径误判成「已持有」→ 跳过加锁 →
    此处 lock_seen 记录到 False。详见 tests/test_regenerate_frame_deadlock.py。
    """
    import json

    from app.gateway.video_gen import service as svc
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    task_dir = tmp_path / "task_nolock"
    task_dir.mkdir()
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="preset:146", tts_speed=1.0),
        frames=[StoryboardFrame(index=0, narration="你好", duration=1.0)],
    )
    (task_dir / "storyboard.json").write_text(json.dumps(storyboard.to_dict()), encoding="utf-8")

    service = svc.VideoGenService.__new__(svc.VideoGenService)

    class _Repo:
        async def get(self, tid, user_id=None):
            return type("T", (), {"status": "ready", "task_type": "ai_image", "payload": "{}"})()

    class _Visual:
        async def render(self, *a, **k):
            return None

    service.repo = _Repo()
    service._storage_dir = str(tmp_path)
    service._config = type("C", (), {"tts_fallback_voice": "preset:146"})()
    service._sb_write_lock = asyncio.Lock()
    service._make_visual_source = lambda *a, **k: _Visual()
    service._make_on_frame_done = lambda *a, **k: None
    monkeypatch.setattr(svc, "_inject_project_character_context", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)

    service._make_on_frame_done = lambda *a, **k: None
    monkeypatch.setattr(svc, "_write_storyboard_srt", lambda *a, **k: None)

    async def fake_synthesize(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)

    lock_seen: list[bool] = []
    real_run_ffmpeg = svc.run_ffmpeg

    async def spy_run_ffmpeg(fn, *args, **kwargs):
        if fn is svc._concat_audio:
            lock_seen.append(svc._synth_lock_for(str(task_dir)).locked())
            return None
        return await real_run_ffmpeg(fn, *args, **kwargs)

    monkeypatch.setattr(svc, "run_ffmpeg", spy_run_ffmpeg)

    # 不持锁直调——_regenerate_frame 应自行取锁保护 concat
    assert not svc._synth_lock_for(str(task_dir)).locked()
    await service._regenerate_frame("t1", "u1", 0, task_dir)
    assert lock_seen and all(lock_seen), f"无锁直调时未自行加锁保护 concat: {lock_seen}"
    # 退出后必须释放，不能泄漏锁态
    assert not svc._synth_lock_for(str(task_dir)).locked(), "退出后锁未释放"


@pytest.mark.asyncio
async def test_gather_failure_cancels_siblings(monkeypatch, tmp_path):
    """一帧失败 → 兄弟 synth_one 在锁释放前被取消（曾变孤儿继续写 CAS 文件）。"""
    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration import tts as tts_mod
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = svc_mod.VideoGenService.__new__(svc_mod.VideoGenService)
    svc._config = type("C", (), {"qihu_api_key": None, "tts_fallback_voice": "preset:146"})()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    frames = [StoryboardFrame(index=0, narration="炸", duration=1.0), StoryboardFrame(index=1, narration="慢", duration=1.0)]
    storyboard = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920, voice_id="preset:146", tts_speed=1.0),
        frames=frames,
    )

    cancelled: list[str] = []

    async def fake_synth(text, *, voice_id, speed, output_path, fallback_voice=None, **kwargs):
        if text == "炸":
            raise RuntimeError("boom")
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(text)
            raise

    monkeypatch.setattr(tts_mod, "synthesize", fake_synth)

    with pytest.raises(RuntimeError, match="boom"):
        await svc._synthesize_audio(storyboard, task_dir, "preset:146")
    assert cancelled == ["慢"]

"""E13: recompose 须校验 audio.mp3 存在——缺音频的 failed 任务给出明确错误，不传空路径给 worker。"""

import pytest


def _make_svc(storage_dir):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}
    svc._storage_dir = str(storage_dir)

    class _Task:
        status = "failed"
        task_type = "ai_image"
        payload = "{}"
        # 预存修复：recompose_task 读 task.dirty_frames（F5 增量引入），stub 缺该属性
        dirty_frames = []

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Task()

    svc.repo = _Repo()
    return svc


@pytest.mark.asyncio
async def test_recompose_rejects_missing_audio(tmp_path):
    """task_dir 无 audio.mp3（TTS 阶段失败的 failed 任务）→ 明确 ValueError，不传空路径给 worker。"""
    svc = _make_svc(tmp_path)
    task_dir = tmp_path / "u1" / "tasks" / "vg_x"
    task_dir.mkdir(parents=True)  # 无 audio.mp3

    with pytest.raises(ValueError, match="音频|audio"):
        await svc.recompose_task("vg_x", user_id="u1")


@pytest.mark.asyncio
async def test_recompose_proceeds_when_audio_exists(tmp_path):
    """audio.mp3 存在 → 不被拦截（正常 recompose 流程不回归）。"""
    svc = _make_svc(tmp_path)
    task_dir = tmp_path / "u1" / "tasks" / "vg_x"
    task_dir.mkdir(parents=True)
    (task_dir / "audio.mp3").write_bytes(b"au")  # 音频在
    (task_dir / "storyboard.json").write_text('{"title":"t","config":{},"frames":[]}', encoding="utf-8")

    # _wrap_pipeline 会被派发；拦截 create_task 防真跑
    import asyncio

    captured = {}

    async def _fake_wrap(*a, **k):
        captured["called"] = True
        return None

    svc._wrap_pipeline = _fake_wrap

    result = await svc.recompose_task("vg_x", user_id="u1")
    assert result["status"] == "composing"
    # 让 create_task 派发的协程跑完
    await asyncio.sleep(0)


# ── D1: regen_state=error frames must be blocked by recompose/re-render ──
# 附录试做结论：变异验证必须走服务级用例（test_visual_availability.py 的内联
# 复现法删 service 合并代码不会变红）。以下复用 _make_svc stub 模式，
# 直接调真实 recompose_task / re_render_task。


def _make_svc_with_storyboard(storage_dir, frames_data, _task_type="ai_image"):
    """构造带 storyboard.json 的 service stub（用于 regen_state 预检测试）。"""
    import json

    svc = _make_svc(storage_dir)
    task_dir = storage_dir / "u1" / "tasks" / "vg_x"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "audio.mp3").write_bytes(b"au")
    (task_dir / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {}, "frames": frames_data}),
        encoding="utf-8",
    )

    class _Task:
        status = "failed"
        task_type = _task_type
        payload = "{}"
        dirty_frames = []

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Task()

    svc.repo = _Repo()
    return svc


@pytest.mark.asyncio
async def test_recompose_blocks_regen_error_frame_with_live_url(tmp_path, monkeypatch):
    """D1: regen_state=error 但旧 URL 可达（probe mock 活）-> 409/ValueError 且文案含帧号。"""
    import app.gateway.video_gen.visual_availability as va

    async def _alive(url, timeout):
        return True  # 旧 URL 仍可达

    monkeypatch.setattr(va, "_head_ok", _alive)

    frames = [
        {"index": 0, "narration": "a", "visual_url": "https://cdn/0.png", "visual_type": "image", "regen_state": "error", "regen_error": "provider timeout"},
        {"index": 1, "narration": "b", "visual_url": "https://cdn/1.png", "visual_type": "image"},
    ]
    svc = _make_svc_with_storyboard(tmp_path, frames)

    with pytest.raises(ValueError, match="帧 1 "):
        await svc.recompose_task("vg_x", user_id="u1")


@pytest.mark.asyncio
async def test_recompose_passes_when_regen_state_done(tmp_path, monkeypatch):
    """对照组：regen_state=done 且 URL 可达 -> 放行进入 composing。"""
    import asyncio

    import app.gateway.video_gen.visual_availability as va

    async def _alive(url, timeout):
        return True

    monkeypatch.setattr(va, "_head_ok", _alive)

    frames = [
        {"index": 0, "narration": "a", "visual_url": "https://cdn/0.png", "visual_type": "image", "regen_state": "done"},
        {"index": 1, "narration": "b", "visual_url": "https://cdn/1.png", "visual_type": "image"},
    ]
    svc = _make_svc_with_storyboard(tmp_path, frames)

    captured = {}

    async def _fake_wrap(*a, **k):
        captured["called"] = True
        return None

    svc._wrap_pipeline = _fake_wrap
    result = await svc.recompose_task("vg_x", user_id="u1")
    assert result["status"] == "composing"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_re_render_passes_regen_error_frame_in_will_regen(tmp_path, monkeypatch):
    """re_render：regen error 帧在 _will_regen 内（无 visual_url）-> 放行。"""
    import asyncio

    import app.gateway.video_gen.visual_availability as va

    async def _alive(url, timeout):
        return True

    monkeypatch.setattr(va, "_head_ok", _alive)

    # 帧0：无 visual_url + regen_state=error -> _will_regen 会包含它 -> 放行
    # 帧1：有 URL + 类型正确 + regen_state=done -> 健康
    frames = [
        {"index": 0, "narration": "a", "regen_state": "error", "regen_error": "boom"},
        {"index": 1, "narration": "b", "visual_url": "https://cdn/1.png", "visual_type": "image"},
    ]
    svc = _make_svc_with_storyboard(tmp_path, frames, _task_type="ai_image")

    captured = {}

    async def _fake_wrap(*a, **k):
        captured["called"] = True
        return None

    svc._wrap_pipeline = _fake_wrap
    svc._inject_project_character_context = lambda *a, **k: None
    svc._resolve_voice_id = lambda *a, **k: None
    svc._resolve_subtitle_style = lambda *a, **k: None
    svc._update_storyboard = lambda *a, **k: asyncio.sleep(0)

    result = await svc.re_render_task("vg_x", user_id="u1")
    assert result["status"] == "composing"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_re_render_blocks_regen_error_frame_not_in_will_regen(tmp_path, monkeypatch):
    """re_render：regen error 帧有 URL + 类型正确（不在 _will_regen）-> 拦。"""
    import app.gateway.video_gen.visual_availability as va

    async def _alive(url, timeout):
        return True

    monkeypatch.setattr(va, "_head_ok", _alive)

    # 帧0：有 URL + 类型正确 + regen_state=error -> 不在 _will_regen -> 必须拦
    frames = [
        {"index": 0, "narration": "a", "visual_url": "https://cdn/0.png", "visual_type": "image", "regen_state": "error", "regen_error": "timeout"},
        {"index": 1, "narration": "b", "visual_url": "https://cdn/1.png", "visual_type": "image"},
    ]
    svc = _make_svc_with_storyboard(tmp_path, frames, _task_type="ai_image")

    with pytest.raises(ValueError, match="帧 1 "):
        await svc.re_render_task("vg_x", user_id="u1")

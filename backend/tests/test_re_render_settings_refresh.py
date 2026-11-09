"""Task 8a: re-render 时未显式指定的 voice/subtitle 重取项目当前值（修前冻结在草稿创建时刻）。

P2-15：re_render_task 直接把草稿创建时刻的 payload 快照丢给 _wrap_pipeline——
用户改项目音色/字幕样式后 re-render 静默沿用旧配置。修复：payload 未显式指定时
重取项目当前值（显式选择优先），并回写盘面 storyboard.json config（生效链——
_render_from_draft 的 TTS 与 build_worker_payload 读盘面 config，不读 payload）。
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest


def _make_bare_service(tmp_path, monkeypatch, payload: dict):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc._RE_RENDER_ALLOWED_TYPES = ("ai_image", "ai_video")

    class FakeRepo:
        async def get(self, task_id, user_id=None):
            return types.SimpleNamespace(
                id=task_id,
                status="ready",
                task_type="ai_image",
                payload=json.dumps(payload),
            )

    svc.repo = FakeRepo()
    monkeypatch.setattr(svc, "_resolve_voice_id", lambda v, pid, user_id="": "preset:332")
    monkeypatch.setattr(svc, "_resolve_subtitle_style", lambda v, pid, *a, **k: "bold-yellow")
    monkeypatch.setattr("app.gateway.video_gen.service._inject_project_character_context", lambda *a, **k: None)
    return svc


def _capture_pipeline(svc, monkeypatch):
    """观测点：monkeypatch _wrap_pipeline 捕获 re_render 传入的 payload。

    返回 (seen, finish)——finish() await 掉 create_task 调度的 fake_wrap（real
    _register_active_task 的 factory 是 lambda: asyncio.create_task(...)，任务需
    事件循环一拍才真正执行，直接断言会拿到空 seen）。
    """
    seen: list[dict] = []

    async def fake_wrap(task_id, user_id, task_type, payload):
        seen.append(payload)

    created: list[asyncio.Task] = []
    monkeypatch.setattr(svc, "_wrap_pipeline", fake_wrap)
    monkeypatch.setattr(svc, "_register_active_task", lambda tid, factory: (created.append(factory()), True)[1])

    async def finish():
        await asyncio.gather(*created)

    return seen, finish


@pytest.mark.asyncio
async def test_re_render_refreshes_project_voice(tmp_path, monkeypatch):
    """payload voice_id/subtitle_style 为 None（创建时未显式选）→ re-render 重取项目当前值。"""
    svc = _make_bare_service(tmp_path, monkeypatch, {"course_project_id": "cp_dddddddddddd", "voice_id": None, "subtitle_style": None})
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_rr1", "default")
    await finish()

    assert seen and seen[0]["voice_id"] == "preset:332"
    assert seen[0]["subtitle_style"] == "bold-yellow"


@pytest.mark.asyncio
async def test_re_render_treats_default_subtitle_as_unset(tmp_path, monkeypatch):
    """subtitle_style='default' 是 schema 未设值标记（CreateVideoRequest 默认、
    _resolve_subtitle_style 语义）——同样要重取项目值，否则常见路径修复失效。"""
    svc = _make_bare_service(tmp_path, monkeypatch, {"course_project_id": "cp_dddddddddddd", "voice_id": None, "subtitle_style": "default"})
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_rr1b", "default")
    await finish()

    assert seen and seen[0]["subtitle_style"] == "bold-yellow"


@pytest.mark.asyncio
async def test_re_render_keeps_explicit_voice(tmp_path, monkeypatch):
    """显式选择优先：payload 已有具体 voice/subtitle → 不被项目值覆盖。"""
    svc = _make_bare_service(tmp_path, monkeypatch, {"course_project_id": "cp_dddddddddddd", "voice_id": "doubao:spk9", "subtitle_style": "bold"})
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_rr2", "default")
    await finish()

    assert seen and seen[0]["voice_id"] == "doubao:spk9"
    assert seen[0]["subtitle_style"] == "bold"


@pytest.mark.asyncio
async def test_re_render_propagates_settings_to_storyboard_config(tmp_path, monkeypatch):
    """生效链：_render_from_draft 读盘面 storyboard.json 的 config（voice_id/subtitle_style），
    不读 payload——刷新值必须回写盘面，否则 re-render 实际仍用旧音色/旧字幕样式。"""
    svc = _make_bare_service(tmp_path, monkeypatch, {"course_project_id": "cp_dddddddddddd", "voice_id": None, "subtitle_style": None})
    svc._sb_write_lock = asyncio.Lock()

    task_dir = tmp_path / "default" / "tasks" / "vg_rr3"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "ai_image", "width": 1080, "height": 1920, "voice_id": "preset:146", "subtitle_style": "default"},
                "frames": [],
            }
        ),
        encoding="utf-8",
    )
    seen, finish = _capture_pipeline(svc, monkeypatch)

    await svc.re_render_task("vg_rr3", "default")
    await finish()

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["config"]["voice_id"] == "preset:332", "刷新后的音色须回写盘面 config 才会被 TTS/worker payload 消费"
    assert data["config"]["subtitle_style"] == "bold-yellow"

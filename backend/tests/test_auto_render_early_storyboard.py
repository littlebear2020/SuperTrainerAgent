"""Task 5: auto_render 路径在 visual.render 开始前 storyboard.json 已落盘（修前渲染
全部完成才写 → 30min+ 期间 on_frame_done 合并全被丢弃，用户盯 0 张分镜等半小时）。"""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_storyboard_written_before_visual_render(tmp_path, monkeypatch):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)

    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n")],
    )

    async def fake_prep(*a, **k):
        return sb, str(tmp_path / "audio.mp3"), str(tmp_path / "subtitle.srt")

    monkeypatch.setattr(svc, "_prepare_storyboard_and_audio", fake_prep)
    monkeypatch.setattr(svc, "_has_mixed_sources", lambda s: False)

    existed_at_render: list[bool] = []

    class FakeVisual:
        async def render(self, storyboard, **kwargs):
            existed_at_render.append((tmp_path / "storyboard.json").exists())

    monkeypatch.setattr(svc, "_make_visual_source", lambda *a, **k: FakeVisual())
    monkeypatch.setattr(svc, "_make_on_frame_done", lambda *a, **k: None)
    # _warn_if_image_provider_not_i2i 是 service.py 模块级函数（:1547 裸调用）→ patch 模块属性
    monkeypatch.setattr("app.gateway.video_gen.service._warn_if_image_provider_not_i2i", lambda *a, **k: None)
    svc._config = __import__("types").SimpleNamespace(effective_frame_concurrency=lambda n: 1)

    async def _async_none():
        return None

    # probe/decide 在 _prepare_storyboard_video 函数体内 from .worker_probe import ...
    # → patch service 模块属性无效，必须 patch 源模块 worker_probe
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.probe_worker_capabilities", _async_none)
    monkeypatch.setattr("app.gateway.video_gen.worker_probe.decide_pipeline_version", lambda sb, caps: "v1")
    monkeypatch.setattr("app.gateway.video_gen.service.build_worker_payload", lambda *a, **k: {"ok": True})

    async def progress(**kwargs):
        return None

    await svc._prepare_storyboard_video("ai_image", {"mode": "fixed", "script": "n"}, tmp_path, progress)
    assert existed_at_render == [True], "storyboard.json 必须在 visual.render 开始前落盘"
    data = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["narration"] == "n"

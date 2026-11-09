"""Task 9: (a) /create 路径 vimax+generate 在 engine 前回填 script（修前必抛错且白烧 3 次 LLM）；
(b) /ai-visual 返回 410。"""

from __future__ import annotations

import json
import types

import pytest


@pytest.mark.asyncio
async def test_vimax_script_frozen_from_storyboard(tmp_path, monkeypatch):
    from app.gateway.video_gen.engines.base import EngineOutcome
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="vimax", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="第一句"), StoryboardFrame(index=1, narration="第二句")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    svc = VideoGenService.__new__(VideoGenService)
    svc._config = types.SimpleNamespace(engine_arch="multi_engine")
    seen: list[dict] = []

    class FakeEngine:
        async def render(self, ctx):
            seen.append(ctx.request)
            out = ctx.task_dir / "out.mp4"
            out.write_bytes(b"x")
            return EngineOutcome(mode="finished", finished_video_path=str(out))

    class FakeRegistry:
        def get_engine(self, engine_type, ctx):
            return FakeEngine()

    monkeypatch.setattr(svc, "_get_engine_registry", lambda: FakeRegistry())

    async def fake_prep(task_type, payload, task_dir, progress, *, skip_tts=False, user_id="default", task_id=None):
        return sb, str(task_dir / "audio.mp3"), str(task_dir / "subtitle.srt")

    monkeypatch.setattr(svc, "_prepare_storyboard_and_audio", fake_prep)

    class _Progress:
        async def __call__(self, **kwargs):
            return None

        async def set_result(self, **kwargs):
            return None

    payload = {"topic": "一个 topic", "mode": "generate"}
    await svc._run_pipeline_engine("vg_vm1", "default", "vimax", payload, tmp_path, _Progress())
    assert seen[0].get("script") == "第一句\n第二句"


@pytest.mark.asyncio
async def test_ai_visual_returns_410():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.gateway.routers.video_gen import router

    # 用最小 app 挂 router 直测状态码（若仓库已有 router 测试基建，改用之）；
    # 410 端点在 _get_service 之前抛，不依赖 app.state。
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/video/ai-visual", json={"prompt": "x", "duration": 5})
    assert resp.status_code == 410
    assert "已废弃" in resp.json()["detail"]

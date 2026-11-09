"""Task 2（2026-10-20，已拍板）：vimax 渲染复用已确认蓝图并冻结帧旁白。

用户确认的盘面 ``storyboard.json`` 是分镜内容唯一事实源：渲染时用帧旁白重建
``payload.script``，跳过 ``_prepare_storyboard_and_audio``（不重新规划），
不再每次渲染/重试重新拆分镜。盘面无帧/无旁白回退原 prep 路径；idea2video 不变。
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.engines.base import EngineOutcome
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard

USER_ID = "u1"
TASK_ID = "vg_reuse"


def _service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    return VideoGenService(
        repo=AsyncMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed_storyboard(tmp_path, *, narration: tuple[str, str] = ("第一句", "第二句"), voice_id: str | None = None) -> None:
    task_dir = tmp_path / USER_ID / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "刻舟求剑",
                "config": {
                    "video_type": "vimax",
                    "width": 1080,
                    "height": 1920,
                    **({"voice_id": voice_id} if voice_id else {}),
                },
                "frames": [
                    {"index": 0, "narration": narration[0], "duration": 2.0},
                    {"index": 1, "narration": narration[1], "duration": 2.0},
                ],
                "total_duration": 238.06,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class _CapturingEngine:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def render(self, ctx):
        self.requests.append(ctx.request)
        return EngineOutcome(mode="finished", finished_video_path=str(ctx.task_dir / "final.mp4"))


class _Registry:
    def __init__(self, engine) -> None:
        self._engine = engine

    def get_engine(self, _name, _ctx):
        return self._engine


async def _run(svc, tmp_path, payload: dict, engine: _CapturingEngine, progress_calls: list | None = None):
    calls = progress_calls if progress_calls is not None else []

    async def progress(**kw):
        calls.append(kw)

    svc._get_engine_registry = lambda: _Registry(engine)  # type: ignore[method-assign]
    with patch("app.gateway.video_gen.service._finish_engine_outcome", new=AsyncMock()):
        await svc._run_pipeline_engine(
            TASK_ID,
            USER_ID,
            "vimax",
            payload,
            tmp_path / USER_ID / "tasks" / TASK_ID,
            progress,
        )
    return calls


@pytest.mark.asyncio
async def test_vimax_reuse_blueprint_skips_replanning_and_freezes_script(tmp_path):
    svc = _service(tmp_path)
    _seed_storyboard(tmp_path)
    engine = _CapturingEngine()
    payload = {"mode": "fixed", "script": "原始脚本（应被帧旁白覆盖）", "reuse_blueprint": True}

    with patch.object(svc, "_prepare_storyboard_and_audio", new=AsyncMock(side_effect=AssertionError("must not replan"))):
        await _run(svc, tmp_path, payload, engine)

    assert engine.requests[-1]["script"] == "第一句\n第二句"
    # 盘面蓝图未被改写（帧数/旁白仍是用户确认版本）
    on_disk = json.loads((tmp_path / USER_ID / "tasks" / TASK_ID / "storyboard.json").read_text(encoding="utf-8"))
    assert [f["narration"] for f in on_disk["frames"]] == ["第一句", "第二句"]


@pytest.mark.asyncio
async def test_vimax_reuse_blueprint_preserves_edited_narration(tmp_path):
    svc = _service(tmp_path)
    _seed_storyboard(tmp_path, narration=("用户改过的第一句", "用户改过的第二句"))
    engine = _CapturingEngine()
    payload = {"mode": "fixed", "script": "原始脚本", "reuse_blueprint": True}

    with patch.object(svc, "_prepare_storyboard_and_audio", new=AsyncMock(side_effect=AssertionError("must not replan"))):
        await _run(svc, tmp_path, payload, engine)

    assert engine.requests[-1]["script"] == "用户改过的第一句\n用户改过的第二句"


@pytest.mark.asyncio
async def test_vimax_reuse_blueprint_preserves_bgm(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    _seed_storyboard(tmp_path)
    engine = _CapturingEngine()
    payload = {"mode": "fixed", "script": "原始脚本", "reuse_blueprint": True, "bgm": "track-1"}
    bgm_src = tmp_path / "bgm-src.mp3"
    bgm_src.write_bytes(b"fake mp3")
    monkeypatch.setattr("app.gateway.video_gen.service._resolve_bgm_or_warn", lambda *_a, **_kw: str(bgm_src))

    with patch.object(svc, "_prepare_storyboard_and_audio", new=AsyncMock(side_effect=AssertionError("must not replan"))):
        await _run(svc, tmp_path, payload, engine)

    bgm_dest = tmp_path / USER_ID / "tasks" / TASK_ID / "bgm.mp3"
    assert bgm_dest.exists()
    on_disk = json.loads((tmp_path / USER_ID / "tasks" / TASK_ID / "storyboard.json").read_text(encoding="utf-8"))
    assert on_disk["config"]["bgm_path"] == str(bgm_dest)


@pytest.mark.asyncio
async def test_vimax_without_reuse_still_prepares(tmp_path):
    svc = _service(tmp_path)
    _seed_storyboard(tmp_path)
    engine = _CapturingEngine()
    payload = {"mode": "fixed", "script": "原始脚本"}
    prepared = Storyboard.from_dict(json.loads((tmp_path / USER_ID / "tasks" / TASK_ID / "storyboard.json").read_text(encoding="utf-8")))
    prep = AsyncMock(return_value=(prepared, "", ""))

    with patch.object(svc, "_prepare_storyboard_and_audio", new=prep):
        await _run(svc, tmp_path, payload, engine)

    assert prep.await_count == 1
    assert engine.requests[-1]["script"] == "原始脚本"


@pytest.mark.asyncio
async def test_vimax_idea2video_reuse_unchanged(tmp_path):
    svc = _service(tmp_path)
    _seed_storyboard(tmp_path)
    engine = _CapturingEngine()
    payload = {"mode": "generate", "idea": "一个想法", "pipeline": "idea2video", "reuse_blueprint": True}
    prepared = Storyboard.from_dict(json.loads((tmp_path / USER_ID / "tasks" / TASK_ID / "storyboard.json").read_text(encoding="utf-8")))
    prep = AsyncMock(return_value=(prepared, "", ""))

    with patch.object(svc, "_prepare_storyboard_and_audio", new=prep):
        await _run(svc, tmp_path, payload, engine)

    assert prep.await_count == 1, "idea2video 必须继续走原 prep 路径"
    assert engine.requests[-1]["idea"] == "一个想法"


@pytest.mark.asyncio
async def test_direct_auto_render_prep_writes_storyboard_total_duration(tmp_path):
    """W4：POST /create + auto_render=true、无 target/budget、无 reuse 时，
    prep 后必须把 storyboard.total_duration 交给 engine，才能派生预算并拆批。"""
    from app.gateway.video_gen.engines.vimax.storyboard_budget import resolve_vimax_budget

    svc = _service(tmp_path)
    _seed_storyboard(tmp_path)
    engine = _CapturingEngine()
    payload = {"mode": "fixed", "script": "原始脚本", "auto_render": True}
    prepared = Storyboard.from_dict(json.loads((tmp_path / USER_ID / "tasks" / TASK_ID / "storyboard.json").read_text(encoding="utf-8")))
    prep = AsyncMock(return_value=(prepared, "", ""))

    with patch.object(svc, "_prepare_storyboard_and_audio", new=prep):
        await _run(svc, tmp_path, payload, engine)

    assert engine.requests[-1]["total_duration"] == pytest.approx(238.06)
    resolved = resolve_vimax_budget(storyboard_duration=engine.requests[-1]["total_duration"])
    assert resolved is not None and resolved.shot_budget == 30

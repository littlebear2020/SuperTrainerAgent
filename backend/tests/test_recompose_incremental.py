"""P1-3: recompose 把 dirty_frames 传给 worker 做增量重渲（修复前永远全量）。"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _setup_ready_task(tmp_path, dirty):
    task = MagicMock()
    task.status = "ready"
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    task.dirty_frames = dirty
    task_dir = tmp_path / "u1" / "tasks" / "vg_inc"
    task_dir.mkdir(parents=True)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            # 2026-07-29：recompose 新增视觉素材预检——必须给帧真实的本地素材，
            # 否则会被判「素材已失效」拦在渲染前（无素材 recompose 只会产出黑屏片）。
            StoryboardFrame(
                index=i,
                narration=f"旁白{i}",
                duration=2.0,
                local_visual_path=f"visuals/img_{i:04d}.png",
            )
            for i in range(3)
        ],
    )
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    (task_dir / "visuals").mkdir()
    for i in range(3):
        (task_dir / "visuals" / f"img_{i:04d}.png").write_bytes(b"img")
    (task_dir / "audio.mp3").write_bytes(b"audio")
    (task_dir / "subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nx\n\n", encoding="utf-8")
    return task, task_dir


@pytest.mark.asyncio
async def test_recompose_passes_dirty_frames_as_changed(tmp_path):
    task, task_dir = _setup_ready_task(tmp_path, dirty=[0, 2])
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=task)
    svc._wrap_pipeline = AsyncMock(return_value=None)

    result = await svc.recompose_task("vg_inc", "u1")
    assert result["status"] == "composing"
    await asyncio.sleep(0)

    assert svc._wrap_pipeline.await_count == 1
    payload = svc._wrap_pipeline.await_args.args[3]
    assert payload["incremental"] is True  # 修复前无此键
    assert payload["changed_frames"] == [0, 2]


@pytest.mark.asyncio
async def test_recompose_without_dirty_stays_full(tmp_path):
    task, _ = _setup_ready_task(tmp_path, dirty=[])
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=task)
    svc._wrap_pipeline = AsyncMock(return_value=None)

    await svc.recompose_task("vg_inc", "u1")
    await asyncio.sleep(0)
    payload = svc._wrap_pipeline.await_args.args[3]
    assert "incremental" not in payload


@pytest.mark.asyncio
async def test_run_pipeline_recompose_passthrough(tmp_path):
    """_run_pipeline 的 recompose_only 分支把 incremental/changed_frames 透传进 worker payload。"""
    task, task_dir = _setup_ready_task(tmp_path, dirty=[1])
    svc = _make_service(tmp_path)
    svc.repo.get = AsyncMock(return_value=task)
    progress = AsyncMock()

    payload = {
        "recompose_only": True,
        "auto_render": True,
        "incremental": True,
        "changed_frames": [1],
    }
    result = await svc._run_pipeline("vg_inc", "u1", "ai_image", payload, task_dir, progress)
    wp = result.payload
    assert wp["incremental"] is True  # 修复前被 build_worker_payload 丢弃
    assert wp["changed_frames"] == [1]

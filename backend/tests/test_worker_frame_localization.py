"""帧素材本地化：/frames/{n}/visual 优先 serve Worker 下载的本地拷贝（lazy 读 sidecar）。

R2：Worker 写独立 local_sources.json sidecar（唯一写者），handler 直接读 sidecar
serve 本地——Worker 从不碰 storyboard.json，零双写竞态（比 merge-via-callback 更简）。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.responses import FileResponse, RedirectResponse

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


def _make_service(tmp_path: Path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t")
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _task_dir(tmp_path: Path, task_id: str = "t1", user_id: str = "u1") -> Path:
    d = tmp_path / user_id / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.mark.asyncio
async def test_frames_visual_serves_local_source_from_sidecar(tmp_path, monkeypatch):
    """local_sources.json sidecar 有 frame 0 → serve 本地文件，不 redirect 过期 URL。"""
    from app.gateway.routers import video_gen as router_mod

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=MagicMock(id="t1", user_id="u1", domain="video", task_type="ai_image", status="ready"))

    task_dir = _task_dir(tmp_path)
    source_dir = task_dir / "source"
    source_dir.mkdir()
    (source_dir / "shot_000.png").write_bytes(b"LOCAL-FRAME-DATA")

    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {"config": {"video_type": "ai_image"}, "frames": [{"index": 0, "narration": "测试", "visual_url": "https://360.cn/expiring.png"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (task_dir / "local_sources.json").write_text(
        json.dumps({"0": "source/shot_000.png"}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    resp = await router_mod.get_frame_visual(MagicMock(), "t1", 0)

    assert isinstance(resp, FileResponse), "应 serve 本地文件，不 redirect 过期 URL"
    assert Path(resp.path).name == "shot_000.png"


@pytest.mark.asyncio
async def test_frames_visual_falls_back_to_redirect_without_sidecar(tmp_path, monkeypatch):
    """无 sidecar → 回退 redirect 远程 URL（向后兼容）。"""
    from app.gateway.routers import video_gen as router_mod

    svc = _make_service(tmp_path)
    svc.get_task = AsyncMock(return_value=MagicMock(id="t1", user_id="u1", domain="video", task_type="ai_image", status="ready"))

    task_dir = _task_dir(tmp_path)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {"config": {"video_type": "ai_image"}, "frames": [{"index": 0, "narration": "测试", "visual_url": "https://360.cn/expiring.png"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(router_mod, "_get_service", lambda req: svc)
    monkeypatch.setattr(router_mod, "get_effective_user_id", lambda: "u1")

    resp = await router_mod.get_frame_visual(MagicMock(), "t1", 0)

    assert isinstance(resp, RedirectResponse), "无 sidecar 时回退 redirect"

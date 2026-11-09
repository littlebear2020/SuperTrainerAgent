"""帧重生异步化后：TTS/视觉失败不再同步 400——后台写 regen_state=error + regen_error，
前端轮询展示（与 preview_error 同机制）。路由只保留同步校验错误（404/409/422/400）。"""

import json

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _make_service(tmp_path):
    from unittest.mock import AsyncMock, MagicMock

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


@pytest.mark.asyncio
async def test_regen_tts_runtime_error_goes_to_regen_state(tmp_path):
    """doubao/TTS RuntimeError（如 'No readable text'）→ regen_state=error，不冒泡。"""
    from unittest.mock import AsyncMock

    from app.gateway.routers.video_gen import _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_a5"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(side_effect=RuntimeError("doubao TTS failed"))

    await _regenerate_frame_bg(svc, "vg_a5", 0, "u1")  # 不 raise

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["regen_state"] == "error"
    assert "doubao TTS failed" in data["frames"][0]["regen_error"]


@pytest.mark.asyncio
async def test_regen_connection_error_goes_to_regen_state(tmp_path):
    """edge-tts 连接失败（ClientConnectorError 属 OSError）→ regen_state=error，不冒泡。"""
    from unittest.mock import AsyncMock

    from app.gateway.routers.video_gen import _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="旁白", image_prompt="p0")],
    )
    task_dir = tmp_path / "u1" / "tasks" / "vg_a5b"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()
    svc._regenerate_frame = AsyncMock(side_effect=OSError("Cannot connect to speech.platform.bing.com:443"))

    await _regenerate_frame_bg(svc, "vg_a5b", 0, "u1")

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["regen_state"] == "error"
    assert "speech.platform.bing.com" in data["frames"][0]["regen_error"]

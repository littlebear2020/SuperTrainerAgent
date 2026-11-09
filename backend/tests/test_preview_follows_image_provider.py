"""E7（360 通道切换，2026-08-21）：预览图跟随任务级生图模型。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E7：
``_generate_frame_preview`` 由 ``self._providers.ai_image``（config 默认）改为
``providers.make_ai_image(payload.image_provider)``——与正式渲染同源（AC6：
选 seedream-360 的任务预览图确为该模型所出）。未选择时行为同现状（None → 默认）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService


class _StubImageProvider:
    def __init__(self, source: str) -> None:
        self.source = source
        self.calls: list[dict] = []

    async def generate_image(self, prompt: str, **kwargs) -> ImageRef:
        self.calls.append({"prompt": prompt, **kwargs})
        return ImageRef(url=f"data:image/png;base64,{self.source}", width=1024, height=1024, source=self.source)


def _write_storyboard(task_dir: Path) -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "ai_image", "width": 1024, "height": 1024},
                "frames": [{"index": 0, "narration": "旁白", "image_prompt": "画面"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _make_service(tmp_path: Path):
    cfg = VideoGenConfig(qihu_api_key="k")
    providers = ProviderRegistry(cfg)
    repo = MagicMock()
    repo.get = AsyncMock()
    svc = VideoGenService(repo=repo, config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    return svc, providers, repo


async def _run_preview(svc, providers, repo, tmp_path, monkeypatch: pytest.MonkeyPatch, payload: dict) -> tuple[list, Path]:
    made: list[str | None] = []
    orig_make = providers.make_ai_image
    stub = _StubImageProvider("stubbed")

    def _record_make(name):
        made.append(name)
        if name is None:
            return orig_make(name)
        return stub

    providers.make_ai_image = _record_make
    # 未选择路径（None → 默认 provider）同样不得触网：实例级打桩 generate_image。
    default_stub = _StubImageProvider("default-stub")
    providers.ai_image.generate_image = default_stub.generate_image

    task = MagicMock()
    task.payload = json.dumps(payload)
    repo.get.return_value = task
    task_dir = Path(tmp_path) / "u" / "tasks" / "t1"
    _write_storyboard(task_dir)

    localize_calls: list[dict] = []

    async def _fake_localize(url, frame, td, **kwargs):
        localize_calls.append({"url": url, "frame_index": frame.index})
        return None

    # service._generate_frame_preview 在函数体内 lazy import
    # （service.py: ``from .visual_sources.ai_image import _localize_image``），
    # 所以桩必须打在定义模块上；打在 service 模块属性上永远不会生效。
    monkeypatch.setattr("app.gateway.video_gen.visual_sources.ai_image._localize_image", _fake_localize)
    svc._update_storyboard = AsyncMock()
    await svc._generate_frame_preview("t1", 0, user_id="u")
    # 防沉默失效：桩没被调到就说明补丁点又漂了（旧版打 service_mod 即如此）。
    assert len(localize_calls) == 1
    assert localize_calls[0]["frame_index"] == 0
    assert localize_calls[0]["url"].startswith("data:image/png;base64,")
    return made, task_dir


@pytest.mark.asyncio
async def test_preview_uses_task_level_seedream_360(tmp_path, monkeypatch):
    """任务选 seedream-360 → 预览图经 make_ai_image('seedream-360') 构建（AC6）。"""
    svc, providers, repo = _make_service(tmp_path)
    made, _ = await _run_preview(svc, providers, repo, tmp_path, monkeypatch, {"image_provider": "seedream-360"})
    assert made == ["seedream-360"]


@pytest.mark.asyncio
async def test_preview_without_selection_follows_default(tmp_path, monkeypatch):
    """未选择 image_provider → make_ai_image(None) → config 默认（行为同现状）。"""
    svc, providers, repo = _make_service(tmp_path)
    made, _ = await _run_preview(svc, providers, repo, tmp_path, monkeypatch, {})
    assert made == [None]

"""graphic-native-text（2026-08-17）：graphic 帧归并 ai_image 渲染的工厂/分派/M7。

Pillow 渲染器退役后：无独立 graphic 源；mixed 分派归并 ai_image；M7 单帧重生 graphic→ai_image。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.service import VideoGenService, build_worker_payload
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_factory import make_visual_source, make_visual_source_mixed


def _providers_stub():
    return SimpleNamespace(make_ai_image=lambda name=None: None, make_ai_video=lambda name=None: None)


def _sb(frame_specs: list[dict]) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_image", width=640, height=360)
    frames = [StoryboardFrame(index=i, narration=s.get("narration", f"n{i}"), image_prompt=f"p{i}", **{k: v for k, v in s.items() if k not in ("narration", "image_prompt")}) for i, s in enumerate(frame_specs)]
    return Storyboard(title="t", config=cfg, frames=frames)


class TestFactoryAfterRetirement:
    def test_mixed_does_not_register_graphic_source(self):
        mixed = make_visual_source_mixed(_sb([{"visual_source": "ai_image"}, {"visual_source": "graphic"}]), providers=_providers_stub(), config=VideoGenConfig())
        assert "graphic" not in mixed._sources, "渲染器退役：不得存在独立 graphic 源"
        assert "ai_image" in mixed._sources

    def test_all_graphic_storyboard_still_registers_ai_image(self):
        """全 graphic 任务：needed 归并修空源 bug（修前 sources={} 全帧静默跳过）。"""
        mixed = make_visual_source_mixed(_sb([{"visual_source": "graphic"}, {"visual_source": "graphic"}]), providers=_providers_stub(), config=VideoGenConfig())
        assert "ai_image" in mixed._sources

    def test_make_visual_source_graphic_rejected(self):
        """graphic 不再是合法单源类型（M7 已改在 service 层映射 graphic→ai_image）。"""
        with pytest.raises(ValueError):
            make_visual_source("graphic", providers=_providers_stub(), config=VideoGenConfig())


class TestM7MapsGraphicToAiImage:
    @pytest.mark.asyncio
    async def test_regenerate_routes_graphic_frame_to_ai_image_source(self, tmp_path, monkeypatch):
        # 任务类型取 ai_video——若 M7 映射被撤，effective_type 会停在 ai_video，映射不可观测
        sb = _sb([{"visual_source": "graphic", "graphic_spec": {"type": "text_card", "title": "t", "points": ["甲", "乙"]}, "duration": 3.0}])
        (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
        task = SimpleNamespace(id="vg_x", task_type="ai_video", status="ready", payload=json.dumps({"mode": "generate"}), result="{}")
        captured: dict = {}

        def _factory(task_type, **kwargs):  # noqa: ANN003
            captured["task_type"] = task_type
            return SimpleNamespace(render=AsyncMock())

        svc = VideoGenService.__new__(VideoGenService)
        svc._sb_write_lock = __import__("asyncio").Lock()
        svc._storage_dir = str(tmp_path)
        svc._config = VideoGenConfig()
        svc.repo = SimpleNamespace(get=AsyncMock(return_value=task))
        monkeypatch.setattr(svc, "_make_visual_source", _factory)
        monkeypatch.setattr("app.gateway.video_gen.service._inject_project_character_context", lambda *a, **k: None)
        await svc._regenerate_frame("vg_x", "u", 0, tmp_path, mode="visual_only")
        assert captured["task_type"] == "ai_image", "graphic 帧单帧重生按 ai_image（fill 注入文字指令）"


class TestWorkerPayloadStillCarriesFields:
    def test_payload_carries_graphic_frame_fields(self):
        sb = _sb([{"visual_source": "graphic", "local_visual_path": "visuals/img_0000.png", "visual_type": "image", "graphic_spec": {"type": "big_number", "number": "37"}, "duration": 4.0}])
        payload = build_worker_payload(sb, subtitle_path=None, audio_concat_path="audio.mp3")
        frame = payload["frames"][0]
        assert frame["visual_source"] == "graphic"
        assert frame["graphic_spec"]["number"] == "37", "字段随 payload 持久化（worker 忽略未知键）"


class TestUnregisteredSourceStillSkipped:
    @pytest.mark.asyncio
    async def test_unknown_source_skips_without_raise(self, tmp_path):
        from app.gateway.video_gen.visual_sources.mixed import MixedVisualSource

        sb = _sb([{"visual_source": "hologram"}])
        mixed = MixedVisualSource({"ai_image": SimpleNamespace(render=AsyncMock())})
        await mixed.render(sb, task_dir=tmp_path)  # 不抛 = 存量行为不回归

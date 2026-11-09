"""T3（2026-09-01 图文镜比例）：任务级占比选项——schema 校验 + 持久化往返。

上限≠保障（用户拍板）：选项只是 graphic 占比上限约束（不超过该值），实际占比由
调度表按内容规划——护栏无下限，内容没要点时选 70% 也不会硬凑。护栏优先级与
规则正交性见 test_visual_routing_guard.TestRatioOverride。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.gateway.video_gen.schemas import CreateVideoRequest
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class TestSchemaGraphicRatio:
    @pytest.mark.parametrize("val", [30, 40, 50, 70])
    def test_valid_values_accepted(self, val: int):
        req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x", graphic_ratio=val)
        assert req.graphic_ratio == val

    def test_33_rejected_by_literal(self):
        """非白名单值 422 在 schema 层拒（Literal 白名单 = 前端下拉选项集）。"""
        with pytest.raises(ValidationError):
            CreateVideoRequest(video_type="ai_image", mode="generate", topic="x", graphic_ratio=33)

    def test_explicit_none_allowed(self):
        """显式 null = 未指定（_strip_explicit_nulls 剥除）——不 422。"""
        req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x", graphic_ratio=None)
        assert req.graphic_ratio is None

    def test_default_is_none(self):
        """不传 = None（默认 30% 由护栏缺省承担，存量任务行为不变）。"""
        req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x")
        assert req.graphic_ratio is None

    def test_model_dump_carries_value(self):
        """值进 payload（service 两处 StoryboardConfig 组装读 payload.get）。"""
        req = CreateVideoRequest(video_type="ai_video", mode="generate", topic="x", graphic_ratio=50)
        assert req.model_dump()["graphic_ratio"] == 50


class TestConfigPersistence:
    def _sb(self, graphic_ratio: int | None) -> Storyboard:
        cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080, graphic_ratio=graphic_ratio)
        frames = [StoryboardFrame(index=0, narration="n0")]
        return Storyboard(title="t", config=cfg, frames=frames)

    def test_roundtrip_preserves_value(self):
        """to_dict → from_dict 往返：graphic_ratio 随 asdict/白名单自动持久化。"""
        sb = self._sb(50)
        restored = Storyboard.from_dict(sb.to_dict())
        assert restored.config.graphic_ratio == 50

    def test_none_roundtrips_as_none(self):
        sb = self._sb(None)
        restored = Storyboard.from_dict(sb.to_dict())
        assert restored.config.graphic_ratio is None

    def test_legacy_dict_without_key_defaults_none(self):
        """存量 storyboard.json 无该键 → from_dict 默认 None（默认 30%，行为不变）。"""
        sb = self._sb(None)
        d = sb.to_dict()
        d["config"].pop("graphic_ratio", None)
        restored = Storyboard.from_dict(d)
        assert restored.config.graphic_ratio is None

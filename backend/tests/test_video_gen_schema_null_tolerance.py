"""CreateVideoRequest 显式 null 容忍（2026-08-31，R4 类推广）。

pydantic v2 对非 Optional 标量默认字段的显式 null 会 422；前端约定「不指定不下发
该字段」，但 spread 状态对象的调用方可能带出 null——mode='before' 剥除后一律走
默认值。同时钉住「剥 null 不等于放松类型」：垃圾值仍须 422。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.gateway.video_gen.schemas import CreateVideoRequest


def _valid_payload() -> dict:
    return {"video_type": "ai_image", "mode": "generate", "topic": "测试主题"}


class TestExplicitNullStripping:
    def test_bool_defaults_survive_null(self):
        """bgm_ducking/auto_render/ai_normalize_script 显式 null → 走默认 True。"""
        body = _valid_payload() | {"bgm_ducking": None, "auto_render": None, "ai_normalize_script": None}
        req = CreateVideoRequest(**body)
        assert req.bgm_ducking is True
        assert req.auto_render is True
        assert req.ai_normalize_script is True

    def test_float_defaults_survive_null(self):
        """transition_duration/bgm_volume 显式 null → 走默认 0.5/0.15。"""
        body = _valid_payload() | {"transition_duration": None, "bgm_volume": None}
        req = CreateVideoRequest(**body)
        assert req.transition_duration == 0.5
        assert req.bgm_volume == 0.15

    def test_optional_null_still_fine(self):
        """Optional 字段 null 本来就合法——剥除不改变结果。"""
        body = _valid_payload() | {"voice_id": None, "bgm": None, "script_model": None}
        req = CreateVideoRequest(**body)
        assert req.voice_id is None
        assert req.bgm is None

    def test_junk_still_422(self):
        """剥 null 不是放松类型：非法 bool/float 仍须 422（防把修复做成大洞）。"""
        with pytest.raises(ValidationError):
            CreateVideoRequest(**(_valid_payload() | {"bgm_ducking": "banana"}))
        with pytest.raises(ValidationError):
            CreateVideoRequest(**(_valid_payload() | {"bgm_volume": "louder"}))

    def test_explicit_false_not_touched(self):
        """显式 False 是有语义的合法值，不得被误剥/翻转。"""
        req = CreateVideoRequest(**(_valid_payload() | {"auto_render": False, "bgm_ducking": False}))
        assert req.auto_render is False
        assert req.bgm_ducking is False

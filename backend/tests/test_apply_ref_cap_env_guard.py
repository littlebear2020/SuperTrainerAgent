"""B2（2026-08-27 评审修复）：apply_ref_cap 的 VIDEO_GEN_REF_CAP_WORKING 守护。

实验位 env 被设为非数字时不得抛 ValueError 打断渲染主链路（4 个调用点：
ai_image 任务级/逐帧、ai_video 逐帧、单帧重生端点）——与 ai_image.py 的
VIDEO_GEN_MULTI_CHAR_VIEWS 守护同款（ValueError → 默认值）。
"""

from __future__ import annotations

import pytest


class TestApplyRefCapEnvGuard:
    def test_garbage_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.gateway.video_gen.service import apply_ref_cap

        monkeypatch.setenv("VIDEO_GEN_REF_CAP_WORKING", "not-a-number")
        refs = [f"r{i}" for i in range(10)]
        keep, record = apply_ref_cap(refs)
        assert keep == refs[:6], "非数字 env 必须回落默认工作点 6，不得抛错"
        assert record["truncated"] == refs[6:]

    def test_valid_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.gateway.video_gen.service import apply_ref_cap

        monkeypatch.setenv("VIDEO_GEN_REF_CAP_WORKING", "3")
        keep, record = apply_ref_cap([f"r{i}" for i in range(10)])
        assert keep == ["r0", "r1", "r2"]
        assert len(record["truncated"]) == 7

    def test_unset_env_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.gateway.video_gen.service import apply_ref_cap

        monkeypatch.delenv("VIDEO_GEN_REF_CAP_WORKING", raising=False)
        keep, _ = apply_ref_cap([f"r{i}" for i in range(10)])
        assert len(keep) == 6

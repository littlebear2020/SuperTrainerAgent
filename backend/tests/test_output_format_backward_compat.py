"""输出格式向后兼容护栏（Task 3，2026-08-01 反馈 6）。

UI 已移除 WebM 选项，但**后端 schema 与 worker 实现必须保留** ——
存量草稿 payload 里带 `output_format="webm"` 的任务若被 422 拒绝，
用户视角就是「我的草稿突然打不开」。

本文件是防止「顺手清理死代码」的护栏：将来有人看到前端不再产生 webm，
可能会把 schema 的 Literal 成员一并删掉，那会破坏存量数据。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.gateway.video_gen.schemas import CreateVideoRequest


def _base_payload(**over):
    p = {"video_type": "ai_image", "topic": "测试主题"}
    p.update(over)
    return p


def test_webm_still_accepted_for_legacy_drafts():
    """存量草稿的 webm 必须仍能通过校验（**不得** 422）。"""
    req = CreateVideoRequest(**_base_payload(output_format="webm"))
    assert req.output_format == "webm"


def test_current_ui_formats_accepted():
    """当前 UI 暴露的两个值正常。"""
    for fmt in ("mp4", "gif"):
        req = CreateVideoRequest(**_base_payload(output_format=fmt))
        assert req.output_format == fmt


def test_output_format_defaults_to_mp4():
    req = CreateVideoRequest(**_base_payload())
    assert req.output_format == "mp4"


def test_unknown_format_still_rejected():
    """兼容不等于放开：未知值仍须拒绝（避免拼错静默落库）。"""
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(output_format="avi"))

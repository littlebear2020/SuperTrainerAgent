"""Task 5（2026-10-20）：创建时自定义标题 + 标题优先级。

优先级（已拍板）：payload.title > result.storyboard.title > payload.topic
> payload.script[:30]。空串/纯空白视为未提供；>80 字 schema 422。
"""

import json
import types

import pytest
from pydantic import ValidationError

from app.gateway.routers.video_gen import _task_with_thumbnail
from app.gateway.video_gen.schemas import CreateVideoRequest


def _task(payload: dict, result: dict | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="vg_title",
        user_id="u1",
        domain="video_gen",
        task_type="vimax",
        status="draft",
        progress=40,
        retry_count=0,
        result=json.dumps(result or {}, ensure_ascii=False),
        payload=json.dumps(payload, ensure_ascii=False),
    )


@pytest.mark.parametrize(
    "payload,result,expected",
    [
        ({"title": "[AUTO-TEST] 刻舟求剑", "topic": "话题", "script": "脚本"}, {"storyboard": {"title": "分镜标题"}}, "[AUTO-TEST] 刻舟求剑"),
        ({"topic": "话题", "script": "脚本"}, {"storyboard": {"title": "分镜标题"}}, "分镜标题"),
        ({"topic": "话题", "script": "脚本"}, {}, "话题"),
        ({"script": "第一句脚本内容超过三十字吗一二三四五六七八九十"}, {}, "第一句脚本内容超过三十字吗一二三四五六七八九十"[:30]),
    ],
)
def test_task_title_priority(tmp_path, payload, result, expected):
    brief = _task_with_thumbnail(_task(payload, result), storage_dir=str(tmp_path), user_id="u1")
    assert brief.title == expected


def test_blank_title_falls_through(tmp_path):
    brief = _task_with_thumbnail(
        _task({"title": "   ", "topic": "话题", "script": "脚本"}, {"storyboard": {"title": "分镜标题"}}),
        storage_dir=str(tmp_path),
        user_id="u1",
    )
    assert brief.title == "分镜标题"


def test_schema_normalizes_title_and_rejects_too_long():
    req = CreateVideoRequest(video_type="vimax", mode="fixed", script="x", title="   ")
    assert req.title is None
    assert CreateVideoRequest(video_type="vimax", mode="fixed", script="x", title="A" * 80).title == "A" * 80
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="vimax", mode="fixed", script="x", title="A" * 81)

"""2026-09-01 计费修复：端点直建后台任务的记账上下文覆盖。

修前 ``set_llm_accounting_context`` 唯一设置点在 ``_wrap_pipeline`` 管线入口
（task_framework/service.py:841）；``_preview_frame_bg``/``_preview_all_bg``/
``_regenerate_frame_bg`` 是端点 ``create_task`` 直建的后台任务，不经管线 →
``record_external_api_call`` 读 ctx 得 None → ``task_id IS NULL`` 无主行
（本地 SQL 实证：qihu/generate_image 290 + gpt-image-2 85 行无主）。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.gateway._shared.llm_usage_sink import get_llm_accounting_context
from app.gateway.routers.video_gen import _preview_all_bg, _preview_frame_bg, _regenerate_frame_bg


@pytest.mark.asyncio
async def test_preview_frame_bg_sets_accounting_context():
    """单帧预览后台任务：ctx 在下游生图调用内可读，登记清理。"""
    seen = {}

    class _Svc:
        _storage_dir = ""
        # 2026-09-07 preview_state 状态机：_preview_frame_bg 开始即写 running（同文件
        # regen 用例 stub _set_regen_state 的同款模式）。
        _set_preview_state = AsyncMock()

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            ctx = get_llm_accounting_context()
            seen["ctx"] = (ctx.task_id, ctx.user_id, ctx.domain) if ctx else None

    from app.gateway.routers.video_gen import _preview_inflight

    await _preview_frame_bg(_Svc(), "task_x", 3, "user_1")
    assert seen["ctx"] == ("task_x", "user_1", "video_gen"), "预览生图必须归账到任务（修前 task_id=None）"
    assert ("task_x", 3) not in _preview_inflight


@pytest.mark.asyncio
async def test_preview_all_bg_sets_accounting_context(tmp_path):
    """批量预览后台任务：帧级生图调用内 ctx 可读（每帧归账）。"""
    seen = {}
    task_dir = tmp_path / "user_1" / "tasks" / "task_y"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"frames": [{"index": 0, "narration": "x", "image_prompt": "p"}]}),
        encoding="utf-8",
    )

    class _Svc:
        _storage_dir = str(tmp_path)

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            ctx = get_llm_accounting_context()
            seen["ctx"] = (ctx.task_id, ctx.user_id, ctx.domain) if ctx else None

    await _preview_all_bg(_Svc(), "task_y", [0], "user_1")
    assert seen["ctx"] == ("task_y", "user_1", "video_gen"), "批量预览的逐帧生图必须归账到任务"


@pytest.mark.asyncio
async def test_regenerate_frame_bg_sets_accounting_context(tmp_path):
    """单帧重生后台任务：ctx 在 _regenerate_frame 内可读（生图/生视频/TTS 归账）。"""
    seen = {}
    task_dir = tmp_path / "user_1" / "tasks" / "task_z"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"frames": [{"index": 1, "narration": "hello"}]}),
        encoding="utf-8",
    )

    class _Svc:
        _storage_dir = str(tmp_path)
        repo = SimpleNamespace(add_dirty_frame=AsyncMock())
        _set_regen_state = AsyncMock()
        _update_storyboard = AsyncMock()

        async def _regenerate_frame(self, task_id, user_id, frame_index, task_dir, mode="all"):
            ctx = get_llm_accounting_context()
            seen["ctx"] = (ctx.task_id, ctx.user_id, ctx.domain) if ctx else None

    await _regenerate_frame_bg(_Svc(), "task_z", 1, "user_1", "all")
    assert seen["ctx"] == ("task_z", "user_1", "video_gen"), "重生帧的付费调用必须归账到任务"

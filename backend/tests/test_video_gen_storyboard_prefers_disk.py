"""A3: GET storyboard 须优先读磁盘（含 visual_url），而非 task.result 旧快照（无 visual_url）。

根因（核实）：task_framework/service.py:370-375 在 draft 期把 storyboard（当时无 visual_url）
快照进 task.result；_generate_frame_preview 只把 visual_url 写磁盘 storyboard.json；
GET 先读 task.result（truthy 旧快照）→ 磁盘回落永不触发 → 前端拿不到 visual_url → 预览不显示。
"""

import json

import pytest


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


@pytest.mark.asyncio
async def test_get_storyboard_prefers_disk_visual_url(tmp_path):
    """磁盘有 visual_url、task.result 是无 visual_url 的旧快照 → GET 须返回磁盘版本。"""
    from app.gateway.routers.video_gen import get_storyboard

    # autouse user = "test-user-autouse"；磁盘 storyboard 放在该用户目录
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_a3"
    task_dir.mkdir(parents=True)
    disk_sb = {
        "title": "t",
        "config": {},
        "frames": [{"index": 0, "narration": "hi", "visual_url": "https://cdn/x.png", "visual_type": "image"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(disk_sb), encoding="utf-8")

    # task.result = draft 期旧快照（无 visual_url）—— 这是 _wrap_pipeline 写进去的
    class _Task:
        status = "draft"
        progress = 0  # M3-3a: 端点暴露 status/progress 给前端（合成中 N%）
        result = json.dumps({"storyboard": {"title": "t", "config": {}, "frames": [{"index": 0, "narration": "hi"}]}})
        payload = "{}"

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

    sb = await get_storyboard(_make_request(_Svc()), "vg_a3")
    frames = sb["frames"] if isinstance(sb, dict) else {}
    assert frames[0].get("visual_url") == "https://cdn/x.png", f"GET 须返回磁盘的 visual_url（修前返回 task.result 旧快照无 visual_url）: {frames[0]}"


@pytest.mark.asyncio
async def test_get_storyboard_falls_back_to_result_when_no_disk(tmp_path):
    """磁盘不存在（草稿未落盘）→ 回落 task.result（不回归）。"""
    from app.gateway.routers.video_gen import get_storyboard

    class _Task:
        status = "ready"
        progress = 100  # M3-3a: 同上
        result = json.dumps({"storyboard": {"title": "t", "config": {}, "frames": [{"index": 0, "narration": "hi"}]}})
        payload = "{}"

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

    sb = await get_storyboard(_make_request(_Svc()), "vg_nodisk")
    assert sb["frames"][0]["narration"] == "hi"

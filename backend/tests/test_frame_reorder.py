"""H2: failed 态禁止 reorder——reorder 不重 concat audio.mp3/不重写 subtitle.srt，
recompose 复用旧序音频 → 视频新帧序 + 音频旧帧序 = 音画错位。
与增删帧 409（video_gen.py add/delete 端点注释）同一设计理由。"""

import json

import pytest
from fastapi import HTTPException

from app.gateway.routers.video_gen import ReorderRequest, reorder_frames


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _svc(tmp_path, status):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_ro"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [{"index": 0, "narration": "a"}, {"index": 1, "narration": "b"}],
            }
        )
    )

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            class T:
                pass

            T.status = status
            return T()

    return _Svc()


@pytest.mark.asyncio
async def test_reorder_failed_status_rejected(tmp_path):
    """failed 任务 reorder → 409（修复前放行 → 音画错位）。"""
    svc = _svc(tmp_path, "failed")
    with pytest.raises(HTTPException) as exc:
        await reorder_frames(_make_request(svc), "vg_ro", ReorderRequest(from_index=0, to_index=2))
    assert exc.value.status_code == 409
    assert "草稿" in exc.value.detail


@pytest.mark.asyncio
async def test_reorder_remaps_planner_scene_groups(tmp_path):
    """T6（2026-08-18）schema 消费链回归：reorder 的 _adjust_index 重映射对 planner
    产出的 scene_groups 同样成立（T6 起普通任务 storyboard.json 非空 groups 成为
    常态）——帧 0 移到末尾后各组 frame_indices 跟随帧身位、逐组保序。

    数据：3 帧各成组 [[0],[1],[2]]（T-S2 单帧组归并后允许中段存在 1 帧组的极端
    边界由归并逻辑保证，这里只锁 reorder 数学）。to_index=2 语义（pop(0)+insert(1)）
    → 帧序 [b,a,c]：组A(帧0)→[1]、组B(帧1)→[0]、组C(帧2)→[2]。
    """
    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_ro2"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [{"index": 0, "narration": "a"}, {"index": 1, "narration": "b"}, {"index": 2, "narration": "c"}],
                "scene_groups": [
                    {"index": 0, "title": "组A", "frame_indices": [0], "origin": "planner"},
                    {"index": 1, "title": "组B", "frame_indices": [1], "origin": "planner"},
                    {"index": 2, "title": "组C", "frame_indices": [2], "origin": "planner"},
                ],
            }
        )
    )

    class _Repo:
        async def clear_dirty_frames(self, task_id):
            pass

    class _Svc:
        _storage_dir = str(tmp_path)
        repo = _Repo()

        async def get_task(self, task_id, user_id):
            class T:
                status = "draft"

            return T()

        async def _update_storyboard(self, td, updater):
            data = json.loads((td / "storyboard.json").read_text(encoding="utf-8"))
            updater(data)
            (td / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        async def touch_task(self, task_id):
            pass

    resp = await reorder_frames(_make_request(_Svc()), "vg_ro2", ReorderRequest(from_index=0, to_index=2))
    groups = resp["storyboard"]["scene_groups"]
    assert [g["frame_indices"] for g in groups] == [[1], [0], [2]], "各组 frame_indices 须随帧身位重映射"
    assert [f["index"] for f in resp["storyboard"]["frames"]] == [0, 1, 2], "帧重编号为全局连续序"
    assert [f["narration"] for f in resp["storyboard"]["frames"]] == ["b", "a", "c"], "帧序已按 reorder 变更"
    assert all(g.get("origin") == "planner" for g in groups), "origin 随 groups 落盘不丢（worker v3 栅栏判据）"

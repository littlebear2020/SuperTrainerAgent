# backend/tests/test_scene_group_frame_sync.py
from app.gateway.video_gen.storyboard import (
    remap_scene_groups_after_delete,
    remap_scene_groups_after_insert,
)


def test_insert_at_end_joins_last_group():
    groups = [
        {"index": 0, "title": "导语", "frame_indices": [0, 1]},
        {"index": 1, "title": "问题一", "frame_indices": [2, 3]},
    ]
    out = remap_scene_groups_after_insert(groups, 4)
    assert out[0]["frame_indices"] == [0, 1]
    assert out[1]["frame_indices"] == [2, 3, 4]


def test_insert_in_middle_shifts_and_joins_host_group():
    groups = [{"index": 0, "title": "导语", "frame_indices": [0, 1, 2]}]
    out = remap_scene_groups_after_insert(groups, 1)
    assert out[0]["frame_indices"] == [0, 1, 2, 3]


def test_insert_without_groups_is_noop():
    assert remap_scene_groups_after_insert([], 0) == []


def test_delete_removes_shifts_and_drops_empty_group():
    groups = [
        {"index": 0, "title": "A", "frame_indices": [0]},
        {"index": 1, "title": "B", "frame_indices": [1, 2]},
    ]
    out = remap_scene_groups_after_delete(groups, 0)
    assert out == [{"index": 0, "title": "B", "frame_indices": [0, 1]}]


def test_delete_keeps_group_when_not_empty():
    groups = [{"index": 0, "title": "A", "frame_indices": [0, 1]}]
    out = remap_scene_groups_after_delete(groups, 0)
    assert out == [{"index": 0, "title": "A", "frame_indices": [0]}]


def test_insert_between_groups_joins_preceding_group():
    """「在 N 镜后插入」→ 并入 N 的组：N 是组末镜时不再漏进下一组。

    分镜边界插入（在 index1 后，即组A末镜后）——新帧应留在组A，
    而不是旧规则的组B。
    """
    groups = [
        {"index": 0, "title": "导语", "frame_indices": [0, 1]},
        {"index": 1, "title": "问题一", "frame_indices": [2, 3]},
    ]
    out = remap_scene_groups_after_insert(groups, 2)
    assert out[0]["frame_indices"] == [0, 1, 2]
    assert out[1]["frame_indices"] == [3, 4]


def test_insert_at_head_joins_original_first_group():
    """插到最前（index 0，无前一镜）→ 并入原第一镜（位移后 index 1）所在组。"""
    groups = [
        {"index": 0, "title": "导语", "frame_indices": [0, 1]},
        {"index": 1, "title": "问题一", "frame_indices": [2, 3]},
    ]
    out = remap_scene_groups_after_insert(groups, 0)
    assert out[0]["frame_indices"] == [0, 1, 2]
    assert out[1]["frame_indices"] == [3, 4]


def test_insert_after_ungrouped_frame_falls_to_last_group():
    """前一镜不在任何组（历史/异常数据）→ 兜底并入最后一组。

    已知边界：兜底路径产生非连续 frame_indices（[3,5,6] 跨过无组帧 4）——
    前端 deriveDisplayGroups 按 indices 映射渲染不受影响（帧 4 落「未分组」桶）。
    """
    groups = [
        {"index": 0, "title": "A", "frame_indices": [0, 1]},
        {"index": 1, "title": "B", "frame_indices": [4, 5]},
    ]
    out = remap_scene_groups_after_insert(groups, 3)
    assert out[0]["frame_indices"] == [0, 1]
    assert out[1]["frame_indices"] == [3, 5, 6]

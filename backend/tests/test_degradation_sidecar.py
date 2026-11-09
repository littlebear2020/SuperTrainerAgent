"""Task 23：静默降级一律留痕（C11 / C13 / C6）。

三条降级此前**只写 logger、不告诉用户**：
- C11 `video_gen_ai_visual.py`：BGM 混音失败 → 裸 `except` + warning。用户明确
  选了背景音乐，成片没有，界面零说明。
- C13 `narration_generator.py`：逐镜对齐失败 → `logger.exception` 后返回**未对齐**
  的原 chunk。该 chunk 时长≠镜头时长 → 音画字幕三方从这镜起累积错位。
- C6 同文件：`ratio > 1.3` 时 atempo 只能到 1.3，其余超出用 `-t` 硬切 → 台词
  没说完就切镜。已有 warning（比 C11/C13 好），但用户侧仍不可见。

本测试锁「降级必须落到 sidecar」这条不变量。sidecar 与 Task 10 的
``placeholder_frames.json`` 并列，字段 ``{kind, frame_index?, detail}``。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.gateway.video_gen.degradations import (
    DEGRADATION_SIDECAR_NAME,
    read_degradations,
    record_degradation,
)


def test_record_then_read_roundtrip(tmp_path: Path) -> None:
    record_degradation(tmp_path, kind="bgm_failed", detail="ffmpeg amix exit 1")

    items = read_degradations(tmp_path)
    assert len(items) == 1
    assert items[0]["kind"] == "bgm_failed"
    assert "amix" in items[0]["detail"]


def test_appends_and_preserves_frame_index(tmp_path: Path) -> None:
    """多条降级须累积，且 frame_index 可选字段要留住。"""
    record_degradation(tmp_path, kind="align_failed", detail="shot 3", frame_index=3)
    record_degradation(tmp_path, kind="dialogue_truncated", detail="overflow 1.8s", frame_index=7)

    items = read_degradations(tmp_path)
    assert [i["kind"] for i in items] == ["align_failed", "dialogue_truncated"]
    assert [i.get("frame_index") for i in items] == [3, 7]


def test_read_missing_returns_empty(tmp_path: Path) -> None:
    assert read_degradations(tmp_path) == []


def test_corrupt_sidecar_does_not_raise(tmp_path: Path) -> None:
    """坏 JSON 不能把渲染或详情接口打挂——降级留痕本身不该成为新故障源。"""
    (tmp_path / DEGRADATION_SIDECAR_NAME).write_text("{not json", encoding="utf-8")
    assert read_degradations(tmp_path) == []

    # 坏文件之后仍应能继续记录
    record_degradation(tmp_path, kind="bgm_failed", detail="after corruption")
    assert [i["kind"] for i in read_degradations(tmp_path)] == ["bgm_failed"]


def test_record_never_raises_on_unwritable_dir(tmp_path: Path) -> None:
    """目录不存在 → 吞掉，不能冒泡打断主渲染流程。"""
    record_degradation(tmp_path / "nope" / "deeper", kind="bgm_failed", detail="x")


def test_dedupes_identical_entries(tmp_path: Path) -> None:
    """同一 kind+frame 重复记录（重试路径）不该刷屏。"""
    for _ in range(3):
        record_degradation(tmp_path, kind="align_failed", detail="shot 3", frame_index=3)

    assert len(read_degradations(tmp_path)) == 1


def test_sidecar_is_valid_json_object_list(tmp_path: Path) -> None:
    """落盘格式必须是 {"items": [...]}，gateway 侧按此读取。"""
    record_degradation(tmp_path, kind="bgm_failed", detail="d")
    raw = json.loads((tmp_path / DEGRADATION_SIDECAR_NAME).read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert isinstance(raw["items"], list)

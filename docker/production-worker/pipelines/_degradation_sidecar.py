"""降级留痕 sidecar（worker 侧副本，Task 23 / C11）。

**为什么是副本**：worker 跑在独立镜像里，不能 import ``app.*``（harness 边界，
`tests/test_harness_boundary.py` 在 CI 里守着）。gateway 侧的同名实现在
``backend/app/gateway/video_gen/degradations.py``——两边**落盘格式必须逐字一致**，
否则 gateway 静默读不到 worker 写的记录，sidecar 等于白写。
``tests/test_bgm_failure_is_recorded.py::test_worker_and_gateway_formats_match``
钉住这条契约。

改这个文件时，同步改 gateway 那份。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEGRADATION_SIDECAR_NAME = "degradations.json"

KIND_BGM_FAILED = "bgm_failed"


def read_degradations(task_dir: Path | str) -> list[dict[str, Any]]:
    """读出降级记录；缺失或损坏一律返回空表（绝不抛）。"""
    path = Path(task_dir) / DEGRADATION_SIDECAR_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("kind")]


def record_degradation(
    task_dir: Path | str,
    *,
    kind: str,
    detail: str,
    frame_index: int | None = None,
) -> None:
    """追加一条降级记录（同 kind+frame_index 幂等）；绝不抛异常。"""
    try:
        path = Path(task_dir) / DEGRADATION_SIDECAR_NAME
        items = read_degradations(path.parent)
        if any(i.get("kind") == kind and i.get("frame_index") == frame_index for i in items):
            return
        entry: dict[str, Any] = {"kind": kind, "detail": str(detail)[:500]}
        if frame_index is not None:
            entry["frame_index"] = frame_index
        items.append(entry)
        path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("record degradation %s failed", kind, exc_info=True)

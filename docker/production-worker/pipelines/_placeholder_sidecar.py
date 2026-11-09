"""共享占位上报 sidecar（Task 10：补 78fd24054 只覆盖 v1 的缺口）。

``_write_placeholder_sidecar`` 原先**唯一定义在** ``video_gen_ai_visual.py``（v1）。
v2_mixed / v3_segmented 无任何占位 sidecar —— 而 >1 scene_group 走 v3，即**课程视频
主路径永远不弹占位告警**：全帧黑场的任务照样 status=ready/progress=100，与完美渲染
不可区分。

提到共享模块后三条管线复用同一实现，语义与 v1 完全一致。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SIDECAR_NAME = "placeholder_frames.json"


def write_placeholder_sidecar(
    task_dir: Path,
    indices: list[int],
    total: int,
    *,
    touched: list[int] | None = None,
) -> None:
    """记录本次渲染中降级为黑场/静态占位的帧，供 gateway 上报给用户。

    2026-07-29：修前占位帧也 append 进 segments，``if not segments`` 守卫放行 →
    全帧黑场的任务照样 status=ready/progress=100，与完美渲染不可区分。
    无占位时删除旧 sidecar——否则上次的告警会永久残留在下次成功的成片上。

    ``touched``：本次实际处理过的帧（增量渲染只碰改动帧）。给了它就按帧做增量合并——
    只对本次处理过的帧改写结论，未触碰的帧沿用上次结果。否则一次只重渲 1 帧的
    recompose 会把其余 95 帧仍是黑场的事实一笔抹掉，报「全部正常」——
    那正是本 sidecar 要消灭的假成功。
    """
    path = Path(task_dir) / SIDECAR_NAME
    try:
        merged = set(indices)
        if touched is not None and path.exists():
            try:
                prev = json.loads(path.read_text(encoding="utf-8")).get("indices") or []
                # 上次的占位帧里，本次没碰过的仍然是占位（本次碰过的以本次结论为准）
                merged |= {i for i in prev if isinstance(i, int) and i not in set(touched)}
            except (OSError, ValueError):
                pass  # 旧 sidecar 不可读 → 只用本次结论
        if not merged:
            path.unlink(missing_ok=True)
            return
        path.write_text(
            json.dumps({"indices": sorted(merged), "total": total}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        logger.warning("write %s failed", SIDECAR_NAME, exc_info=True)

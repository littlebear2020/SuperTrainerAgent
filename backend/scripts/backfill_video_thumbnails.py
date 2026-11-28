"""Backfill ``result.thumbnail_path`` for historical video tasks (2026-11-28).

背景：worker 只在抽帧成功时写 ``result.thumbnail_path``，Remotion 旧实现吞异常、
历史任务（含已下线的 ``remotion`` 类型）从未抽帧，于是「我的作品」里一批 ready 任务
没有真实缩略图。本脚本离线补图：复用 gateway 现有同步 ``_extract_thumbnail``，缩略图
落在成片同目录的 ``thumbnail.jpg``（worker 任务即 ``task_dir/thumbnail.jpg``），只写回
``result`` 的 ``thumbnail_path`` 一个键。

范围（默认扫描也只限这五种）：remotion_course / remotion / ai_image / ai_visual / ai_video
中 status=ready、``result.output_path`` 存在、且 ``result.thumbnail_path`` 缺失或其文件
不存在的任务。不重新渲染、不调用 provider、不删除任务或 outputs。

默认 dry-run（只读连接，零写入）；``--apply`` 才写库，每个任务一条
``UPDATE ... SET result = json_set(result, '$.thumbnail_path', ?) WHERE id = ? AND status = 'ready'``，
不覆盖并发写入的其他字段；单任务失败不中断批次。

用法（在 gateway 容器内运行——写入的路径前缀须与 gateway 一致，容器里有 ffmpeg）：
    docker exec <gateway> sh -c 'cd /app/backend && PYTHONPATH=. uv run --no-sync python scripts/backfill_video_thumbnails.py'
    docker exec <gateway> sh -c 'cd /app/backend && PYTHONPATH=. uv run --no-sync python scripts/backfill_video_thumbnails.py --apply --task-type ai_video --limit 200'

退出码：0 = 正常（dry-run 或 apply 完成）；1 = 范围内任务有抽帧失败（其余照常处理）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

TASK_TYPES: tuple[str, ...] = (
    "remotion_course",
    "remotion",
    "ai_image",
    "ai_visual",
    "ai_video",
)


def find_db(explicit: str | None = None) -> Path:
    """Locate deerflow.db. Mirrors scripts/reconcile_video_tasks.py candidates."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"--db-path {p} does not exist")
        return p
    candidates = [
        Path(os.getcwd(), ".deer-flow", "data", "deerflow.db"),
        Path(os.getcwd(), "backend", ".deer-flow", "data", "deerflow.db"),
        Path(os.path.dirname(os.getcwd()), "backend", ".deer-flow", "data", "deerflow.db"),
        Path(_SCRIPT_DIR, "..", ".deer-flow", "data", "deerflow.db"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    raise FileNotFoundError("Could not find deerflow.db. Expected one of: " + ", ".join(str(c) for c in candidates) + ". Use --db-path to specify.")


def _parse_result(raw: str | None) -> dict:
    """result JSON → dict；空/损坏返回 {}（损坏行不会被当作有缩略图）。"""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def scan_tasks(
    conn: sqlite3.Connection,
    *,
    task_type: str | None = None,
    task_id: str | None = None,
    limit: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """只读扫描：返回 (candidates, skipped)。candidates = 需要补图且成片存在的任务。"""
    types = [task_type] if task_type else list(TASK_TYPES)
    where = "status = 'ready' AND task_type IN ({})".format(",".join("?" * len(types)))
    params: list[object] = list(types)
    if task_id:
        where += " AND id = ?"
        params.append(task_id)
    sql = f"SELECT id, task_type, status, result FROM unified_tasks WHERE {where} ORDER BY created_at"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    candidates: list[dict] = []
    skipped: list[dict] = []
    for tid, ttype, status, result in rows:
        info = {"id": tid, "task_type": ttype, "status": status}
        parsed = _parse_result(result)
        output_path = parsed.get("output_path") or ""
        thumb_path = parsed.get("thumbnail_path") or ""
        if not output_path:
            info["reason"] = "no_output_path"
            skipped.append(info)
            continue
        if not Path(output_path).exists():
            info["reason"] = "output_missing"
            skipped.append(info)
            continue
        if thumb_path and Path(thumb_path).exists():
            info["reason"] = "thumbnail_present"
            skipped.append(info)
            continue
        info["output_path"] = output_path
        candidates.append(info)
    return candidates, skipped


def gateway_extractor() -> Callable[[str, Path], str | None]:
    """复用 gateway 现有同步抽帧函数（不修改它）：(video_path, task_dir) -> thumbnail path | None。"""
    from app.gateway.video_gen.service import _extract_thumbnail

    return _extract_thumbnail


def _write_thumbnail_path(conn: sqlite3.Connection, task_id: str, thumb_path: str) -> None:
    """只动 result 的 thumbnail_path 一个键；status 已不是 ready 的行不会被写。"""
    conn.execute(
        "UPDATE unified_tasks SET result = json_set(COALESCE(result, '{}'), '$.thumbnail_path', ?) WHERE id = ? AND status = 'ready'",
        (thumb_path, task_id),
    )
    conn.commit()


def backfill(
    conn: sqlite3.Connection,
    candidates: list[dict],
    *,
    apply: bool,
    extractor: Callable[[str, Path], str | None],
) -> dict[str, int]:
    """逐任务抽帧；apply=False 只报告会生成的数量（抽到临时目录，不落盘到任务目录）。单任务失败不中断批次。"""
    counts = {"scanned": 0, "generated": 0, "skipped": 0, "failed": 0}
    for info in candidates:
        output_path = info["output_path"]
        extracted = False
        try:
            if apply:
                thumb = extractor(output_path, Path(output_path).parent)
                extracted = bool(thumb and Path(thumb).exists())
            else:
                # dry-run 连文件系统都不写：抽到一次性临时目录，存在性在目录销毁前判定
                with tempfile.TemporaryDirectory(prefix="thumb-dry-run-") as scratch:
                    thumb = extractor(output_path, Path(scratch))
                    extracted = bool(thumb and Path(thumb).exists())
        except Exception as exc:  # noqa: BLE001 - 单任务失败不中断批次
            print(f"  [failed] {info['id']} ({info['task_type']}): {type(exc).__name__}: {exc}")
        if not apply:
            if extracted:
                counts["generated"] += 1
                print(f"  [dry-run] would backfill {info['id']} ({info['task_type']}) -> thumbnail.jpg")
            else:
                counts["failed"] += 1
                print(f"  [dry-run] extraction would fail {info['id']} ({info['task_type']})")
            continue
        if not extracted:
            counts["failed"] += 1
            print(f"  [failed] {info['id']} ({info['task_type']}): no thumbnail extracted")
            continue
        try:
            _write_thumbnail_path(conn, info["id"], str(thumb))
        except sqlite3.Error as exc:
            counts["failed"] += 1
            print(f"  [failed] {info['id']} ({info['task_type']}): write-back failed: {exc}")
            continue
        counts["generated"] += 1
        print(f"  [backfilled] {info['id']} ({info['task_type']})")
    return counts


def print_report(counts: dict[str, int]) -> None:
    print("\nscanned={scanned} generated={generated} skipped={skipped} failed={failed}".format(**counts))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史视频任务缩略图离线回填（只写 result.thumbnail_path；默认 dry-run）",
        epilog=("在 gateway 容器内运行（写入路径前缀与 gateway 一致、容器内有 ffmpeg）：docker exec <gateway> sh -c 'cd /app/backend && PYTHONPATH=. uv run --no-sync python scripts/backfill_video_thumbnails.py'"),
    )
    parser.add_argument("--db-path", default=None, help="Path to deerflow.db (default: auto-detect)")
    parser.add_argument("--apply", action="store_true", help="写入 result.thumbnail_path（不传 = dry-run，只读连接）")
    parser.add_argument("--task-type", action="append", default=None, choices=TASK_TYPES, help="只处理指定任务类型（可多次；默认五种）")
    parser.add_argument("--task-id", default=None, help="只处理指定任务")
    parser.add_argument("--limit", type=int, default=None, help="最多扫描多少行（按创建时间）")
    args = parser.parse_args(argv)

    db_path = find_db(args.db_path)
    if args.apply:
        conn = sqlite3.connect(str(db_path))
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # dry-run 只读连接
    try:
        candidates, skipped = scan_tasks(
            conn,
            task_type=args.task_type[0] if args.task_type and len(args.task_type) == 1 else None,
            task_id=args.task_id,
            limit=args.limit,
        )
        if args.task_type and len(args.task_type) > 1:
            # argparse 的 append 形态允许多个类型：手动过滤（默认五种时不走这里）
            wanted = set(args.task_type)
            candidates = [c for c in candidates if c["task_type"] in wanted]
            skipped = [s for s in skipped if s["task_type"] in wanted]
        print(f"DB: {db_path}")
        print("[dry-run] 未写库。复核后带 --apply 执行。" if not args.apply else "[apply] 写入 result.thumbnail_path。")
        counts = backfill(conn, candidates, apply=args.apply, extractor=gateway_extractor())
        counts["skipped"] += len(skipped)
        counts["scanned"] = len(candidates) + len(skipped)
        print_report(counts)
        return 1 if counts["failed"] > 0 else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

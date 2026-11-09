"""Reconcile video_gen task rows against on-disk artifacts (2026-09-13 死链修复).

背景：26 个 vg_ 任务目录被应用外批量删除后，unified_tasks 残留 thumbnail_url
死链与「假就绪」行（status=ready 但 output.mp4/thumbnail.jpg 双失）。本脚本做
DB ↔ 磁盘对账；磁盘清理（人工 rm）前必须先跑本脚本（dry-run 圈选 → apply 连带
清引用/删行），否则会再造一批死链与假就绪。

用法（默认 dry-run，绝不写库）：
    cd backend && PYTHONPATH=. python scripts/reconcile_video_tasks.py \
        [--db-path PATH] [--path-map /app/backend/.deer-flow=/opt/trainer-ai/.deer-flow] \
        [--storage-root HOST_ROOT] [--all-statuses] \
        [--apply {clear-thumbnail-refs,delete-rows}] [--confirm-delete N]

路径映射：DB result 里的产物路径是 gateway 容器路径（/app/backend/.deer-flow/...）。
宿主机跑法须映射到宿主根——优先级：--path-map 显式前缀替换 > --storage-root 显式根
> 候选根自动探测（/opt/trainer-ai/.deer-flow 等首个存在者）。在 gateway 容器内
（docker exec deer-flow-gateway ...）跑则前缀天然一致，零映射。

档位（按 result JSON 的 thumbnail_path/output_path 文件存在性；默认只扫
status='ready'，--all-statuses 放宽）：
  - both_missing        双失（假就绪）：缩略图引用与 output 引用均在且文件均失
                        → delete-rows 候选
  - thumb_missing       仅缩略图引用失（output 完好或未引用）→ clear-thumbnail-refs 候选
  - output_missing_only 仅 output 失（缩略图在）→ 只报告，两个 apply 均不碰
  - intact              完好
  - unparseable         result JSON 损坏 → 只报告，永不进任何 apply 档

台账口径：external_api_calls 按 task_id 软引用这批任务（DB 无外键），本脚本
**只读报告、绝不删台账**（console 聚合不 join unified_tasks，删行不丢账）。

退出码：0 = dry-run/成功；1 = delete-rows 确认计数不匹配（拒绝执行）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent

# gateway 容器内 DB 路径前缀（worker/service 落库的 result 产物路径都带它）
CONTAINER_PREFIX = "/app/backend/.deer-flow"

# 宿主侧对应根的自动探测候选（火山云生产/测试服务器宿主路径见运维手册；
# 容器内跑法前缀相同 = 无需映射；cwd 候选覆盖本地开发库）
_HOST_ROOT_CANDIDATES = (
    Path("/opt/trainer-ai/.deer-flow"),
    Path(CONTAINER_PREFIX),
    Path(os.getcwd(), ".deer-flow"),
    Path(os.getcwd(), "backend", ".deer-flow"),
    Path(_SCRIPT_DIR, "..", ".deer-flow"),
)


def find_db(explicit: str | None = None) -> Path:
    """Locate deerflow.db. Mirrors scripts/cleanup_stock_tasks.py candidates."""
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


def make_mapper(path_maps: list[tuple[str, str]], storage_root: str | None = None) -> Callable[[str], str]:
    """Build DB-path → 检查用路径 的映射函数。

    --path-map (FROM=TO) 无条件前缀替换（可多条，按序短路；边界由操作者自担）；
    storage_root/候选根仅在「原路径不存在且命中容器前缀（含 / 边界）」时重映射，
    且映射目标必须真实存在——否则维持原路径（文件确已删除，按缺失计）。
    """
    auto: list[tuple[str, str]] = []
    if storage_root:
        auto.append((CONTAINER_PREFIX, str(Path(storage_root).resolve())))
    else:
        for c in _HOST_ROOT_CANDIDATES:
            if c.exists():
                auto.append((CONTAINER_PREFIX, str(c.resolve())))
                break

    def map_path(p: str) -> str:
        for src, dst in path_maps:
            if p.startswith(src):
                return dst + p[len(src) :]
        if not Path(p).exists():
            for src, dst in auto:
                # 复审加固（2026-09-13）：带 / 边界匹配——'/app/backend/.deer-flow-x'
                # 这类同前缀兄弟目录不属容器根，不得重映射（显式 --path-map 维持
                # 无条件前缀替换语义，边界由操作者自担）
                if p.startswith(src + "/"):
                    cand = dst + p[len(src) :]
                    if Path(cand).exists():
                        return cand
        return p

    return map_path


def scan_tasks(
    conn: sqlite3.Connection,
    map_path: Callable[[str], str],
    *,
    all_statuses: bool = False,
) -> dict[str, list[dict]]:
    """Bucket vg_ tasks by artifact existence. Read-only."""
    where = "id LIKE 'vg\\_%' ESCAPE '\\'"
    if not all_statuses:
        where += " AND status = 'ready'"
    rows = conn.execute(f"SELECT id, user_id, task_type, status, thumbnail_url, result FROM unified_tasks WHERE {where} ORDER BY created_at").fetchall()
    buckets: dict[str, list[dict]] = {
        "both_missing": [],
        "thumb_missing": [],
        "output_missing_only": [],
        "intact": [],
        "unparseable": [],
    }
    for tid, uid, ttype, status, turl, result in rows:
        info = {"id": tid, "user_id": uid, "task_type": ttype, "status": status, "thumb_ok": False, "out_ok": False}
        try:
            r = json.loads(result) if result else {}
        except (json.JSONDecodeError, TypeError):
            buckets["unparseable"].append(info)
            continue
        if not isinstance(r, dict):
            buckets["unparseable"].append(info)
            continue
        tp = r.get("thumbnail_path") or ""
        op = r.get("output_path") or ""
        # 引用存在性：缩略图引用看 result 或 DB 列（P2-4 完成路径两种都可能留痕）
        thumb_ref = bool(tp) or bool(turl)
        out_ref = bool(op)
        info["thumb_ok"] = bool(tp) and Path(map_path(tp)).exists()
        info["out_ok"] = out_ref and Path(map_path(op)).exists()
        if out_ref and not info["out_ok"]:
            # output 引用在但文件失 → 假就绪域
            if thumb_ref and not info["thumb_ok"]:
                buckets["both_missing"].append(info)  # 缩略图引用同样失 → 删行候选
            else:
                buckets["output_missing_only"].append(info)  # 只报告，两个 apply 均不碰
        elif thumb_ref and not info["thumb_ok"]:
            buckets["thumb_missing"].append(info)  # output 完好/未引用；缩略图引用死 → 清列候选
        else:
            buckets["intact"].append(info)  # 无产物引用的行不属于本脚本管辖
    return buckets


def apply_clear_thumbnail_refs(conn: sqlite3.Connection, task_ids: list[str]) -> int:
    """thumb_missing 档：清 DB 列引用（不动 result JSON——自清修复后路由 404 属正确行为）。"""
    cur = conn.executemany("UPDATE unified_tasks SET thumbnail_url = NULL WHERE id = ?", [(t,) for t in task_ids])
    conn.commit()
    return cur.rowcount


def apply_delete_rows(conn: sqlite3.Connection, task_ids: list[str]) -> int:
    """both_missing 档：删 unified_tasks 行。external_api_calls 一行不动（台账保留）。"""
    cur = conn.executemany("DELETE FROM unified_tasks WHERE id = ?", [(t,) for t in task_ids])
    conn.commit()
    return cur.rowcount


def count_external_api_calls(conn: sqlite3.Connection, task_ids: list[str]) -> int:
    """只读报告：这批任务在 external_api_calls 的软引用行数。-1 = 表不存在（最小测试库）。"""
    if not task_ids:
        return 0
    q = f"SELECT COUNT(*) FROM external_api_calls WHERE task_id IN ({','.join('?' * len(task_ids))})"
    try:
        return int(conn.execute(q, task_ids).fetchone()[0])
    except sqlite3.OperationalError:
        return -1


def print_report(db_path: Path, buckets: dict[str, list[dict]]) -> None:
    print(f"DB: {db_path}")
    labels = (
        ("both_missing", "双失（假就绪，delete-rows 候选）"),
        ("thumb_missing", "缩略图引用已失（output 完好/未引用；clear-thumbnail-refs 候选）"),
        ("output_missing_only", "仅成片失（只报告，不自动处理）"),
        ("intact", "完好"),
        ("unparseable", "result 损坏（只报告）"),
    )
    for key, label in labels:
        items = buckets[key]
        print(f"\n[{label}] {len(items)} 个")
        for it in items:
            print(f"  {it['id']}  status={it['status']}  type={it['task_type']}  thumb={'Y' if it['thumb_ok'] else 'N'} out={'Y' if it['out_ok'] else 'N'}")


def parse_path_map(raw: list[str] | None) -> list[tuple[str, str]]:
    maps: list[tuple[str, str]] = []
    for item in raw or []:
        if "=" not in item:
            raise SystemExit(f"--path-map 需为 FROM=TO 形态，收到: {item!r}")
        src, dst = item.split("=", 1)
        maps.append((src.rstrip("/"), dst.rstrip("/")))
    return maps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="video_gen 任务产物对账（死链/假就绪清理前必跑）")
    parser.add_argument("--db-path", default=None, help="Path to deerflow.db (default: auto-detect)")
    parser.add_argument("--path-map", action="append", default=None, metavar="FROM=TO", help="DB 路径前缀映射（可多次）")
    parser.add_argument("--storage-root", default=None, help="宿主侧 .deer-flow 根（对应容器 /app/backend/.deer-flow）")
    parser.add_argument("--all-statuses", action="store_true", help="放宽到全部状态（默认仅 status=ready）")
    parser.add_argument("--apply", choices=("clear-thumbnail-refs", "delete-rows"), default=None, help="不传 = dry-run")
    parser.add_argument("--confirm-delete", type=int, default=-1, metavar="N", help="delete-rows 强制确认：N 必须等于双失任务数")
    args = parser.parse_args(argv)

    db_path = find_db(args.db_path)
    write_mode = args.apply is not None
    if write_mode:
        conn = sqlite3.connect(str(db_path))
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # dry-run 只读连接
    try:
        mapper = make_mapper(parse_path_map(args.path_map), args.storage_root)
        buckets = scan_tasks(conn, mapper, all_statuses=args.all_statuses)
        print_report(db_path, buckets)

        if not write_mode:
            print("\n[dry-run] 未写库。复核无误后带 --apply=clear-thumbnail-refs / delete-rows（delete 需 --confirm-delete N）执行。")
            return 0

        if args.apply == "clear-thumbnail-refs":
            ids = [t["id"] for t in buckets["thumb_missing"]]
            if not ids:
                print("\n无「仅缩略图失」任务，跳过。")
                return 0
            n = apply_clear_thumbnail_refs(conn, ids)
            print(f"\n已清 {n} 行 thumbnail_url 引用（未动 result JSON；其余档位未碰）。")
            return 0

        # delete-rows
        both = buckets["both_missing"]
        n = len(both)
        if args.confirm_delete != n:
            print(f"\n[abort] --confirm-delete={args.confirm_delete} ≠ 双失任务数 {n}。先 dry-run 复核清单后再执行。")
            return 1
        ids = [t["id"] for t in both]
        calls = count_external_api_calls(conn, ids)
        # -1 = 表不存在（最小测试库）：跳过台账报告（复审修复：-1 为 truthy，
        # 修前会打出「受影响台账 -1 行」）
        if calls > 0:
            print(f"\n受影响 external_api_calls 台账 {calls} 行：保留不删（console 聚合不 join unified_tasks）。")
        deleted = apply_delete_rows(conn, ids)
        print(f"已删 {deleted} 行 unified_tasks（external_api_calls 一行未动）。")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

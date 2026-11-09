#!/usr/bin/env python3
"""WD1（2026-08-24 看门狗活性重构）：误杀消除门禁--reaper 收尸日志扫描。

≥3 个 ≥50 帧真实任务跑完后执行：扫描 gateway 日志里的
``Reaped stalled task`` / ``Reaped composing task`` 事件，断言目标任务
**零收尸且全部完成**（历史基线：08-20/21 三次误杀）。

用法：
    cd backend && uv run python scripts/acceptance/watchdog/wd1_reap_scan.py \
        --task-ids vg_xxx,vg_yyy,vg_zzz --log-file /path/to/gateway.log

    # 或从 DB 直查（sqlite 在容器卷里时用 docker cp 出来再传路径）
    uv run python scripts/acceptance/watchdog/wd1_reap_scan.py --db /path/to/unified.db

优先 --task-ids + --log-file（日志含谓词归因）；--db 是兜底（只看终态与
error_message 里的 reap 痕迹）。退出码 0 = 门禁通过。
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

REAP_LOG_RE = re.compile(r"Reaped (?:stalled|composing) task (\S+)")
PREDICATE_RE = re.compile(r"predicate (\w+)")


def scan_log(log_file: Path, task_ids: list[str]) -> dict:
    hits: dict[str, list[str]] = {tid: [] for tid in task_ids}
    with log_file.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            m = REAP_LOG_RE.search(line)
            if not m or m.group(1) not in hits:
                continue
            p = PREDICATE_RE.search(line)
            hits[m.group(1)].append(p.group(1) if p else "unknown")
    return hits


def scan_db(db_file: Path, task_ids: list[str]) -> dict:
    out: dict[str, dict] = {}
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        for tid in task_ids:
            row = conn.execute("SELECT status, error_message, retry_count FROM unified_tasks WHERE id = ?", (tid,)).fetchone()
            if row is None:
                out[tid] = {"found": False}
                continue
            out[tid] = {
                "found": True,
                "status": row["status"],
                "retry_count": row["retry_count"],
                "reaped": "Reaped by watchdog" in (row["error_message"] or ""),
            }
    finally:
        conn.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-ids", default="", help="逗号分隔的任务 id")
    parser.add_argument("--log-file", default="", help="gateway 日志文件路径")
    parser.add_argument("--db", default="", help="unified_tasks sqlite 文件路径（兜底）")
    args = parser.parse_args()

    task_ids = [t.strip() for t in args.task_ids.split(",") if t.strip()]
    if not task_ids:
        print("需要 --task-ids（逗号分隔）", file=sys.stderr)
        return 2

    ok = True
    print(f"== WD1 误杀消除扫描：{len(task_ids)} 个任务 ==")
    if args.log_file:
        log_file = Path(args.log_file)
        if not log_file.exists():
            print(f"日志文件不存在：{log_file}", file=sys.stderr)
            return 2
        hits = scan_log(log_file, task_ids)
        for tid, predicates in hits.items():
            status = "PASS" if not predicates else "FAIL"
            ok = ok and not predicates
            print(f"  [{status}] {tid}: 收尸事件 {len(predicates)} 次 {predicates}")
    elif args.db:
        db_file = Path(args.db)
        if not db_file.exists():
            print(f"DB 文件不存在：{db_file}", file=sys.stderr)
            return 2
        rows = scan_db(db_file, task_ids)
        for tid, info in rows.items():
            if not info.get("found"):
                ok = False
                print(f"  [FAIL] {tid}: 任务行缺失")
                continue
            done = info["status"] in ("ready",)
            no_reap = not info["reaped"]
            status = "PASS" if done and no_reap else "FAIL"
            ok = ok and done and no_reap
            print(f"  [{status}] {tid}: status={info['status']}, retry={info['retry_count']}, reap痕迹={info['reaped']}（要求：ready 且零收尸）")
    else:
        print("需要 --log-file 或 --db 之一", file=sys.stderr)
        return 2

    print(f"WD1 判定：{'PASS' if ok else 'FAIL'}（目标任务零收尸且全部完成）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""P1-S1（2026-09-30 上游移植）迁移配套：清理旧命名温池容器。

沙箱身份 user 化后，确定性沙箱 ID 从 8-hex（纯 thread 派生）换成
16-hex（user:thread 派生，`deerflow.sandbox.identity`）。发布切版的瞬间，
旧命名温池容器（`deer-flow-sandbox-<8hex>`）永远不会再被新代码命中——
不清理就变成无人认领的常驻容器（占内存/端口预算）。

计划选择「发布脚本 + 文档」路线（plan §4.1 迁移过渡二选一）：
在低峰发布窗口、gateway 停机后运行本脚本，再起新版本。也可以随时
重跑——幂等，新命名容器（16-hex）不会被碰。

判别规则：容器名 = `<prefix>-<id 段>`；id 段为 8 位 hex 的即旧命名。
非 `deer-flow-sandbox-` 前缀的容器一律不动。默认 dry-run，--apply 才动手。

用法（仓库 backend 目录下）：
    uv run python scripts/cleanup_legacy_warm_pool_containers.py          # 只列出
    uv run python scripts/cleanup_legacy_warm_pool_containers.py --apply  # 实际 stop+rm
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

DEFAULT_PREFIX = "deer-flow-sandbox"
# 旧命名：8 位小写 hex（sha256(thread)[:8]）。新命名是 16 位 hex。
LEGACY_ID_RE = re.compile(r"^[0-9a-f]{8}$")


def _docker(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help=f"容器名前缀（默认 {DEFAULT_PREFIX}）")
    parser.add_argument("--apply", action="store_true", help="实际执行 stop+rm（默认 dry-run 只列出）")
    args = parser.parse_args()

    ps = _docker(["ps", "-a", "--format", "{{.Names}}", "--filter", f"name={args.prefix}-"])
    if ps.returncode != 0:
        print(f"docker ps 失败（rc={ps.returncode}）: {ps.stderr.strip()}", file=sys.stderr)
        return 1

    legacy: list[str] = []
    for name in ps.stdout.splitlines():
        name = name.strip()
        if not name or not name.startswith(f"{args.prefix}-"):
            continue
        id_part = name[len(args.prefix) + 1 :]
        if LEGACY_ID_RE.fullmatch(id_part):
            legacy.append(name)

    if not legacy:
        print("没有旧命名（8-hex）温池容器。无需清理。")
        return 0

    mode = "APPLY（stop+rm）" if args.apply else "DRY-RUN（只列出，加 --apply 执行）"
    print(f"发现 {len(legacy)} 个旧命名容器，模式：{mode}")
    for name in legacy:
        print(f"  {name}")

    if not args.apply:
        return 0

    failed = 0
    for name in legacy:
        for action in ("stop", "rm"):
            r = _docker([action, name])
            if r.returncode != 0:
                print(f"  docker {action} {name} 失败: {r.stderr.strip()}", file=sys.stderr)
                failed += 1
                break
        else:
            print(f"  已清理 {name}")
    if failed:
        print(f"{failed} 个容器清理失败，见 stderr。", file=sys.stderr)
        return 1
    print(f"清理完成：{len(legacy)} 个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""部署前检查单（2026-10-14 自动化）：部署公司服务器/测试机前一屏回答三件事——

1. 有无在途工作（open PR / 附加 worktree / 本地 main 未推送/未拉取）——**阻断项**，有则先收尾；
2. 近 14 天 changelog 的部署动作提示（镜像重建/重启/config patch/warm 池等关键词行）——提示项；
3. 仓库 `config_version` 当前值——部署端 config 只读挂载、靠版本号触发升级提示，须人工比对。

退出码：1 = 有阻断项；0 = 干净。提示项不影响退出码。
gh 不可用时 open-PR 检查降级为提示（不静默跳过）。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG_DIR = REPO_ROOT / "docs" / "changelog"
RECENT_DAYS = 14
DEPLOY_KEYWORDS = re.compile(r"重建|部署|config_version|patcher|patch|重启|nginx|warm|镜像|K1\d")


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def main() -> int:
    issues: list[str] = []  # 阻断项：在途工作
    notes: list[str] = []  # 提示项：部署动作与版本号

    run(["git", "fetch", "origin", "--prune"])  # 失败不致命（离线场景照旧用本地 ref）

    pr = run(["gh", "pr", "list", "--state", "open", "--json", "number,title"])
    if pr.returncode != 0:
        notes.append("gh 不可用/未登录——open PR 检查未执行，请人工确认 GitHub 无在途 PR")
    else:
        for item in json.loads(pr.stdout or "[]"):
            issues.append(f"open PR #{item['number']}：{item['title']}——合入或明确不带后再部署")

    wt = run(["git", "worktree", "list", "--porcelain"])
    worktrees = [line[9:] for line in wt.stdout.splitlines() if line.startswith("worktree ")]
    for w in worktrees:
        if Path(w).resolve() != REPO_ROOT:
            issues.append(f"在途 worktree：{w}——确认其分支已合入（或明确不随本次部署），收尾后 remove")

    ahead = run(["git", "rev-list", "--count", "origin/main..main"]).stdout.strip()
    behind = run(["git", "rev-list", "--count", "main..origin/main"]).stdout.strip()
    if ahead and ahead != "0":
        issues.append(f"本地 main 领先 origin/main {ahead} 笔——先 push，部署以远端 main 为准")
    if behind and behind != "0":
        notes.append(f"本地 main 落后 origin/main {behind} 笔——部署打包前先 pull")

    cutoff = date.today() - timedelta(days=RECENT_DAYS)
    seen = 0
    for path in sorted(CHANGELOG_DIR.glob("changelog-*.md"), reverse=True):
        try:
            file_date = date(int(path.name[10:14]), int(path.name[15:17]), int(path.name[18:20]))
        except ValueError:
            continue
        if file_date < cutoff:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        tldr = next((ln.strip() for ln in text.splitlines() if "TL;DR" in ln), "")
        hits = [ln.strip() for ln in text.splitlines() if DEPLOY_KEYWORDS.search(ln) and "TL;DR" not in ln][:5]
        notes.append(f"· {path.name[10:20]} {path.name[21:-3]}")
        if tldr:
            notes.append(f"  TL;DR：{tldr[:150]}")
        for ln in hits:
            notes.append(f"  {ln[:180]}")
        seen += 1
    if seen == 0:
        notes.append(f"近 {RECENT_DAYS} 天无 changelog——无待部署动作")

    m = re.search(
        r"^config_version:\s*(\d+)",
        (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8", errors="replace"),
        re.M,
    )
    notes.append(
        f"仓库 config_version 当前 = {m.group(1) if m else '未解析到'}——"
        "确认部署端 config.yaml 已 patch 到同值（只读挂载靠版本号触发升级提示）"
    )

    print(f"=== 部署前检查（{date.today()}，近 {RECENT_DAYS} 天窗口）===")
    if issues:
        print("[阻断项]")
        issues and [print(f"  ✗ {i}") for i in issues]
    else:
        print("[阻断项] 无——无 open PR / 在途 worktree / 未同步 main")
    print("[部署动作提示]")
    for n in notes:
        print(f"  {n}")
    if issues:
        print("结论：有在途工作，先收尾再部署。")
        return 1
    print("结论：无阻断项；按上方提示核对部署动作与 config_version 后可部署。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""正式发布前检查：固定发布 ref，阻断未验收例外，提示其他 Agent 在途工作。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RECENT_DAYS = 14
DEPLOY_KEYWORDS = re.compile(r"重建|部署|config_version|patcher|patch|重启|nginx|warm|镜像|K1\d")
BASE_REF = "origin/main"


def run(cmd: list[str], env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False, env=env)


def find_release_blockers(root: Path) -> list[str]:
    backlog = root / "docs" / "findings-backlog.md"
    if not backlog.is_file():
        return []
    return [line.strip() for line in backlog.read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("- [ ]") and "[release-blocker]" in line]


def _resolve_ref(ref: str) -> subprocess.CompletedProcess[str]:
    return run(["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"])


def _validate_release_ref(ref: str, issues: list[str]) -> None:
    base = _resolve_ref(BASE_REF)
    source = _resolve_ref(ref)
    if base.returncode != 0:
        issues.append(f"无法解析发布基准 {BASE_REF}：{base.stderr.strip()}")
        return
    if source.returncode != 0:
        issues.append(f"无法解析发布 ref {ref}：{source.stderr.strip()}")
        return
    source_sha = source.stdout.strip()
    base_sha = base.stdout.strip()
    ancestry = run(["git", "merge-base", "--is-ancestor", source_sha, base_sha])
    if ancestry.returncode != 0:
        issues.append(f"发布 ref {ref} 未合入 {BASE_REF}，正式发布拒绝使用未审提交")


def _github_repo() -> str | None:
    remote = run(["git", "remote", "get-url", "origin"])
    if remote.returncode != 0:
        return None
    value = remote.stdout.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.search(r"github\.com[:/]([^/]+/[^/]+)$", value)
    return match.group(1) if match else None


def _append_agent_notes(notes: list[str]) -> None:
    repository = _github_repo()
    if repository is None:
        notes.append("无法从 origin 确定 GH_REPO——开放 PR 检查未执行")
    else:
        env = {**os.environ, "GH_REPO": repository}
        pr = run(["gh", "pr", "list", "--state", "open", "--json", "number,title"], env=env)
        if pr.returncode != 0:
            notes.append("gh 不可用/未登录——开放 PR 检查未执行，请人工确认")
        else:
            try:
                items = json.loads(pr.stdout or "[]")
                for item in items:
                    notes.append(f"开放 PR #{item['number']}：{item['title']}——不会混入固定 ref 发布包")
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                notes.append(f"开放 PR 响应无法解析：{exc}")

    wt = run(["git", "worktree", "list", "--porcelain"])
    if wt.returncode != 0:
        notes.append("worktree 列表读取失败")
    else:
        worktrees = [line[9:] for line in wt.stdout.splitlines() if line.startswith("worktree ")]
        for worktree in worktrees:
            if Path(worktree).resolve() != REPO_ROOT.resolve():
                notes.append(f"其他 worktree：{worktree}——仅提示，不阻断不可变 ref 发布")

    ahead_result = run(["git", "rev-list", "--count", "origin/main..main"])
    behind_result = run(["git", "rev-list", "--count", "main..origin/main"])
    ahead = ahead_result.stdout.strip()
    behind = behind_result.stdout.strip()
    if ahead_result.returncode == 0 and ahead and ahead != "0":
        notes.append(f"本地 main 领先 origin/main {ahead} 笔——仅提示，发布以固定 ref 为准")
    if behind_result.returncode == 0 and behind and behind != "0":
        notes.append(f"本地 main 落后 origin/main {behind} 笔——仅提示，发布以固定 ref 为准")


def _append_changelog_notes(notes: list[str]) -> None:
    cutoff = date.today() - timedelta(days=RECENT_DAYS)
    seen = 0
    changelog_dir = REPO_ROOT / "docs" / "changelog"
    for path in sorted(changelog_dir.glob("changelog-*.md"), reverse=True):
        try:
            file_date = date(int(path.name[10:14]), int(path.name[15:17]), int(path.name[18:20]))
        except ValueError:
            continue
        if file_date < cutoff:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        tldr = next((line.strip() for line in text.splitlines() if "TL;DR" in line), "")
        hits = [line.strip() for line in text.splitlines() if DEPLOY_KEYWORDS.search(line) and "TL;DR" not in line][:5]
        notes.append(f"· {path.name[10:20]} {path.name[21:-3]}")
        if tldr:
            notes.append(f"  TL;DR：{tldr[:150]}")
        for line in hits:
            notes.append(f"  {line[:180]}")
        seen += 1
    if seen == 0:
        notes.append(f"近 {RECENT_DAYS} 天无 changelog——无待部署动作")

    try:
        config_text = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        notes.append(f"config.example.yaml 读取失败：{exc}")
        return
    match = re.search(r"^config_version:\s*(\d+)", config_text, re.M)
    notes.append(f"仓库 config_version 当前 = {match.group(1) if match else '未解析到'}——仍需核对部署端实际配置；本检查不证明配置已生效")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="正式发布前检查")
    parser.add_argument("--ref", default=BASE_REF)
    args = parser.parse_args(argv)
    issues: list[str] = []
    notes: list[str] = []

    fetch = run(["git", "fetch", "origin", "--prune"])
    if fetch.returncode != 0:
        issues.append(f"git fetch 失败，无法核对远端最新状态：{fetch.stderr.strip()}")
    _validate_release_ref(args.ref, issues)
    for blocker in find_release_blockers(REPO_ROOT):
        issues.append(f"开放 release-blocker：{blocker}")
    _append_agent_notes(notes)
    _append_changelog_notes(notes)

    print(f"=== 部署前检查（{date.today()}，近 {RECENT_DAYS} 天窗口；发布 ref={args.ref}）===")
    if issues:
        print("[阻断项]")
        for issue in issues:
            print(f"  ✗ {issue}")
    else:
        print("[阻断项] 无——无开放 release-blocker，发布 ref 已核对")
    print("[部署动作与其他 Agent 提示]")
    for note in notes:
        print(f"  {note}")
    if issues:
        print("结论：发布阻断仍存在；关闭例外并重新核对 ref 后再部署。")
        return 1
    print("结论：无阻断项；其他 Agent 在途工作不会混入固定 ref 发布包。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

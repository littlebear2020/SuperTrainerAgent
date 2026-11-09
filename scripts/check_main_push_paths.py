#!/usr/bin/env python3
"""核心路径禁直推 main 门禁（2026-10-13 MA-P0 C10：P0-2 补偿控制第一层 + P0-3 机械牙齿）。

两种用法：
1. 本机 pre-push（pre-commit 调起，无参数）：读 PRE_COMMIT_* 环境变量——推 main 且 diff
   命中核心路径 → exit 1 阻断并提示走分支 + PR；推功能分支 / 未命中 → 放行。
   diff 计算失败 → 打印警告后放行（工具故障不堵死推送；CI 报告模式兜底）。
2. CI 报告模式：`--report <base> <head>`（main-push-guard.yml 在 push main 后调起）——
   命中打 ::warning:: 与摘要，恒 exit 0（事后报警，兜 --no-verify / 未装 hook 的绕过）。

清单 SoT = 本文件 CORE_PATH_PATTERNS；AGENTS.md「Git 约定」节镜像该清单，
两侧一致性由 check_docs_convention.py 校验项 19 兜底（改清单须两侧同改）。

匹配语义：尾 `/` = 目录前缀；含 `*` 的 = glob（`**/` 任意深度目录、`*` 段内）；
其余 = 精确匹配。glob 对路径的任一祖先前缀命中即算命中（gitignore 式目录语义：
`backend/app/**/auth*` 同时覆盖 `.../admin_auth.py` 与 `.../auth/` 整棵子树）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

CORE_PATH_PATTERNS = (
    "backend/app/**/auth*",
    "backend/app/**/*_auth*",
    "backend/app/**/middleware*",
    "backend/packages/harness/deerflow/**/middleware*",
    "backend/app/**/provider*",
    "backend/app/**/video_gen*",
    "config*.yaml",
    "docker/",
    "skills/",
    "backend/seed/agents/",
    "scripts/check_docs_convention.py",
    ".github/workflows/",
    "AGENTS.md",
)

BLOCK_MESSAGE = """已拦截：本次直推 main 命中核心路径清单（P0-3，2026-10-13 入约）：
{hits}
核心路径改动必须走分支 + PR + AI 评审——「≤5 文件小修直推」豁免不适用这些路径。
改法：git checkout -b <主题> 另起分支推送并开 PR；确属紧急直推须用户明确指示——
且 --no-verify 绕过也会被 main-push-guard.yml 事后报警记录。"""


def _glob_regex(pattern: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def _matches(pattern: str, path: str) -> bool:
    if pattern.endswith("/"):
        return path.startswith(pattern)
    if "*" not in pattern:
        return path == pattern
    regex = _glob_regex(pattern)
    parts = path.split("/")
    # gitignore 式语义：命中任一祖先前缀（即该 glob 命中了路径上的某个目录）也算命中
    return any(regex.fullmatch("/".join(parts[:k])) for k in range(1, len(parts) + 1))


def _git_diff_names(range_spec: str) -> list[str] | None:
    result = subprocess.run(
        ["git", "diff", "--name-only", range_spec],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def _hits(paths: list[str]) -> list[str]:
    return [p for p in paths if any(_matches(pattern, p) for pattern in CORE_PATH_PATTERNS)]


def _is_null_sha(value: str) -> bool:
    """全零 SHA（任意长度，含短形态）；空串 = 未注入，不算 null。"""
    return bool(value) and set(value) == {"0"}


def _report_mode(base: str, head: str) -> int:
    """CI 报告模式：恒 exit 0，命中打 ::warning::（事后报警，不阻断）。"""
    if _is_null_sha(base):
        print("main-push-guard：base 全零（首次推送/分支重建场景），跳过比对")
        return 0
    names = _git_diff_names(f"{base}...{head}")
    if names is None:
        print(f"::warning::main-push-guard：git diff {base}...{head} 计算失败，本路报警失效，请人工核对")
        return 0
    hits = _hits(names)
    if not hits:
        print(f"main-push-guard：本次 push 的 {len(names)} 个文件均未触及核心路径清单")
        return 0
    for path in hits:
        print(f"::warning::核心路径触碰：{path}")
    print(
        f"::warning::本次 push main 触及核心路径清单 {len(hits)} 个文件——若为已审 PR 合入请忽略；"
        f"若为直推请按 AGENTS.md「Git 约定」回滚或补审（P0-3）。命中：{', '.join(hits)}"
    )
    return 0


def _pre_push_mode() -> int:
    remote_branch = os.environ.get("PRE_COMMIT_REMOTE_BRANCH", "")
    if not remote_branch:
        # 环境变量缺失 = pre-commit 过旧（<3.2）或非 push 阶段——无法判定目标分支，
        # 放行但必须留痕（静默放行 = 第一层失效不可观测，PR#14 评审修复）
        print(
            "main-push-path-guard：未注入 PRE_COMMIT_REMOTE_BRANCH（pre-commit 过旧或非 push 调起）"
            "——无法判定目标分支，放行但请自查；建议 pre-commit>=3.2",
            file=sys.stderr,
        )
        return 0
    # 归一化为分支名末段（PR#14 复评修复：旧版 pre-commit 可能注入 origin/main 形态）
    if remote_branch.rsplit("/", 1)[-1] != "main":
        return 0  # 推功能分支放行
    # FROM_REF/TO_REF 为新名，ORIGIN/SOURCE 为 legacy 别名（pre-commit 4.6.0 run.py 两者都注入）
    remote_rev = os.environ.get("PRE_COMMIT_FROM_REF") or os.environ.get("PRE_COMMIT_ORIGIN", "")
    local_rev = os.environ.get("PRE_COMMIT_TO_REF") or os.environ.get("PRE_COMMIT_SOURCE") or "HEAD"
    if remote_rev and not _is_null_sha(remote_rev):
        range_spec = f"{remote_rev}..{local_rev}"
    else:
        range_spec = f"origin/main...{local_rev}"  # 远端无记录（首次/重建）时退回三叉点
    names = _git_diff_names(range_spec)
    if names is None:
        print(
            f"main-push-path-guard：git diff {range_spec} 计算失败——放行但请人工确认未碰核心路径",
            file=sys.stderr,
        )
        return 0
    hits = _hits(names)
    if not hits:
        return 0
    print(BLOCK_MESSAGE.format(hits="\n".join(f"  - {h}" for h in hits)), file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        # 恒 exit 0 契约：参数缺失/形态异常只报警不抛异常（job 红 ≠ 报警，PR#14 评审修复）
        if len(sys.argv) != 4:
            print("::warning::main-push-guard：--report 需要 <base> <head> 两个参数，本路报警跳过")
            return 0
        base, head = sys.argv[2], sys.argv[3]
        if not re.fullmatch(r"[0-9a-f]{7,40}", base) or not re.fullmatch(r"[0-9a-f]{7,40}", head):
            print(f"::warning::main-push-guard：SHA 形态异常（base={base!r} head={head!r}），本路报警跳过")
            return 0
        return _report_mode(base, head)
    return _pre_push_mode()


if __name__ == "__main__":
    sys.exit(main())

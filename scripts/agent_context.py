#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class ContextError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_output(root: Path, *args: str) -> str:
    result = _git(root, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ContextError(f"git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def _changed_paths(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ContextError(f"git status: {detail}")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise ContextError(f"无法解析 git status 条目：{entry!r}")
        status = entry[:2]
        paths.append(entry[3:])
        if "R" in status or "C" in status:
            index += 1
    return sorted(paths)


def _worktrees(root: Path) -> list[str]:
    output = _git_output(root, "worktree", "list", "--porcelain")
    return [line[9:] for line in output.splitlines() if line.startswith("worktree ")]


def _patch_equivalent(root: Path, base_ref: str, head_sha: str, base_sha: str) -> bool:
    if head_sha == base_sha:
        return True
    output = _git_output(root, "cherry", base_ref, head_sha)
    return not any(line.startswith("+") for line in output.splitlines())


def _spec_context(root: Path, spec: str) -> dict[str, str | None]:
    relative = Path(spec)
    if relative.is_absolute() or ".." in relative.parts:
        raise ContextError(f"计划路径必须是仓库内相对路径：{spec}")
    path = root / relative
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root) or not resolved_path.is_file():
        raise ContextError(f"计划文件不存在或越过仓库边界：{spec}")
    normalized = relative.as_posix()
    tree = _git(root, "ls-tree", "-z", "HEAD", "--", normalized)
    if tree.returncode != 0:
        raise ContextError(f"读取计划 blob 失败：{tree.stderr.strip()}")
    spec_blob_sha = None
    if tree.stdout:
        metadata, _ = tree.stdout.split("\t", 1)
        parts = metadata.split()
        if len(parts) < 3 or parts[1] != "blob":
            raise ContextError(f"计划路径不是 HEAD 中的文件：{spec}")
        spec_blob_sha = parts[2]
    return {
        "spec_path": normalized,
        "spec_blob_sha": spec_blob_sha,
        "spec_sha256": hashlib.sha256(resolved_path.read_bytes()).hexdigest(),
    }


def build_context(root: Path, base_ref: str, spec: str | None) -> dict:
    root = root.resolve()
    head_sha = _git_output(root, "rev-parse", "HEAD")
    base_sha = _git_output(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    counts = _git_output(root, "rev-list", "--left-right", "--count", f"{base_ref}...{head_sha}").split()
    if len(counts) != 2:
        raise ContextError("无法解析 ahead/behind")
    behind, ahead = (int(value) for value in counts)
    changed_paths = _changed_paths(root)
    context = {
        "head_sha": head_sha,
        "branch": _git_output(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "base_ref": base_ref,
        "base_sha": base_sha,
        "ahead": ahead,
        "behind": behind,
        "dirty": bool(changed_paths),
        "changed_paths": changed_paths,
        "worktrees": _worktrees(root),
        "patch_equivalent_to_base": _patch_equivalent(root, base_ref, head_sha, base_sha),
        "spec_path": None,
        "spec_blob_sha": None,
        "spec_sha256": None,
    }
    if spec is not None:
        context.update(_spec_context(root, spec))
    return context


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="输出 Agent 会话的代码、基准与计划基线")
    parser.add_argument("--base", default="origin/main", dest="base_ref")
    parser.add_argument("--spec")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-head", action="store_true")
    parser.add_argument("--expect-spec-sha256")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _print_human(context: dict) -> None:
    print(f"HEAD: {context['head_sha']} ({context['branch']})")
    print(f"base: {context['base_ref']} {context['base_sha']} ahead={context['ahead']} behind={context['behind']}")
    print(f"dirty: {str(context['dirty']).lower()}; changed_paths={len(context['changed_paths'])}")
    print(f"patch_equivalent_to_base: {str(context['patch_equivalent_to_base']).lower()}")
    print(f"worktrees: {len(context['worktrees'])}")
    if context["spec_path"] is not None:
        print(f"spec: {context['spec_path']} sha256={context['spec_sha256']} blob={context['spec_blob_sha']}")
    for path in context["changed_paths"]:
        print(f"  changed: {path}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.expect_spec_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expect_spec_sha256):
        print("agent_context: --expect-spec-sha256 必须是 64 位十六进制", file=sys.stderr)
        return 1
    if args.expect_spec_sha256 is not None and args.spec is None:
        print("agent_context: --expect-spec-sha256 必须同时指定 --spec", file=sys.stderr)
        return 1
    try:
        context = build_context(Path.cwd(), args.base_ref, args.spec)
        failures: list[str] = []
        if args.require_clean and context["dirty"]:
            failures.append("工作区不干净")
        if args.require_head and context["head_sha"] != context["base_sha"]:
            failures.append(f"HEAD 不等于 {args.base_ref}")
        if args.spec is not None and args.expect_spec_sha256 is not None:
            if context["spec_sha256"] != args.expect_spec_sha256.lower():
                failures.append("计划 SHA-256 与预期不符")
        if args.json_output:
            print(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_human(context)
        for failure in failures:
            print(f"agent_context: {failure}", file=sys.stderr)
        return 1 if failures else 0
    except (ContextError, OSError, ValueError) as exc:
        print(f"agent_context: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

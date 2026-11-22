#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

REQUIRED_CONFIG = ("config.yaml", ".env")
OPTIONAL_CONFIG = ("frontend/.env", "extensions_config.json")
MANIFEST_PATH = "release-manifest.json"


class ArchiveError(RuntimeError):
    pass


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _git_output(root: Path, *args: str) -> str:
    result = _git(root, *args)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ArchiveError(f"git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def _resolve_commit(root: Path, ref: str) -> str:
    return _git_output(root, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")


def _normalize_archive_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or value.startswith("/") or "\\" in value or ".." in path.parts:
        raise ArchiveError(f"非法仓库相对路径：{value!r}")
    normalized = "/".join(part for part in path.parts if part != ".").rstrip("/")
    if not normalized or normalized == ".git" or normalized.startswith(".git/"):
        raise ArchiveError(f"非法仓库相对路径：{value!r}")
    return normalized


def _is_excluded(path: str, excludes: Sequence[str]) -> bool:
    return any(path == excluded or path.startswith(f"{excluded}/") for excluded in excludes)


def _local_configs(root: Path, include: bool) -> list[tuple[str, Path]]:
    if not include:
        return []
    configs: list[tuple[str, Path]] = []
    for relative in (*REQUIRED_CONFIG, *OPTIONAL_CONFIG):
        required = relative in REQUIRED_CONFIG
        candidate = root / relative
        if not candidate.exists() and not required:
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            raise ArchiveError(f"本地配置缺失或越过仓库边界：{relative}")
        configs.append((relative, resolved))
    return configs


def _tar_info(name: str, size: int, mtime: int, mode: int = 0o644) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = mtime
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_archive(
    root: Path,
    ref: str,
    output: Path,
    *,
    base_ref: str = "origin/main",
    allow_unmerged: bool = False,
    include_local_config: bool = False,
    excludes: Sequence[str] = (),
) -> dict:
    root = root.resolve()
    source_sha = _resolve_commit(root, ref)
    base_sha = _resolve_commit(root, base_ref)
    if not allow_unmerged:
        ancestry = _git(root, "merge-base", "--is-ancestor", source_sha, base_sha)
        if ancestry.returncode == 1:
            raise ArchiveError(f"ref {ref!r} 未合入 {base_ref!r}；仅测试发布可显式 --allow-unmerged")
        if ancestry.returncode != 0:
            raise ArchiveError(f"无法验证 ref 祖先关系：{ancestry.stderr.strip()}")
    normalized_excludes = [_normalize_archive_path(value) for value in excludes]
    configs = _local_configs(root, include_local_config)
    config_paths = {name for name, _ in configs}
    output = output if output.is_absolute() else Path.cwd() / output
    output_parent = output.parent.resolve()
    if not output_parent.is_dir():
        raise ArchiveError(f"输出目录不存在：{output.parent}")
    output = output_parent / output.name
    commit_time = int(_git_output(root, "show", "-s", "--format=%ct", source_sha))
    manifest = {
        "source_sha": source_sha,
        "requested_ref": ref,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "excludes": normalized_excludes,
        "includes_local_config": include_local_config,
        "config_files": [name for name, _ in configs],
    }
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output_parent)
    os.close(fd)
    temporary = Path(temporary_name)
    process: subprocess.Popen[bytes] | None = None
    stderr_file = None
    try:
        stderr_file = tempfile.TemporaryFile()
        process = subprocess.Popen(
            ["git", "archive", "--format=tar", source_sha],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
        )
        assert process.stdout is not None
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=commit_time) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as target:
                    with tarfile.open(fileobj=process.stdout, mode="r|") as source:
                        for member in source:
                            name = _normalize_archive_path(member.name)
                            if _is_excluded(name, normalized_excludes) or name in config_paths or name == MANIFEST_PATH:
                                continue
                            target.addfile(member, source.extractfile(member) if member.isfile() else None)
                    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
                    target.addfile(_tar_info(MANIFEST_PATH, len(manifest_bytes), commit_time), io.BytesIO(manifest_bytes))
                    for name, path in configs:
                        data = path.read_bytes()
                        target.addfile(
                            _tar_info(name, len(data), commit_time, stat.S_IMODE(path.stat().st_mode)),
                            io.BytesIO(data),
                        )
        stderr_file.seek(0)
        stderr = stderr_file.read().decode(errors="replace").strip()
        returncode = process.wait()
        if returncode != 0:
            raise ArchiveError(f"git archive 失败：{stderr or returncode}")
        os.replace(temporary, output)
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if stderr_file is not None:
            stderr_file.close()
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从不可变 Git 提交生成带 manifest 的发布包")
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--allow-unmerged", action="store_true")
    parser.add_argument("--include-local-config", action="store_true")
    parser.add_argument("--exclude", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = build_archive(
            Path.cwd(),
            args.ref,
            args.output,
            base_ref=args.base_ref,
            allow_unmerged=args.allow_unmerged,
            include_local_config=args.include_local_config,
            excludes=args.exclude,
        )
    except (ArchiveError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"release_archive: {exc}", file=sys.stderr)
        return 1
    print(f"release_archive: {args.output} source_sha={manifest['source_sha']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

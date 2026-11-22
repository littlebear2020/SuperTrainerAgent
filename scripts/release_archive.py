#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import os
import re
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


LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


def _lfs_paths(root: Path, source_sha: str) -> set[str]:
    tracked = _git(root, "ls-tree", "-r", "-z", "--name-only", source_sha)
    if tracked.returncode != 0:
        raise ArchiveError(f"读取 Git 文件清单失败：{tracked.stderr.strip()}")
    result = subprocess.run(
        ["git", "check-attr", "-z", f"--source={source_sha}", "--stdin", "filter"],
        cwd=root,
        input=tracked.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ArchiveError(f"读取 LFS 属性失败：{result.stderr.strip()}")
    fields = result.stdout.split("\0")
    if fields[-1]:
        raise ArchiveError("git check-attr 返回了未终止的记录")
    paths: set[str] = set()
    for index in range(0, len(fields) - 1, 3):
        if index + 2 >= len(fields):
            raise ArchiveError("git check-attr 返回了不完整的三元组")
        if fields[index + 2] == "lfs":
            paths.add(fields[index])
    return paths


def _lfs_replacement(root: Path, name: str, member: tarfile.TarInfo, pointer: bytes):
    if len(pointer) > 64 * 1024:
        raise ArchiveError(f"LFS 指针异常过大：{name}")
    text = pointer.decode("ascii")
    oid_match = re.search(r"^oid sha256:([0-9a-f]{64})$", text, re.M)
    size_match = re.search(r"^size (\d+)$", text, re.M)
    if oid_match is None or size_match is None:
        raise ArchiveError(f"LFS 指针缺少 oid/size：{name}")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ArchiveError(f"LFS 对象未物化：{name}；请先 git lfs checkout")
    expected_oid = oid_match.group(1)
    expected_size = int(size_match.group(1))
    if path.stat().st_size != expected_size:
        raise ArchiveError(f"LFS 对象大小与指针不符：{name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_oid:
        raise ArchiveError(f"LFS 对象 SHA-256 与指针不符：{name}")
    info = copy.copy(member)
    info.size = expected_size
    return info, path


def _add_source_member(root: Path, member: tarfile.TarInfo, source: tarfile.TarFile, target: tarfile.TarFile, lfs_paths: set[str]) -> None:
    if not member.isfile():
        target.addfile(member)
        return
    stream = source.extractfile(member)
    if stream is None:
        raise ArchiveError(f"无法读取归档成员：{member.name}")
    if member.name not in lfs_paths:
        target.addfile(member, stream)
        return
    if member.size > 64 * 1024:
        raise ArchiveError(f"LFS 指针异常过大：{member.name}")
    pointer = stream.read()
    if not pointer.startswith(LFS_POINTER_PREFIX):
        raise ArchiveError(f"LFS 文件未以指针形式导出：{member.name}")
    info, path = _lfs_replacement(root, member.name, member, pointer)
    with path.open("rb") as replacement:
        target.addfile(info, replacement)


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
        "contains_sensitive_material": include_local_config,
        "config_files": [name for name, _ in configs],
    }
    lfs_paths = _lfs_paths(root, source_sha)
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
            env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"},
        )
        if process.stdout is None:
            raise ArchiveError("git archive 未返回输出管道")
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=commit_time) as compressed:
                with tarfile.open(fileobj=compressed, mode="w|") as target:
                    with tarfile.open(fileobj=process.stdout, mode="r|") as source:
                        for member in source:
                            name = _normalize_archive_path(member.name)
                            if _is_excluded(name, normalized_excludes) or name in config_paths or name == MANIFEST_PATH:
                                continue
                            _add_source_member(root, member, source, target, lfs_paths)
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
        process.stdout.close()
        returncode = process.wait()
        if returncode != 0:
            raise ArchiveError(f"git archive 失败：{stderr or returncode}")
        os.replace(temporary, output)
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        temporary.unlink(missing_ok=True)
        if stderr_file is not None:
            stderr_file.seek(0)
            detail = stderr_file.read().decode(errors="replace").strip()
            if process is not None and process.returncode not in (None, 0) and detail:
                raise ArchiveError(f"git archive 失败：{detail}") from exc
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
    parser.add_argument("--exclude", action="append", default=None)
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
            excludes=args.exclude or [],
        )
    except (ArchiveError, OSError, ValueError, tarfile.TarError) as exc:
        print(f"release_archive: {exc}", file=sys.stderr)
        return 1
    print(f"release_archive: {args.output} source_sha={manifest['source_sha']}")
    if args.include_local_config:
        print("release_archive: 警告：包内含本地配置，请限制存放并在部署后删除", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

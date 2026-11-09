"""ppt-master vendored 基准树 manifest 生成/校验（2026-09-12 缺陷批条目 2）。

用途：为 TRAINERAI_PATCHES.md 的 `manifest_sha256` 升级锚点提供可复现的
生成与校验工具。算法（钉死，勿改——改了所有历史 pinned 值全部作废）：

  1. 逐文件计算 sha256 hex；
  2. 每个文件产出条目 `relpath + hex`（relpath = 相对 root 的 POSIX 路径，
     Unicode NFC 归一化）；
  3. 条目按字节序（LC_ALL=C 等价）排序后串接；
  4. 对串接后的字节流取 sha256，即 manifest。

排除项：任何路径段为 `__pycache__` 的文件（vendored 树含 .pyc 噪声，
基准树纯净不影响）。

两套 manifest 语义分清（勿混）：
- **基准树**：上游 ppt-master 原始解压树（如
  `/Users/littlebear/Documents/ppt-master-main-9.8/skills/ppt-master`），
  用于升级 diff 对比，pinned 值见 TRAINERAI_PATCHES.md；
- **vendored 树**：仓库 `skills/public/ppt-master/`（上游 + 已声明补丁），
  仅可用本脚本生成快照，不与基准树 pinned 值比对。

用法：
  # 生成
  uv run python scripts/ppt_master_manifest.py /path/to/ppt-master
  # 校验（exit 0 = 匹配）
  uv run python scripts/ppt_master_manifest.py /path/to/ppt-master \
      --check 363cd587f6fdf0a3401c03907da57319083077694ae5fefddefa8f3756e9120c
  # 上游 zip 摘要（sha256/字节数/条目数；计数走 zipfile API，勿解析 unzip -l 文本）
  uv run python scripts/ppt_master_manifest.py --zip /path/to/ppt-master-main.zip

上游 zip 档案（6.3.0，2026-09-08 下载）：
  sha256 = e047146e5d479aca2f9d6b5efb269267fef2f4c8f7933419bfc174d5354868d1
  大小   = 92,569,685 B
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import unicodedata
import zipfile
from pathlib import Path

UPSTREAM_MANIFEST_SHA256 = "363cd587f6fdf0a3401c03907da57319083077694ae5fefddefa8f3756e9120c"
UPSTREAM_ZIP_SHA256 = "e047146e5d479aca2f9d6b5efb269267fef2f4c8f7933419bfc174d5354868d1"

EXCLUDED_DIR_NAMES = {"__pycache__"}


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def compute_manifest(root: Path) -> tuple[str, int]:
    """返回 (manifest_sha256, 文件数)。算法见模块 docstring，勿改。"""
    entries: list[str] = []
    for path in _iter_files(root):
        relpath = unicodedata.normalize("NFC", path.relative_to(root).as_posix())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{relpath}{digest}")
    # 按字节序排序（LC_ALL=C 等价），再串接取总 sha256
    entries.sort(key=lambda entry: entry.encode("utf-8"))
    blob = "".join(entries).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), len(entries)


def zip_digest(zip_path: Path) -> dict[str, object]:
    """zip 摘要：文件 sha256 / 字节数 / 条目数（zipfile API，非文本解析）。"""
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    size = zip_path.stat().st_size
    with zipfile.ZipFile(zip_path) as zf:
        count = sum(1 for info in zf.infolist() if not info.is_dir() and not any(part in EXCLUDED_DIR_NAMES for part in Path(info.filename).parts))
    return {"sha256": sha, "bytes": size, "entries": count}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", type=Path, help="基准树或 vendored 树根目录")
    parser.add_argument(
        "--check",
        metavar="EXPECTED_SHA256",
        help="校验模式：manifest 与期望值相等 exit 0，否则 exit 1",
    )
    parser.add_argument(
        "--zip",
        metavar="ZIP_PATH",
        type=Path,
        help="输出 zip 摘要（sha256/字节数/条目数）后退出，不走树扫描",
    )
    args = parser.parse_args(argv)

    if args.zip:
        info = zip_digest(args.zip)
        print(f"zip sha256 : {info['sha256']}")
        print(f"zip bytes  : {info['bytes']:,}")
        print(f"zip entries: {info['entries']:,}")
        return 0

    if not args.root:
        parser.error("需要 root 目录（或 --zip）")
    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: {root} 不是目录", file=sys.stderr)
        return 2

    manifest, file_count = compute_manifest(root)
    print(f"root       : {root}")
    print(f"files      : {file_count:,}")
    print(f"manifest   : {manifest}")

    if args.check:
        expected = args.check.strip().lower()
        if manifest == expected:
            print("check      : OK")
            return 0
        print(f"check      : MISMATCH (expected {expected})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

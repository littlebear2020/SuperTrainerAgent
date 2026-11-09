#!/usr/bin/env python3
"""docs/ 月度归档 + 常青复核提醒（2026-10-08 docs-system-optimization 批 4 D1，AGENTS.md「文档治理」兜底 CI 的手工替代）。

职责三连：
1. 归档：docs/ 根目录上月及更早的 dated 文件/目录（YYYY-MM-DD-* / YYYY-MM-*）→ docs/archive/YYYY-MM/
   （plans/、changelog/ 专目录不动——约定；archive/ 不动）。
2. 自验：归档后跑 check_docs_convention.py，违例即报告并退出非零。
3. 常青复核提醒：对「文档 → 代码路径」映射表中，上次归档月以来自身代码有改动的文档，
   输出「建议复核清单」——只提醒，不阻断。

用法：python3 scripts/archive_docs.py [--apply]（默认 --dry-run，只打印不移动）。
幂等：重复运行无操作即成功。
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-\d{2}")
MONTH_DIR_RE = re.compile(r"^(\d{4})-(\d{2})$")
# 与 check_docs_convention.py 的 ALLOWED_ROOT_DIRS 保持一致（专目录不动）
PROTECTED_DIRS = {"archive", "changelog", "plans"}

# 常青文档 → 关联代码路径（复核提醒用；初版小表，失配只漏提醒不阻断）
EVERGREEN_CODE_MAP: dict[str, list[str]] = {
    "task-pipeline.md": ["backend/app/gateway/_shared/task_framework", "docker/production-worker"],
    "remotion-course-pipeline.md": ["backend/app/gateway/video_gen", "video-templates/course-standard", "docker/production-worker"],
    "architecture-overview.md": ["docker", "backend/app/gateway/app.py"],
    "company-server-deployment-guide.md": ["docker/docker-compose-deploy.yaml"],
    "production-server-ops-manual.md": ["docker/docker-compose-deploy.yaml", "docker/production-worker"],
    "personal-server-deployment-guide.md": ["docker/docker-compose-deploy.yaml"],
    "360-api-reference.md": ["backend/app/gateway/video_gen/providers"],
}


def last_archive_month() -> tuple[int, int] | None:
    """最近一次归档的月份（archive/ 下最新 YYYY-MM 目录）。"""
    archive = DOCS / "archive"
    if not archive.is_dir():
        return None
    months = sorted(
        (int(m.group(1)), int(m.group(2)))
        for d in archive.iterdir()
        if d.is_dir() and (m := MONTH_DIR_RE.match(d.name))
    )
    return months[-1] if months else None


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    current = (date.today().year, date.today().month)

    # 1. 归档
    moves: list[tuple[Path, Path]] = []
    for entry in sorted(DOCS.iterdir()):
        if entry.name.startswith(".") or entry.name in PROTECTED_DIRS:
            continue
        m = DATE_RE.match(entry.name) or MONTH_DIR_RE.match(entry.name)
        if not m:
            continue  # 常青文档/无日期条目不管
        month = (int(m.group(1)), int(m.group(2)))
        if month >= current:
            continue
        moves.append((entry, DOCS / "archive" / f"{month[0]:04d}-{month[1]:02d}" / entry.name))

    if not moves:
        print("归档：无上月及更早的 dated 文件/目录，无操作")
    for src, dst in moves:
        print(f"{'[APPLY]' if apply else '[dry-run]'} git mv {src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)}")
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            git("mv", str(src.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT)))

    # 2. 自验
    if apply:
        rc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "check_docs_convention.py")], cwd=REPO_ROOT).returncode
        if rc != 0:
            print("FAIL 归档后 check_docs_convention 有违例", file=sys.stderr)
            return rc

    # 3. 常青复核提醒（只提醒，不阻断）
    last = last_archive_month()
    since = f"{last[0]:04d}-{last[1]:02d}-01" if last else "1970-01-01"
    suggestions: list[str] = []
    for doc, paths in EVERGREEN_CODE_MAP.items():
        doc_path = DOCS / doc
        if not doc_path.is_file():
            continue
        # 文档创建日晚于基线（如刚新增的文档）不提醒——防「创建当天就被要求复核」的狼来了噪音
        created = git("log", "--diff-filter=A", "--follow", "-1", "--format=%cs", "--", str(doc_path.relative_to(REPO_ROOT))).strip()
        if created and created >= since:
            continue
        changed = [p for p in paths if git("log", f"--since={since}", "--oneline", "--", p).strip()]
        if changed:
            suggestions.append(f"  - docs/{doc}：上次归档月（{since}）以来关联代码有改动：{'、'.join(changed)}")
    if suggestions:
        print("\n常青复核提醒（建议复核清单，非阻断）：")
        print("\n".join(suggestions))
    else:
        print("\n常青复核：无关联代码改动，无需复核")

    if not apply and moves:
        print(f"\ndry-run 完成：{len(moves)} 项待归档。确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

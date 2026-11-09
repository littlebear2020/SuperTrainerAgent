#!/usr/bin/env python3
"""Migrate per-agent memory of retired agents into their successor agent.

Retired agents keep their memory as plain JSON files. This script merges them
per user into the successor defined by ``MIGRATION_TARGETS`` (2026-09-09 创意
合并: card-poster/case-article/design-specialist/knowledge-extraction/video-clip
→ ``creative-studio``；2026-09-11 退役: course-ppt → ``ppt-master``). Each
``(user, target)`` pair gets its own merge+archive pass in a single run:

    {base}/users/{user_id}/agents/{retired}/memory.json
        -> merged into {base}/users/{user_id}/agents/{target}/memory.json

Legacy top-level files ({base}/agents/{retired}/memory.json) are only archived,
NOT merged: every production read/write path resolves user_id via
``get_effective_user_id()`` which never returns None (falls back to "default"),
so the legacy ``{base}/agents/...`` branch of
``FileMemoryStorage._get_memory_file_path`` is unreachable. See
backend/packages/harness/deerflow/agents/memory/storage.py,
backend/packages/harness/deerflow/runtime/user_context.py.

Merge semantics:
  - facts: concatenated and de-duplicated by ``id`` (later duplicates dropped,
    including duplicates against facts already in the target file).
  - single-value sections (user.workContext / personalContext / topOfMind and
    history.recentMonths / earlierContext / longTermBackground): the non-empty
    candidate with the newest file-level ``lastUpdated`` wins.
  - ``lastUpdated`` of the merged file is refreshed to the run time.

Idempotency: a source file is archived next to itself as
``memory.json.pre-creative-merge.bak`` only AFTER its user's merged target has
been written successfully (merging itself is idempotent: facts de-duplicated by
``id``, single-value sections won by newest ``lastUpdated``). A source whose
archive already exists was fully migrated by a previous run and is skipped. If
a run dies mid-merge, unarchived sources are simply re-merged on the next run —
archives are never created for work that was not completed, so the
"already migrated" log line can never lie.

Corrupt source JSON files are skipped with a warning (no archive is created,
so they can be fixed and reprocessed); the exit code stays 0 and the
reconciliation report carries a corrupt count. Non-dict fact entries are
dropped during merge and counted separately as ``invalid_facts_dropped`` —
they are NOT lumped into ``duplicate_facts_dropped`` (which counts true ``id``
duplicates only).

Pure stdlib; run with any python3:

    python3 migrate_retired_agent_memory.py [--base-dir PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("migrate_retired_agent_memory")

# 2026-09-11 ppt-master 6.3.0 批次：course-ppt 完全退役 → ppt-master。
# 泛化为 retired → (target, archive_suffix) 映射；creative 五壳沿用旧后缀保
# 旧批归档幂等（archive 存在即跳过的判定依赖同后缀）。
MIGRATION_TARGETS: dict[str, tuple[str, str]] = {
    "card-poster": ("creative-studio", ".pre-creative-merge.bak"),
    "case-article": ("creative-studio", ".pre-creative-merge.bak"),
    "design-specialist": ("creative-studio", ".pre-creative-merge.bak"),
    "knowledge-extraction": ("creative-studio", ".pre-creative-merge.bak"),
    "video-clip": ("creative-studio", ".pre-creative-merge.bak"),
    "course-ppt": ("ppt-master", ".pre-course-ppt-retire.bak"),
}
RETIRED_AGENTS = list(MIGRATION_TARGETS)
TARGET_AGENT = "creative-studio"  # 兼容旧引用（报告文案）；实际 target 按 pair 取
ARCHIVE_SUFFIX = ".pre-creative-merge.bak"  # 兼容旧引用；实际后缀按 pair 取

# Single-value sections: (top-level key, section key). Each section is a
# {"summary": str, "updatedAt": str} object in the memory schema
# (see deerflow.agents.memory.storage.create_empty_memory).
SINGLE_VALUE_SECTIONS = [
    ("user", "workContext"),
    ("user", "personalContext"),
    ("user", "topOfMind"),
    ("history", "recentMonths"),
    ("history", "earlierContext"),
    ("history", "longTermBackground"),
]


def utc_now_iso_z() -> str:
    """Current UTC time as ISO-8601 with ``Z`` suffix (matches storage.py)."""
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"


def default_base_dir() -> Path:
    """Repo-root-relative default: <repo>/backend/.deer-flow."""
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "backend" / ".deer-flow"


def parse_last_updated(value: Any) -> datetime:
    """Parse a file-level ``lastUpdated``; unparseable/empty sorts oldest.

    Naive values are treated as UTC: storage.py historically wrote naive-UTC
    (its ``utc_now_iso_z`` docstring documents the format change), and retired
    agents' memory files are exactly the ones old enough to still carry it.
    Without normalization, comparing a naive stamp against an aware one in
    ``pick_section`` raises ``TypeError``.
    """
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return datetime.min.replace(tzinfo=UTC)


class SourceFile:
    """One retired-agent memory.json discovered on disk."""

    def __init__(self, path: Path, user_id: str | None, agent_name: str, legacy: bool) -> None:
        self.path = path
        self.user_id = user_id
        self.agent_name = agent_name
        self.legacy = legacy
        self.data: dict[str, Any] | None = None
        self.corrupt = False
        self.already_archived = False

    @property
    def archive_path(self) -> Path:
        _, suffix = MIGRATION_TARGETS.get(self.agent_name, (TARGET_AGENT, ARCHIVE_SUFFIX))
        return self.path.with_name(self.path.name + suffix)

    def load(self) -> None:
        """Parse the JSON body; mark corrupt instead of raising."""
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("top-level value is not a JSON object")
            self.data = data
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning("Skipping corrupt memory file %s: %s", self.path, e)
            self.corrupt = True

    def fact_count(self) -> int:
        facts = (self.data or {}).get("facts")
        return len(facts) if isinstance(facts, list) else 0


def discover_sources(base_dir: Path) -> list[SourceFile]:
    """Find retired-agent memory files in the users tree and the legacy tree."""
    sources: list[SourceFile] = []

    users_dir = base_dir / "users"
    if users_dir.is_dir():
        for user_dir in sorted(p for p in users_dir.iterdir() if p.is_dir()):
            agents_dir = user_dir / "agents"
            if not agents_dir.is_dir():
                continue
            for agent in RETIRED_AGENTS:
                memory_file = agents_dir / agent / "memory.json"
                if memory_file.is_file():
                    sources.append(SourceFile(memory_file, user_dir.name, agent, legacy=False))

    legacy_agents_dir = base_dir / "agents"
    if legacy_agents_dir.is_dir():
        for agent in RETIRED_AGENTS:
            memory_file = legacy_agents_dir / agent / "memory.json"
            if memory_file.is_file():
                sources.append(SourceFile(memory_file, None, agent, legacy=True))

    return sources


def pick_section(candidates: list[tuple[dict[str, Any], datetime]]) -> dict[str, Any]:
    """Pick the winning {"summary", "updatedAt"} section.

    Non-empty summaries beat empty ones; ties break on the newest file-level
    ``lastUpdated`` (per migration spec).
    """
    best: dict[str, Any] | None = None
    best_key: tuple[bool, datetime] | None = None
    for section, file_last_updated in candidates:
        summary = section.get("summary") if isinstance(section, dict) else None
        key = (bool(isinstance(summary, str) and summary.strip()), file_last_updated)
        if best_key is None or key > best_key:
            best_key = key
            best = section if isinstance(section, dict) else None
    if best is None:
        return {"summary": "", "updatedAt": ""}
    return {"summary": best.get("summary", ""), "updatedAt": best.get("updatedAt", "")}


def merge_into_target(
    target: dict[str, Any] | None,
    sources: list[SourceFile],
    run_time: str,
    stats: dict[str, int],
) -> dict[str, Any]:
    """Merge source memories into the (possibly pre-existing) target memory.

    ``stats["invalid_facts_dropped"]`` accumulates non-dict fact entries
    skipped during the merge (kept separate from true id-duplicate drops).
    """
    merged: dict[str, Any] = target if target is not None else {}
    if not isinstance(merged.get("user"), dict):
        merged["user"] = {}
    if not isinstance(merged.get("history"), dict):
        merged["history"] = {}

    # Facts: target facts first, then source facts in discovery order,
    # de-duplicated by id. Facts without an id cannot be deduped and are kept.
    merged_facts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for fact in merged.get("facts") or []:
        if not isinstance(fact, dict):
            stats["invalid_facts_dropped"] += 1
            continue
        fact_id = fact.get("id")
        if isinstance(fact_id, str):
            if fact_id in seen_ids:
                continue
            seen_ids.add(fact_id)
        merged_facts.append(fact)
    for source in sources:
        for fact in (source.data or {}).get("facts") or []:
            if not isinstance(fact, dict):
                stats["invalid_facts_dropped"] += 1
                continue
            fact_id = fact.get("id")
            if isinstance(fact_id, str):
                if fact_id in seen_ids:
                    continue
                seen_ids.add(fact_id)
            merged_facts.append(fact)
    merged["facts"] = merged_facts

    # Single-value sections: target competes with sources.
    target_last_updated = parse_last_updated(target.get("lastUpdated")) if target else None
    for top_key, section_key in SINGLE_VALUE_SECTIONS:
        candidates: list[tuple[dict[str, Any], datetime]] = []
        existing = merged[top_key].get(section_key)
        if isinstance(existing, dict) and target_last_updated is not None:
            candidates.append((existing, target_last_updated))
        for source in sources:
            section = (source.data or {}).get(top_key, {}).get(section_key)
            if isinstance(section, dict):
                candidates.append((section, parse_last_updated((source.data or {}).get("lastUpdated"))))
        merged[top_key][section_key] = pick_section(candidates)

    merged["version"] = merged.get("version") or "1.0"
    merged["lastUpdated"] = run_time
    return merged


def write_memory_file(path: Path, data: dict[str, Any]) -> None:
    """Write memory JSON the same way storage.py does (indent=2, utf-8, atomic rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f".{datetime.now(UTC).timestamp():.6f}.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)


def load_target(path: Path) -> dict[str, Any] | None:
    """Load an existing target memory file; corrupt targets abort the run."""
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("top-level value is not a JSON object")
        return data
    except (json.JSONDecodeError, OSError, ValueError) as e:
        raise RuntimeError(f"existing target {path} is not parseable ({e}); refusing to overwrite it") from e


def print_dry_run(base_dir: Path, sources: list[SourceFile]) -> None:
    """Read-only census: source files, per-agent fact counts, totals."""
    print(f"[dry-run] base dir: {base_dir}")
    print(f"[dry-run] source files found: {len(sources)} (users tree: {sum(1 for s in sources if not s.legacy)}, legacy tree: {sum(1 for s in sources if s.legacy)})")

    per_agent: dict[str, dict[str, int]] = {}
    total_facts = 0
    corrupt = 0
    skipped_archived = 0
    for source in sources:
        if source.already_archived:
            skipped_archived += 1
        if source.corrupt:
            corrupt += 1
            continue
        count = source.fact_count()
        total_facts += count
        bucket = per_agent.setdefault(source.agent_name, {"files": 0, "facts": 0})
        bucket["files"] += 1
        bucket["facts"] += count

    print("[dry-run] per-agent facts (parseable files):")
    for agent in RETIRED_AGENTS:
        bucket = per_agent.get(agent, {"files": 0, "facts": 0})
        print(f"  {agent}: {bucket['files']} file(s), {bucket['facts']} fact(s)")
    print(f"[dry-run] total facts across parseable sources: {total_facts}")
    if corrupt:
        print(f"[dry-run] corrupt files (would be skipped): {corrupt}")
    if skipped_archived:
        print(f"[dry-run] sources with existing {ARCHIVE_SUFFIX} archive (would be skipped as already migrated): {skipped_archived}")
    legacy_sources = [s for s in sources if s.legacy]
    if legacy_sources:
        print(f"[dry-run] legacy-tree sources are archived only, NOT merged (read side always resolves a users-tree path): {len(legacy_sources)}")
    print("[dry-run] no files were modified.")


def run_migration(base_dir: Path, sources: list[SourceFile]) -> int:
    """Archive, merge, and print reconciliation. Returns the process exit code."""
    run_time = utc_now_iso_z()

    # Group mergeable (non-legacy, non-corrupt, not-yet-archived) sources by user.
    # Legacy sources are archive-only (no merge step depends on them), so they are
    # archived right away; users-tree sources are archived ONLY after their user's
    # merged target has been written — a crash mid-merge then simply re-merges them
    # on the next run instead of silently skipping them as "already migrated".
    # 按 (user, target) 全组合各走一轮合并+归档：同一 user 的多组 target（如既有
    # creative 壳又有 course-ppt）单次运行全部迁完。修前循环头只按 user 取
    # srcs[0] 的 target，其余 target 的源既不合并也不归档，要重跑才自愈——
    # 而 runbook 是一次性执行，漏迁组在 reconciliation 报告里无任何提示。
    by_user: dict[str, list[SourceFile]] = {}
    archived_created: list[Path] = []
    archived_preexisting: list[Path] = []
    corrupt_sources: list[SourceFile] = []
    legacy_archived_only: list[SourceFile] = []
    duplicate_facts_dropped = 0
    stats = {"invalid_facts_dropped": 0}
    per_user_report: list[dict[str, Any]] = []

    for source in sources:
        if source.corrupt:
            corrupt_sources.append(source)
            continue
        if source.archive_path.exists():
            archived_preexisting.append(source.archive_path)
            continue
        if source.legacy:
            shutil.copy2(source.path, source.archive_path)
            archived_created.append(source.archive_path)
            legacy_archived_only.append(source)
            continue
        by_user.setdefault(source.user_id or "", []).append(source)

    for user_id, target_agent in sorted({(uid, MIGRATION_TARGETS[s.agent_name][0]) for uid, srcs in by_user.items() for s in srcs}):
        # 每个 (user, target) 对都来自至少一个实际源（推导式按源展开），
        # 过滤结果非空无需守卫
        user_sources = sorted(
            [src for src in by_user[user_id] if MIGRATION_TARGETS[src.agent_name][0] == target_agent],
            key=lambda s: (s.agent_name, str(s.path)),
        )
        target_path = base_dir / "users" / user_id / "agents" / target_agent / "memory.json"
        try:
            existing_target = load_target(target_path)
        except RuntimeError as e:
            # 本 user 的源此时均未归档 → 修复目标文件后重跑可完整合并。
            logger.error("%s", e)
            return 1

        facts_before = len((existing_target or {}).get("facts") or [])
        source_fact_total = sum(s.fact_count() for s in user_sources)
        invalid_before = stats["invalid_facts_dropped"]
        merged = merge_into_target(existing_target, user_sources, run_time, stats)
        facts_after = len(merged["facts"])
        # 守恒式：before + sources = after + 真重复丢弃 + 非 dict 无效丢弃
        invalid_this_user = stats["invalid_facts_dropped"] - invalid_before
        duplicate_facts_dropped += facts_before + source_fact_total - facts_after - invalid_this_user
        write_memory_file(target_path, merged)
        # 归档只在合并产物写盘成功后（问题 1b 修复：先合并后归档）
        for s in user_sources:
            shutil.copy2(s.path, s.archive_path)
            archived_created.append(s.archive_path)
        per_user_report.append(
            {
                "user_id": user_id,
                "sources": [f"{s.agent_name} ({s.fact_count()} facts)" for s in user_sources],
                "facts_before": facts_before,
                "facts_added": facts_after - facts_before,
                "facts_after": facts_after,
                "target": target_path,
            }
        )

    # Reconciliation report.
    print("=== reconciliation ===")
    print(f"base dir: {base_dir}")
    print(f"run time: {run_time}")
    users_sources = [s for s in sources if not s.legacy]
    legacy_sources = [s for s in sources if s.legacy]
    print(f"source files discovered: {len(sources)} (users tree: {len(users_sources)}, legacy tree: {len(legacy_sources)})")
    print(f"corrupt source files skipped (not archived, not merged): {len(corrupt_sources)}")
    for source in corrupt_sources:
        print(f"  corrupt: {source.path}")
    print(f"archives created this run: {len(archived_created)}")
    for path in archived_created:
        print(f"  archived: {path}")
    if archived_preexisting:
        print(f"sources skipped because archive already exists (already migrated): {len(archived_preexisting)}")
        for path in archived_preexisting:
            print(f"  pre-existing archive: {path}")
    if legacy_archived_only:
        print(f"legacy-tree sources archived but NOT merged (read side only resolves users-tree paths): {len(legacy_archived_only)}")
        for source in legacy_archived_only:
            print(f"  legacy not merged: {source.path}")
    print(f"target files written: {len(per_user_report)}")
    for report in per_user_report:
        print(f"  user {report['user_id']}:")
        print(f"    target: {report['target']}")
        print(f"    sources merged: {len(report['sources'])} -> {', '.join(report['sources'])}")
        print(f"    facts: {report['facts_before']} pre-existing + merged = {report['facts_after']} (added {report['facts_added']})")
    print(f"duplicate facts dropped (by id, incl. against pre-existing target facts): {duplicate_facts_dropped}")
    print(f"invalid (non-dict) fact entries dropped during merge: {stats['invalid_facts_dropped']}")
    print("=== done ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Merge retired agents' per-agent memory files into creative-studio memory.",
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=default_base_dir(),
        help="Memory root directory (default: <repo>/backend/.deer-flow).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only census: print discovered sources and fact counts without modifying anything.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    base_dir = args.base_dir.resolve()
    if not base_dir.is_dir():
        print(f"error: base dir does not exist: {base_dir}", file=sys.stderr)
        return 1

    sources = discover_sources(base_dir)
    for source in sources:
        source.already_archived = source.archive_path.exists()
        source.load()

    if args.dry_run:
        print_dry_run(base_dir, sources)
        return 0
    return run_migration(base_dir, sources)


if __name__ == "__main__":
    sys.exit(main())

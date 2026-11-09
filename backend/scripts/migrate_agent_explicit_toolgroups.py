"""One-time migration: make legacy custom agents' tool_groups/skills explicit.

SEC-C3/M14 (安全加固批次 2 项 2.1): custom agent configs written before the
write-side hardening (536b2f392) may lack the ``tool_groups``/``skills`` keys.
At runtime those load as ``None`` = "all tool groups"/"all enabled skills",
which over-grants for user-created agents. The runtime now converges null
values (load_agent_config + lead_agent hard gate); this script materializes
the same explicit values into the on-disk ``config.yaml`` files so the config
surface, backups and future audits see them.

Usage (from backend/):
    uv run python scripts/migrate_agent_explicit_toolgroups.py [--dry-run]
    uv run python scripts/migrate_agent_explicit_toolgroups.py --undo <snapshot-dir>

Behavior:
- Scans ``.deer-flow/agents/*/config.yaml`` (agent names must match
  ``^[A-Za-z0-9-]+$`` — ``.bak.<ts>`` directories are skipped, same as
  ``list_custom_agents``).
- Missing or null ``tool_groups`` -> ``["web"]``; missing or null ``skills``
  -> ``[]`` (same defaults POST applies today).
- Repairs are text-level: absent keys are appended at end of file, explicit
  nulls are rewritten in place — all existing content/comments preserved.
- Before any write, the whole ``agents/`` dir is snapshotted to a sibling
  ``agents-backup-<ts>`` directory; ``--undo <snapshot-dir>`` restores it.
- A migration manifest JSON is written next to the snapshot and kept
  permanently (rollback reference). Dry-run writes nothing.

The script is idempotent — re-running after a successful migration is a no-op.
"""

import argparse
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

import yaml

from deerflow.config.agents_config import (
    AGENT_NAME_PATTERN,
    CONVERGED_DEFAULT_SKILLS,
    CONVERGED_DEFAULT_TOOL_GROUPS,
)
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

DEFAULT_TOOL_GROUPS_YAML = "tool_groups:\n" + "".join(f"  - {g}\n" for g in CONVERGED_DEFAULT_TOOL_GROUPS)
DEFAULT_SKILLS_YAML = "skills: []\n"


def _yaml_value_lines(value: list) -> str:
    if not value:
        return "skills: []\n" if value == CONVERGED_DEFAULT_SKILLS else ""
    return "tool_groups:\n" + "".join(f"  - {item}\n" for item in value)


def repair_config(config_file: Path, *, dry_run: bool) -> dict | None:
    """Ensure tool_groups/skills are explicit in one agent config.

    Returns a report entry when a repair is needed, else None.
    """
    raw = config_file.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        return {"file": str(config_file), "action": f"SKIP (unparseable: {e})"}

    changes: list[str] = []

    if data.get("tool_groups") is None:
        changes.append("tool_groups -> ['web']")
    if data.get("skills") is None:
        changes.append("skills -> []")
    if not changes:
        return None

    if dry_run:
        return {"file": str(config_file), "action": "WOULD FIX: " + ", ".join(changes)}

    new_text = raw
    for key, block in (("tool_groups", DEFAULT_TOOL_GROUPS_YAML), ("skills", DEFAULT_SKILLS_YAML)):
        if data.get(key) is not None:
            continue
        if re.search(rf"^{key}:", new_text, flags=re.MULTILINE):
            # Key present with null value: rewrite that line in place.
            new_text = re.sub(
                rf"^{key}:[ \t]*(?:[~nN]ull(?![\w])|[ \t]*(?:#.*)?)$",
                block.rstrip("\n"),
                new_text,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            # Key absent: append at end (top-level flat mapping).
            if not new_text.endswith("\n"):
                new_text += "\n"
            new_text += block

    config_file.write_text(new_text, encoding="utf-8")

    # Sanity: file must now parse with both keys non-null.
    check = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    if check.get("tool_groups") is None or check.get("skills") is None:
        raise RuntimeError(f"post-repair verification failed for {config_file}")

    return {"file": str(config_file), "action": "FIXED: " + ", ".join(changes)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only, write nothing.")
    parser.add_argument("--undo", metavar="SNAPSHOT_DIR", help="Restore agents dir from snapshot and exit.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    agents_dir = get_paths().agents_dir

    if args.undo:
        snapshot = Path(args.undo).resolve()
        if not snapshot.is_dir():
            logger.error("Snapshot dir not found: %s", snapshot)
            return 1
        if agents_dir.exists():
            shutil.rmtree(agents_dir)
        shutil.copytree(snapshot, agents_dir)
        logger.info("Restored %s from %s", agents_dir, snapshot)
        return 0

    if not agents_dir.exists():
        logger.info("No agents directory at %s — nothing to migrate.", agents_dir)
        return 0

    report: list[dict] = []
    candidates = [entry for entry in sorted(agents_dir.iterdir()) if entry.is_dir() and AGENT_NAME_PATTERN.fullmatch(entry.name) and (entry / "config.yaml").exists()]
    logger.info("Scanning %d agent config(s) under %s", len(candidates), agents_dir)

    # 先探测后快照再写盘（复核修复 2026-09-04）：快照必须在任何修复写入
    # 之前落下——否则快照的是修复后状态，--undo 名存实亡；中途抛错也保证
    # 已有快照可回滚。dry-run 探测零写入，天然安全。
    pending: list[Path] = []
    if not args.dry_run:
        for entry in candidates:
            config_file = entry / "config.yaml"
            try:
                data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue  # repair_config 稍后以 SKIP 条目上报
            if data.get("tool_groups") is None or data.get("skills") is None:
                pending.append(config_file)

    manifest_path = None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    snapshot_dir = None
    if not args.dry_run and pending:
        snapshot_dir = agents_dir.parent / f"agents-backup-{stamp}"
        shutil.copytree(agents_dir, snapshot_dir)
        logger.info("Snapshot (pre-repair, --undo restores this): %s", snapshot_dir)

    for entry in candidates:
        result = repair_config(entry / "config.yaml", dry_run=args.dry_run)
        if result:
            report.append(result)

    manifest_path = None
    if not args.dry_run and report and snapshot_dir is not None:
        manifest_path = snapshot_dir.parent / f"agent-toolgroups-migration-{stamp}.json"
        manifest_path.write_text(
            json.dumps({"agents_dir": str(agents_dir), "snapshot": str(snapshot_dir), "entries": report}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Snapshot: %s", snapshot_dir)
        logger.info("Manifest (kept permanently for rollback): %s", manifest_path)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    logger.info("%d agent(s) %s.", len(report), "would be repaired (dry-run)" if args.dry_run else "repaired")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""白名单 fail-loud 校验（2026-09-09 创意工作合并计划·阶段一新增，地雷 9 根治）。

历史机制问题：agent config 的 skills 白名单引用不存在的技能时，加载侧静默丢弃
不报错——幽灵引用可长期潜伏。本测试把"引用必须可解析"变成显式失败：
遍历 `backend/seed/agents/*/config.yaml` 的白名单 + 根 `config.yaml`
`subagents.custom_agents` 的 skills，断言每个名字都能在 skills/public 或
skills/custom 下找到对应 SKILL.md（custom 与 public 同名时 custom 覆盖 public，
与 loader 语义一致，故并集即可）。

注意：根 `config.yaml` 为 gitignored 运行时配置，缺失时只校验 seed agents
（CI/新克隆场景），不视为失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (REPO_ROOT / "skills" / "public", REPO_ROOT / "skills" / "custom")
SEED_AGENTS = REPO_ROOT / "backend" / "seed" / "agents"
ROOT_CONFIG = REPO_ROOT / "config.yaml"


def _available_skills() -> set[str]:
    """收集全部可用技能名（frontmatter name 优先，缺失回退目录名）。"""
    names: set[str] = set()
    for root in SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_md in root.rglob("SKILL.md"):
            rel_parts = skill_md.relative_to(root).parts
            if "node_modules" in rel_parts:
                continue
            if any(part.startswith(".") for part in rel_parts):
                continue
            head = skill_md.read_text(encoding="utf-8")[:2000]
            m = re.search(r"^name:\s*(\S+)\s*$", head, re.M)
            names.add(m.group(1) if m else skill_md.parent.name)
    return names


def _seed_agent_whitelists() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cfg in sorted(SEED_AGENTS.glob("*/config.yaml")):
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        out[f"seed:{cfg.parent.name}"] = list(data.get("skills") or [])
    return out


def _custom_agent_whitelists() -> dict[str, list[str]]:
    if not ROOT_CONFIG.exists():
        return {}
    data = yaml.safe_load(ROOT_CONFIG.read_text(encoding="utf-8")) or {}
    agents = (data.get("subagents") or {}).get("custom_agents") or {}
    return {f"subagent:{name}": list(cfg.get("skills") or []) for name, cfg in agents.items()}


def test_all_whitelist_refs_resolve() -> None:
    available = _available_skills()
    assert available, "技能库为空——SKILL.md 扫描逻辑可能失效（假绿防线）"

    whitelists = {**_seed_agent_whitelists(), **_custom_agent_whitelists()}
    assert whitelists, "未发现任何 agent 白名单——seed 目录扫描可能失效（假绿防线）"

    missing = [f"{owner} -> {skill}" for owner, skills in whitelists.items() for skill in skills if skill not in available]
    assert not missing, "白名单引用了不存在的技能（fail-loud）:\n" + "\n".join(missing)

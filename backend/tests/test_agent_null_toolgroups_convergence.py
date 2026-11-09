"""SEC-C3/M14 (批次 2 项 2.1): null tool_groups/skills 收敛.

Legacy custom-agent configs written before the write-side hardening
(536b2f392) can lack the ``tool_groups``/``skills`` keys. At runtime ``None``
means "all tool groups"/"all enabled skills" — for user-created agents that
over-grants (bash/file groups; other users' custom skills from the shared
skills directory). Two defenses:

1. ``load_agent_config`` converges missing/null keys to the POST defaults
   (``["web"]`` / ``[]``) with a warning;
2. ``make_lead_agent`` re-checks at runtime (hard gate) — covered indirectly:
   the same AgentConfig value flows to ``get_available_tools(groups=...)``.

The migration script ``scripts/migrate_agent_explicit_toolgroups.py``
materializes the same values on disk; its repair logic is unit-tested here
(append-missing-key, rewrite-explicit-null, idempotency).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from deerflow.config.agents_config import load_agent_config
from deerflow.config.paths import Paths


def _write_agent(base: Path, config_yaml: str) -> None:
    agent_dir = base / "agents" / "legacy-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")


def _load(base: Path):
    import deerflow.config.agents_config as agents_config

    original = agents_config.get_paths
    agents_config.get_paths = lambda: Paths(base_dir=base)
    try:
        return load_agent_config("legacy-agent")
    finally:
        agents_config.get_paths = original


def test_missing_keys_converge_to_post_defaults(tmp_path, caplog):
    _write_agent(tmp_path, "name: legacy-agent\ndescription: old\n")
    with caplog.at_level(logging.WARNING):
        cfg = _load(tmp_path)
    assert cfg is not None
    assert cfg.tool_groups == ["web"]
    assert cfg.skills == []
    assert any("tool_groups" in r.message for r in caplog.records)
    assert any("skills" in r.message for r in caplog.records)


def test_explicit_null_converges_too(tmp_path):
    _write_agent(tmp_path, "name: legacy-agent\ntool_groups:\nskills:\n")
    cfg = _load(tmp_path)
    assert cfg is not None
    assert cfg.tool_groups == ["web"]
    assert cfg.skills == []


def test_explicit_values_pass_through_untouched(tmp_path, caplog):
    _write_agent(tmp_path, "name: legacy-agent\ntool_groups:\n  - file:read\nskills:\n  - deep-research\n")
    with caplog.at_level(logging.WARNING):
        cfg = _load(tmp_path)
    assert cfg is not None
    assert cfg.tool_groups == ["file:read"]
    assert cfg.skills == ["deep-research"]
    assert not any("converging" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Migration script repair logic
# ---------------------------------------------------------------------------


def _run_repair(config_file: Path, dry_run: bool = False):
    import importlib.util
    import sys

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_agent_explicit_toolgroups.py"
    spec = importlib.util.spec_from_file_location("migrate_agent_explicit_toolgroups", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.repair_config(config_file, dry_run=dry_run)


def test_repair_appends_missing_keys_preserving_comments(tmp_path):
    cfg = tmp_path / "config.yaml"
    original = "# hand-written comment\nname: legacy-agent\nmemory_scope: none  # keep me\n"
    cfg.write_text(original, encoding="utf-8")
    result = _run_repair(cfg)
    assert result is not None and "FIXED" in result["action"]
    text = cfg.read_text(encoding="utf-8")
    assert "# hand-written comment" in text
    assert "memory_scope: none  # keep me" in text
    data = yaml.safe_load(text)
    assert data["tool_groups"] == ["web"]
    assert data["skills"] == []
    assert data["memory_scope"] == "none"


def test_repair_rewrites_explicit_null_in_place(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("name: legacy-agent\ntool_groups:\nskills:\n", encoding="utf-8")
    result = _run_repair(cfg)
    assert result is not None
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["tool_groups"] == ["web"]
    assert data["skills"] == []


def test_repair_is_idempotent(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("name: legacy-agent\n", encoding="utf-8")
    assert _run_repair(cfg) is not None
    after_first = cfg.read_text(encoding="utf-8")
    assert _run_repair(cfg) is None  # second run: nothing to do
    assert cfg.read_text(encoding="utf-8") == after_first


def test_repair_dry_run_writes_nothing(tmp_path):
    cfg = tmp_path / "config.yaml"
    original = "name: legacy-agent\n"
    cfg.write_text(original, encoding="utf-8")
    result = _run_repair(cfg, dry_run=True)
    assert result is not None and "WOULD FIX" in result["action"]
    assert cfg.read_text(encoding="utf-8") == original


def test_migration_snapshot_precedes_repairs(tmp_path):
    """P3 复核修复（2026-09-04）：快照必须是修复前状态——--undo 才有意义。"""
    import importlib.util
    import sys

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_agent_explicit_toolgroups.py"
    spec = importlib.util.spec_from_file_location("migrate_agent_explicit_toolgroups_v2", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    agents = tmp_path / "agents" / "legacy-agent"
    agents.mkdir(parents=True)
    original = "name: legacy-agent\n"
    (agents / "config.yaml").write_text(original, encoding="utf-8")

    import contextlib
    import io

    # 脚本模块自带 from deerflow.config.paths import get_paths——patch 模块符号
    original_get_paths = module.get_paths
    module.get_paths = lambda: type("P", (), {"agents_dir": tmp_path / "agents"})()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            import sys as _sys

            _argv = _sys.argv
            _sys.argv = ["migrate_agent_explicit_toolgroups"]
            try:
                exit_code = module.main()
            finally:
                _sys.argv = _argv
    finally:
        module.get_paths = original_get_paths
    assert exit_code == 0

    backups = list((tmp_path / "agents").parent.glob("agents-backup-*"))
    assert len(backups) == 1, "must snapshot exactly once"
    snapshot_content = (backups[0] / "legacy-agent" / "config.yaml").read_text(encoding="utf-8")
    assert snapshot_content == original, "snapshot must capture PRE-repair state (--undo relies on it)"
    # 修复后现场已带显式键
    assert "tool_groups:" in (agents / "config.yaml").read_text(encoding="utf-8")

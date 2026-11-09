"""config.example.yaml 键集守护（C4，2026-10-05 审计 §3-3）。

config.yaml 新增顶层段而 example 未覆盖时，本测试红——防部署端模板再漂移。
覆盖口径 = example 的**活跃顶层键** ∪ **顶层注释模板键**（`# key:`——example
惯例：env 依赖段以注释模板形态提供，同样算覆盖）。
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = REPO_ROOT / "config.yaml"
CONFIG_EXAMPLE = REPO_ROOT / "config.example.yaml"

pytestmark = pytest.mark.skipif(not CONFIG_YAML.exists(), reason="config.yaml 不入库（CI 无此文件）")


def _example_top_level_keys(example_text: str, example_data: dict) -> set[str]:
    """Active YAML keys + top-level commented template keys (`# key:`)."""
    keys = {k for k in example_data if isinstance(k, str)}
    for line in example_text.splitlines():
        stripped = line.strip()
        if line.startswith("#") and stripped.startswith("#") and not line.startswith(" #"):
            body = stripped[1:].strip()
            if body and ":" in body:
                key = body.split(":", 1)[0].strip()
                # 仅形态像顶层键（小写 snake、无缩进语义）；注释段里列出的子键会被
                # 误收为顶层候选——用活跃键集过滤无意义，宁可多收：误收只会漏报，
                # 不会误报（守护只断言 config.yaml 的键被覆盖）。
                if key and key.replace("_", "").isalpha() and key.islower():
                    keys.add(key)
    return keys


def test_example_covers_config_top_level_keys():
    real = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))
    example_data = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}
    example_text = CONFIG_EXAMPLE.read_text(encoding="utf-8")

    real_keys = {k for k in real if isinstance(k, str)}
    covered = _example_top_level_keys(example_text, example_data)

    missing = real_keys - covered
    assert not missing, f"config.yaml 顶层段在 config.example.yaml 无覆盖（活跃或注释模板均无）: {sorted(missing)}"


def test_config_versions_in_sync():
    real = yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8"))
    example = yaml.safe_load(CONFIG_EXAMPLE.read_text(encoding="utf-8")) or {}
    assert real.get("config_version") == example.get("config_version"), "config_version 两份不一致——结构性改动必须双侧同步递增（AGENTS.md Config 口径）"


def test_guard_logic_actually_catches_drift(tmp_path):
    """守护自证：缺覆盖时必须红（防本测试退化成恒绿摆设）。"""
    example_active_only = {"models": []}
    example_text = "models: []\n"
    covered = _example_top_level_keys(example_text, example_active_only)
    assert {"subagents"} - covered, "guard must detect an uncovered top-level key"
    covered_with_template = _example_top_level_keys(example_text + "#subagents:\n  timeout_seconds: 900\n", example_active_only)
    assert not ({"subagents"} - covered_with_template), "guard must accept commented template coverage"

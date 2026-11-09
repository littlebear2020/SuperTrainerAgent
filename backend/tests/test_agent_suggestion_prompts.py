"""Agent suggestion_prompts（新会话自然语言建议 chip，B.3 2026-09-11）。

配置源链路测试：seed config.yaml 携带 [{label, prompt}] → AgentConfig 解析 →
AgentResponse 透传 → PUT 不管理的字段经 preserve_non_managed_fields 保留。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from deerflow.config.agents_config import (
    AgentConfig,
    load_agent_config,
    preserve_non_managed_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_CFG = REPO_ROOT / "backend/seed/agents/ppt-master/config.yaml"


class _Paths:
    def __init__(self, agents_dir: Path):
        self.agents_dir = agents_dir

    def agent_dir(self, name: str) -> Path:
        return self.agents_dir / name


def test_ppt_master_seed_carries_two_chips():
    data = yaml.safe_load(SEED_CFG.read_text(encoding="utf-8"))
    chips = data.get("suggestion_prompts")
    assert isinstance(chips, list) and len(chips) == 2
    for chip in chips:
        assert isinstance(chip.get("label"), str) and chip["label"].strip()
        assert isinstance(chip.get("prompt"), str) and chip["prompt"].strip()
    labels = [c["label"] for c in chips]
    assert "改一份现有 PPT" in labels  # roundtrip 触发（B.1）
    assert "快速生成课件" in labels  # quick-generate 触发（B.3）


def test_load_agent_config_parses_suggestion_prompts(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    (agents / "demo-agent").mkdir(parents=True)
    (agents / "demo-agent" / "config.yaml").write_text(
        "name: demo-agent\ndescription: demo\ntool_groups: [web]\nskills: []\nsuggestion_prompts:\n  - label: 试一试\n    prompt: 帮我做点什么\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: _Paths(agents))
    cfg = load_agent_config("demo-agent")
    assert cfg.suggestion_prompts == [{"label": "试一试", "prompt": "帮我做点什么"}]


def test_agent_config_defaults_to_none():
    cfg = AgentConfig(name="x-agent", description="d", tool_groups=["web"], skills=[])
    assert cfg.suggestion_prompts is None


def test_agent_response_passthrough():
    from app.gateway.routers.agents import _agent_config_to_response

    cfg = AgentConfig(
        name="x-agent",
        description="d",
        tool_groups=["web"],
        skills=[],
        suggestion_prompts=[{"label": "L", "prompt": "P"}],
    )
    resp = _agent_config_to_response(cfg)
    assert resp.suggestion_prompts == [{"label": "L", "prompt": "P"}]


def test_put_preserves_suggestion_prompts():
    """suggestion_prompts 不在 _MANAGED_AGENT_CONFIG_FIELDS，PUT 重写 config.yaml
    时必须原样保留（seed-authored 字段防丢）。"""
    cfg = AgentConfig(
        name="x-agent",
        description="d",
        tool_groups=["web"],
        skills=[],
        suggestion_prompts=[{"label": "L", "prompt": "P"}],
    )
    carried = preserve_non_managed_fields(cfg)
    assert carried["suggestion_prompts"] == [{"label": "L", "prompt": "P"}]

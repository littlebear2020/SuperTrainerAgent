"""条目 6（2026-09-12 缺陷批）：后端退役 agent 别名映射改写 + 跨仓守护。

修前：course-ppt/五壳别名只在前端 retired-mapping.ts 生效；API 直调旧名在
全新部署上 → run 被创建（HTTP 200）后后台裸 FileNotFoundError（带服务器绝对
路径）经 SSE 暴露；在残留运行态目录的机器上退役 agent 会被直接跑起来。
方案 a：后端 start_run 入口改写别名（_shared/retired_agents 单一后端事实源，
与前端文件内容一致——跨仓守护测试兜住双写漂移）。
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def test_mapping_content():
    from app.gateway._shared.retired_agents import RETIRED_AGENT_ALIAS, resolve_retired_agent

    assert RETIRED_AGENT_ALIAS["course-ppt"] == "ppt-master"
    for shell in ("card-poster", "case-article", "design-specialist", "knowledge-extraction", "video-clip"):
        assert RETIRED_AGENT_ALIAS[shell] == "creative-studio"
    # 未知名原样返回（与前端 resolveAgentName 同语义）
    assert resolve_retired_agent("ppt-master") == "ppt-master"
    assert resolve_retired_agent("lead_agent") == "lead_agent"


def test_frontend_mapping_in_sync():
    """跨仓守护：backend `_shared/retired_agents.py` 与 frontend
    `retired-mapping.ts` 键值必须一致（双写漂移在此拦下）。"""
    from app.gateway._shared.retired_agents import RETIRED_AGENT_ALIAS

    ts_path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "core" / "agents" / "retired-mapping.ts"
    src = ts_path.read_text(encoding="utf-8")
    entries = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', src))
    assert entries == RETIRED_AGENT_ALIAS, f"前后端退役映射漂移：backend={dict(RETIRED_AGENT_ALIAS)} frontend={entries}"


def test_apply_retired_agent_aliases_rewrites_all_sources():
    """方案 a：四个 agent_name 来源全部改写——routing 优先级 context >
    configurable > assistant_id，只改一处会被旧名盖回。"""
    from app.gateway.services import _apply_retired_agent_aliases

    body = SimpleNamespace(
        assistant_id="course-ppt",
        config={"configurable": {"agent_name": "video-clip"}, "context": {"agent_name": "card-poster"}},
        context={"agent_name": "course-ppt"},
    )
    _apply_retired_agent_aliases(body)
    assert body.assistant_id == "ppt-master"
    assert body.config["configurable"]["agent_name"] == "creative-studio"
    assert body.config["context"]["agent_name"] == "creative-studio"
    assert body.context["agent_name"] == "ppt-master"


def test_apply_retired_agent_aliases_noop_for_live_names():
    from app.gateway.services import _apply_retired_agent_aliases

    body = SimpleNamespace(assistant_id="ppt-master", config=None, context=None)
    _apply_retired_agent_aliases(body)
    assert body.assistant_id == "ppt-master"


@pytest.mark.asyncio
async def test_gate_unknown_agent_name_404(monkeypatch):
    """配套：未知/已退役 agent 名（改写后仍未知名）→ 建入口 404（不建 run 记录、
    不再后台裸 FileNotFoundError 泄服务器路径）。"""
    from fastapi import HTTPException

    from app.gateway.services import _gate_agent_runnable

    def _missing(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr("deerflow.config.agents_config.load_agent_config", _missing)
    with pytest.raises(HTTPException) as exc:
        await _gate_agent_runnable("ghost-agent", MagicMock())
    assert exc.value.status_code == 404
    assert "不存在或已退役" in exc.value.detail


# ── 复审二轮（2026-09-13）：归一化查找 ────────────────────────────────────


def test_resolve_retired_agent_normalizes_before_lookup():
    """大小写/下划线/首尾空白变体也须改写——与 _gate_agent_runnable 同款归一化
    （strip + lower + 下划线→连字符）。修前 "Course_PPT" 原串查不到映射，落
    gate 的 FileNotFoundError 404 而非改写启动承接 agent。"""
    from app.gateway._shared.retired_agents import resolve_retired_agent

    assert resolve_retired_agent("Course_PPT") == "ppt-master"
    assert resolve_retired_agent("COURSE-PPT") == "ppt-master"
    assert resolve_retired_agent("  course-ppt  ") == "ppt-master"
    assert resolve_retired_agent("Video_Clip") == "creative-studio"
    # 未命中：原样返回（不归一化输出——语义与前端 resolveAgentName 一致，
    # 活名的归一化是 gate 的职责）
    assert resolve_retired_agent("Some_Live_Agent") == "Some_Live_Agent"


@pytest.mark.asyncio
async def test_apply_retired_agent_aliases_rewrites_normalized_variant():
    """端到端：API 直调 "Course_PPT"（assistant_id + context 双源）→ 改写为
    ppt-master，不再 404。"""
    from types import SimpleNamespace

    from app.gateway.services import _apply_retired_agent_aliases

    body = SimpleNamespace(assistant_id="Course_PPT", config={"context": {"agent_name": "Video_Clip"}}, context=None)
    _apply_retired_agent_aliases(body)
    assert body.assistant_id == "ppt-master"
    assert body.config["context"]["agent_name"] == "creative-studio"

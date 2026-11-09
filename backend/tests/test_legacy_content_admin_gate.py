"""SEC-M22 (批次 2 项 2.9): 无主 legacy skill/agent 改删需 admin.

无 created_by 的 legacy 内容原先全员可改删——skill 内容进全员系统提示词，
agent SOUL/配置进所有运行会话，构成跨用户提示注入原语。现改为 admin-only。
前置盘点（写入提交信息）：seed 直接落盘，不走本 API——无自动化流程以
普通用户身份改 legacy 内容。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from deerflow.skills.types import Skill


def _skill(name: str, created_by: str | None) -> Skill:
    skill_dir = Path("/skills/custom") / name
    return Skill(
        name=name,
        description="d",
        license=None,
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category="custom",
        enabled=True,
        created_by=created_by,
    )


def _legacy_skill() -> Skill:
    return _skill("legacy-skill", None)


def _owned_skill() -> Skill:
    return _skill("owned-skill", "u-owner")


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(), cookies={})


def _user(role="user"):
    return SimpleNamespace(system_role=role)


@pytest.mark.asyncio
async def test_legacy_skill_requires_admin():
    from app.gateway.routers.skills import _check_skill_ownership

    with patch("app.gateway.routers.skills.get_optional_user_from_request", new=AsyncMock(return_value=_user("user"))):
        with pytest.raises(HTTPException) as exc:
            await _check_skill_ownership(_legacy_skill(), _request())
    assert exc.value.status_code == 403
    assert "admin" in exc.value.detail


@pytest.mark.asyncio
async def test_legacy_skill_allows_admin():
    from app.gateway.routers.skills import _check_skill_ownership

    with patch("app.gateway.routers.skills.get_optional_user_from_request", new=AsyncMock(return_value=_user("admin"))):
        await _check_skill_ownership(_legacy_skill(), _request())  # no raise


@pytest.mark.asyncio
async def test_legacy_skill_rejects_anonymous():
    from app.gateway.routers.skills import _check_skill_ownership

    with patch("app.gateway.routers.skills.get_optional_user_from_request", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await _check_skill_ownership(_legacy_skill(), _request())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owned_skill_semantics_unchanged(monkeypatch):
    """有主 skill 语义不变：创建者放行、他人 403（contextvar 解析）。"""
    from app.gateway.routers.skills import _check_skill_ownership

    monkeypatch.setattr("app.gateway.routers.skills.get_effective_user_id", lambda: "u-owner")
    await _check_skill_ownership(_owned_skill(), _request())  # creator → no raise
    monkeypatch.setattr("app.gateway.routers.skills.get_effective_user_id", lambda: "u-attacker")
    with pytest.raises(HTTPException) as exc:
        await _check_skill_ownership(_owned_skill(), _request())
    assert exc.value.status_code == 403


# ── agents ───────────────────────────────────────────────────────────────


def _agent_cfg(**kwargs):
    from deerflow.config.agents_config import AgentConfig

    defaults = {"name": "legacy-agent", "description": "d", "category": "custom"}
    defaults.update(kwargs)
    return AgentConfig(**defaults)


def _request_with_user(user):
    auth = SimpleNamespace(user=user, is_authenticated=user is not None)
    return SimpleNamespace(state=SimpleNamespace(auth=auth))


def _auser(role="user", uid="u-1"):
    return SimpleNamespace(id=uid, system_role=role)


@pytest.mark.asyncio
async def test_legacy_agent_update_requires_admin():
    from app.gateway.routers.agents import _check_update_permission

    with pytest.raises(HTTPException) as exc:
        await _check_update_permission(_agent_cfg(created_by=None), _request_with_user(_auser("user")))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_legacy_agent_update_allows_admin():
    from app.gateway.routers.agents import _check_update_permission

    await _check_update_permission(_agent_cfg(created_by=None), _request_with_user(_auser("admin")))  # no raise


@pytest.mark.asyncio
async def test_legacy_agent_delete_requires_admin():
    from app.gateway.routers.agents import _check_delete_permission

    with pytest.raises(HTTPException) as exc:
        with patch("app.gateway.routers.agents.load_agent_config", return_value=_agent_cfg(created_by=None)):
            await _check_delete_permission("legacy-agent", _request_with_user(_auser("user")))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owned_agent_semantics_unchanged():
    """有主 custom agent：创建者放行、他人 404（不泄露存在性）。"""
    from app.gateway.routers.agents import _check_update_permission

    await _check_update_permission(_agent_cfg(created_by="u-1"), _request_with_user(_auser("user", uid="u-1")))  # creator
    with pytest.raises(HTTPException) as exc:
        await _check_update_permission(_agent_cfg(created_by="u-1"), _request_with_user(_auser("user", uid="u-2")))
    assert exc.value.status_code == 404

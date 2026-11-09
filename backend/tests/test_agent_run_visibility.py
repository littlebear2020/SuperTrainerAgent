"""SEC-H8 (批次 2 项 2.3): start_run 自定义 agent 可见性校验.

任意认证（或匿名）用户只要知道 agent 名就能通过 runs/thread_runs/gallery
全部 6 个 run 创建入口运行他人自定义 agent——`start_run` 是唯一汇聚点。
`_assert_assistant_runnable` 用共享 `can_see_agent` 谓词（与 agents 列表/
详情路由同语义）在创建任何 run 记录之前拦截：

- public → 所有人（含匿名）放行；
- custom + created_by → 仅创建者/admin；
- custom 无 created_by（legacy）→ 仅登录用户（免登录也拒绝——2026-09-03
  需求方拍板：免登录形态与认证形态行为统一）；
- 默认 assistant（lead_agent/None）短路放行；未知 agent 名不在此处拦截。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from deerflow.config.agents_config import AgentConfig


def _agent(**kwargs) -> AgentConfig:
    defaults = {"name": "secret-agent", "description": "d", "category": "custom"}
    defaults.update(kwargs)
    return AgentConfig(**defaults)


def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(), cookies={})


def _auth(user=None):
    """Build an AuthContext-like object matching _assert_assistant_runnable reads."""
    ctx = SimpleNamespace(user=user, is_authenticated=user is not None)
    return ctx


async def _run(body_assistant_id: str | None, user) -> None:
    from app.gateway.services import _assert_assistant_runnable

    with patch("app.gateway.authz._authenticate", new=AsyncMock(return_value=_auth(user))):
        body = SimpleNamespace(assistant_id=body_assistant_id)
        await _assert_assistant_runnable(body, _request())


def _user(role="user", uid="u-123"):
    return SimpleNamespace(id=uid, system_role=role)


AGENTS = {
    "public-agent": _agent(name="public-agent", category="public", created_by="admin"),
    "owned-agent": _agent(name="owned-agent", created_by="u-owner"),
    "legacy-agent": _agent(name="legacy-agent", created_by=None),
}


def _patch_loader():
    def _load(name):
        return AGENTS.get(name)

    return patch("deerflow.config.agents_config.load_agent_config", side_effect=_load)


@pytest.mark.asyncio
async def test_default_assistant_short_circuits():
    """lead_agent / None（无 agent 目录）不触发任何加载与校验。"""
    with _patch_loader() as loader:
        await _run(None, user=_user())
        await _run("lead_agent", user=_user())
        loader.assert_not_called()


@pytest.mark.asyncio
async def test_public_agent_runnable_by_anonymous():
    with _patch_loader():
        await _run("public-agent", user=None)  # no raise


@pytest.mark.asyncio
async def test_custom_agent_denied_for_other_user():
    with _patch_loader():
        with pytest.raises(HTTPException) as exc:
            await _run("owned-agent", user=_user(uid="u-attacker"))
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_custom_agent_allowed_for_creator():
    with _patch_loader():
        await _run("owned-agent", user=_user(uid="u-owner"))  # no raise


@pytest.mark.asyncio
async def test_custom_agent_allowed_for_admin():
    with _patch_loader():
        await _run("owned-agent", user=_user(role="admin", uid="u-admin"))  # no raise


@pytest.mark.asyncio
async def test_legacy_agent_allowed_for_logged_in_user():
    with _patch_loader():
        await _run("legacy-agent", user=_user(uid="u-anyone"))  # no raise


@pytest.mark.asyncio
async def test_legacy_agent_denied_for_anonymous():
    """2.3 拍板：免登录形态也一起拒绝（与认证形态统一）。"""
    with _patch_loader():
        with pytest.raises(HTTPException) as exc:
            await _run("legacy-agent", user=None)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_custom_agent_denied_for_anonymous():
    with _patch_loader():
        with pytest.raises(HTTPException) as exc:
            await _run("owned-agent", user=None)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_agent_name_rejected_404():
    """条目 6 配套（2026-09-12 缺陷批）：未知/已退役 agent 名建入口同步 404。

    修前放行走旧失败路径：run 被创建（HTTP 200）后后台裸 FileNotFoundError（带
    服务器绝对路径）经 SSE 暴露；404 不建 run 记录、不泄路径。退役名在更早的
    _apply_retired_agent_aliases 已改写到承接 agent，走不到本分支。"""

    def _raise(name):
        raise FileNotFoundError(f"Agent directory not found: {name}")

    with patch("deerflow.config.agents_config.load_agent_config", side_effect=_raise):
        with pytest.raises(HTTPException) as exc:
            await _run("no-such-agent", user=_user())
        assert exc.value.status_code == 404
        assert "不存在或已退役" in exc.value.detail


@pytest.mark.asyncio
async def test_assistant_id_normalized_like_build_run_config():
    """build_run_config 归一化（lower/下划线转连字符）后才能命中目录名。

    条目 6 配套：loader FileNotFoundError 现在同步 404（未知名不再放行）——
    归一化断言不变（loader 收到的是归一化后的目录名）。"""
    with patch("deerflow.config.agents_config.load_agent_config") as loader:
        loader.side_effect = FileNotFoundError("not found")
        with pytest.raises(HTTPException):
            await _run("Owned_Agent", user=_user(uid="u-attacker"))
        assert loader.call_args.args[0] == "owned-agent"


def test_can_see_agent_shared_predicate_semantics():
    """共享谓词本体（迁移自 routers/agents.py，语义不变）。"""
    from deerflow.config.agents_config import can_see_agent

    pub = AGENTS["public-agent"]
    assert can_see_agent(pub, None, False)
    owned = AGENTS["owned-agent"]
    assert not can_see_agent(owned, None, False)
    assert not can_see_agent(owned, "u-other", False)
    assert can_see_agent(owned, "u-owner", False)
    assert can_see_agent(owned, "u-other", True)
    legacy = AGENTS["legacy-agent"]
    assert can_see_agent(legacy, "u-anyone", False)
    assert not can_see_agent(legacy, None, False)


# ---------------------------------------------------------------------------
# SEC-H8 复核修复（2026-09-04）：agent_name 注入绕过——端到端回归
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body_kwargs",
    [
        # 向量 1：不带 assistant_id，仅 body.context.agent_name
        {"assistant_id": "lead_agent", "config": {}, "context": {"agent_name": "victim-agent"}},
        # 向量 2：可见 assistant_id + body.config.configurable.agent_name 塞他人 agent
        {"assistant_id": "public-agent", "config": {"configurable": {"agent_name": "victim-agent"}}},
        # 向量 3：不带 assistant_id，仅 config.configurable.agent_name
        # （configurable 优先于 assistant_id 注入——见 build_run_config :284）
        {"assistant_id": "lead_agent", "config": {"configurable": {"agent_name": "victim-agent"}}},
        # 注：assistant_id + context.agent_name 不构成绕过——context 合并用
        # setdefault，输给 assistant_id 派生值（最终生效 assistant_id 本身）。
    ],
)
async def test_agent_name_injection_vectors_blocked(tmp_path, monkeypatch, body_kwargs):
    """agent 路由不止 assistant_id：context/configurable 携带的 agent_name
    也必须过 can_see_agent——修前门禁只查 assistant_id，可完全绕过。"""
    import app.gateway.services as services
    from deerflow.config.paths import Paths

    agents = tmp_path / "agents"
    for name, created_by, category in (("victim-agent", "u-victim", "custom"), ("public-agent", "admin", "public")):
        d = agents / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            f"name: {name}\ndescription: d\ntool_groups:\n  - web\nskills: []\ncategory: {category}\ncreated_by: {created_by}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: Paths(base_dir=tmp_path))

    auth = SimpleNamespace(user=_user(uid="u-alice"), is_authenticated=True)
    monkeypatch.setattr("app.gateway.authz._authenticate", AsyncMock(return_value=auth))

    record = SimpleNamespace(run_id="r1", status="pending")
    run_mgr = MagicMock()
    run_mgr.create_or_reject = AsyncMock(return_value=record)
    run_mgr.set_status = AsyncMock()

    async def _thread_get(thread_id, user_id=None):
        return {"user_id": "u-alice"}  # 既有行 → 跳过 create 分支

    thread_store = MagicMock()
    thread_store.get = _thread_get
    thread_store.update_status = AsyncMock()
    monkeypatch.setattr(services, "get_stream_bridge", lambda request: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda request: run_mgr)
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=thread_store))

    body = SimpleNamespace(
        on_disconnect="cancel",
        metadata={},
        input={"messages": []},
        multitask_strategy=None,
        stream_mode=None,
        stream_subgraphs=False,
        on_completion=None,
        **body_kwargs,
    )
    request = _request()

    with pytest.raises(HTTPException) as exc:
        await services.start_run(body, "thread-1", request)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_context_masks_config_context_agent_name(tmp_path, monkeypatch):
    """二轮复核（2026-09-04）：运行时 _get_runtime_config 是 context 覆盖
    configurable——顶层 context 放一个可见名掩护 config.context 里的受害名，
    一轮修复的门禁（configurable 先读）正好看不见受害名。全源门禁必须拦下。"""
    import app.gateway.services as services
    from deerflow.config.paths import Paths

    agents = tmp_path / "agents"
    for name, created_by, category in (("victim-agent", "u-victim", "custom"), ("visible-agent", "admin", "public")):
        d = agents / name
        d.mkdir(parents=True)
        (d / "config.yaml").write_text(
            f"name: {name}\ndescription: d\ntool_groups:\n  - web\nskills: []\ncategory: {category}\ncreated_by: {created_by}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: Paths(base_dir=tmp_path))

    auth = SimpleNamespace(user=_user(uid="u-alice"), is_authenticated=True)
    monkeypatch.setattr("app.gateway.authz._authenticate", AsyncMock(return_value=auth))

    run_mgr = MagicMock()
    run_mgr.create_or_reject = AsyncMock()

    async def _thread_get(thread_id, user_id=None):
        return {"user_id": "u-alice"}

    thread_store = MagicMock()
    thread_store.get = _thread_get
    thread_store.update_status = AsyncMock()
    monkeypatch.setattr(services, "get_stream_bridge", lambda request: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda request: run_mgr)
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=thread_store))

    body = SimpleNamespace(
        assistant_id="lead_agent",
        on_disconnect="cancel",
        metadata={},
        input={"messages": []},
        config={"context": {"agent_name": "victim-agent"}},
        multitask_strategy=None,
        stream_mode=None,
        context={"agent_name": "visible-agent"},
    )
    with pytest.raises(HTTPException) as exc:
        await services.start_run(body, "thread-1", _request())
    assert exc.value.status_code == 404
    # 门禁在建 run 记录之前——拒绝不留孤儿 pending run
    run_mgr.create_or_reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_run_leaves_no_record(tmp_path, monkeypatch):
    """门禁前置：任意注入向量被拒时 create_or_reject 不被调用（孤儿 run 修复）。"""
    import app.gateway.services as services
    from deerflow.config.paths import Paths

    d = tmp_path / "agents" / "victim-agent"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(
        "name: victim-agent\ndescription: d\ntool_groups:\n  - web\nskills: []\ncategory: custom\ncreated_by: u-victim\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: Paths(base_dir=tmp_path))
    auth = SimpleNamespace(user=_user(uid="u-alice"), is_authenticated=True)
    monkeypatch.setattr("app.gateway.authz._authenticate", AsyncMock(return_value=auth))

    run_mgr = MagicMock()
    run_mgr.create_or_reject = AsyncMock()
    monkeypatch.setattr(services, "get_stream_bridge", lambda request: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda request: run_mgr)
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=MagicMock()))

    body = SimpleNamespace(
        assistant_id="lead_agent",
        on_disconnect="cancel",
        metadata={},
        input={"messages": []},
        config={},
        multitask_strategy=None,
        stream_mode=None,
        context={"agent_name": "victim-agent"},
    )
    with pytest.raises(HTTPException):
        await services.start_run(body, "thread-2", _request())
    run_mgr.create_or_reject.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_agent_name_visible_agent_allowed(tmp_path, monkeypatch):
    """对照：自己创建的 agent 经 context.agent_name 路由照常放行（前端 agent
    聊天页正是此通道——修复不得破坏）。"""
    import app.gateway.services as services
    from deerflow.config.paths import Paths

    d = tmp_path / "agents" / "my-agent"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text(
        "name: my-agent\ndescription: d\ntool_groups:\n  - web\nskills: []\ncategory: custom\ncreated_by: u-alice\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: Paths(base_dir=tmp_path))

    auth = SimpleNamespace(user=_user(uid="u-alice"), is_authenticated=True)
    monkeypatch.setattr("app.gateway.authz._authenticate", AsyncMock(return_value=auth))

    record = SimpleNamespace(run_id="r2", status="pending")
    run_mgr = MagicMock()
    run_mgr.create_or_reject = AsyncMock(return_value=record)

    async def _thread_get(thread_id, user_id=None):
        return {"user_id": "u-alice"}

    thread_store = MagicMock()
    thread_store.get = _thread_get
    thread_store.update_status = AsyncMock()

    async def _fake_run_agent(*args, **kwargs):
        return None

    monkeypatch.setattr(services, "get_stream_bridge", lambda request: MagicMock())
    monkeypatch.setattr(services, "get_run_manager", lambda request: run_mgr)
    monkeypatch.setattr(services, "get_run_context", lambda request: SimpleNamespace(thread_store=thread_store))
    monkeypatch.setattr(services, "run_agent", _fake_run_agent)

    body = SimpleNamespace(
        assistant_id="lead_agent",
        on_disconnect="cancel",
        metadata={},
        input={"messages": []},
        config={},
        multitask_strategy=None,
        stream_mode=None,
        stream_subgraphs=False,
        on_completion=None,
        interrupt_before=None,
        interrupt_after=None,
        context={"agent_name": "my-agent"},
    )
    record2 = await services.start_run(body, "thread-2", _request())
    assert record2 is record

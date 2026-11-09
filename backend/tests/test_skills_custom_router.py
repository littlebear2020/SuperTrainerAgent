import errno
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers import skills as skills_router
from deerflow.skills.types import Skill

_TEST_USER = "test-user-autouse"  # conftest autouse contextvar user


def _skill_content(name: str, description: str = "Demo skill", created_by: str | None = None) -> str:
    owner = f"created_by: {created_by}\n" if created_by else ""
    return f"---\nname: {name}\ndescription: {description}\n{owner}---\n\n# {name}\n"


async def _async_scan(decision: str, reason: str):
    from deerflow.skills.security_scanner import ScanResult

    return ScanResult(decision=decision, reason=reason)


def _make_skill(name: str, *, enabled: bool) -> Skill:
    skill_dir = Path(f"/tmp/{name}")
    return Skill(
        name=name,
        description=f"Description for {name}",
        license="MIT",
        skill_dir=skill_dir,
        skill_file=skill_dir / "SKILL.md",
        relative_path=Path(name),
        category="public",
        enabled=enabled,
    )


def _make_test_app(config) -> FastAPI:
    app = FastAPI()
    app.state.config = config
    app.include_router(skills_router.router)
    return app


def _make_skill_archive(tmp_path: Path, name: str, content: str | None = None) -> Path:
    archive = tmp_path / f"{name}.skill"
    skill_content = content or _skill_content(name)
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", skill_content)
    return archive


def _setup_storage_env(monkeypatch, tmp_path: Path) -> tuple[SimpleNamespace, Path, Path]:
    """P1-S3（2026-09-30）：user-scoped 存储世界的测试夹具。

    - ``get_paths`` 指向 tmp 基座（用户 custom 根 = tmp/users/{uid}/skills/custom）
    - config.skills 指向 tmp/skills（public 全局根 + 旧全局 custom = LEGACY 兜底）
    - 重置存储进程级缓存，避免跨用例串台
    """
    from deerflow.config.paths import Paths
    from deerflow.skills.storage import reset_skill_storage, reset_user_skill_storage

    base = tmp_path
    skills_root = base / "skills"
    (skills_root / "public").mkdir(parents=True, exist_ok=True)
    (skills_root / "custom").mkdir(parents=True, exist_ok=True)
    user_custom_root = base / "users" / _TEST_USER / "skills" / "custom"
    user_custom_root.mkdir(parents=True, exist_ok=True)

    config = SimpleNamespace(
        skills=SimpleNamespace(get_skills_path=lambda: skills_root, container_path="/mnt/skills"),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(base_dir=base))
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    monkeypatch.setattr("deerflow.config.get_app_config", lambda *a, **k: config)
    monkeypatch.setattr("deerflow.skills.manager.get_app_config", lambda *a, **k: config)
    reset_skill_storage()
    reset_user_skill_storage()
    return config, skills_root, user_custom_root


def _write_user_skill(user_custom_root: Path, name: str, created_by: str | None = _TEST_USER, description: str = "Demo skill") -> Path:
    d = user_custom_root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(_skill_content(name, description, created_by), encoding="utf-8")
    return d


def test_install_skill_archive_runs_security_scan(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    archive = _make_skill_archive(tmp_path, "archive-skill")
    scan_calls = []
    refresh_calls = []

    async def _scan(content, *, executable, location, app_config=None, static_findings=None):
        from deerflow.skills.security_scanner import ScanResult

        scan_calls.append({"content": content, "executable": executable, "location": location})
        return ScanResult(decision="allow", reason="ok")

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr(skills_router, "resolve_thread_virtual_path", lambda thread_id, path: archive)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    app = FastAPI()
    app.state.config = config
    app.include_router(skills_router.router)

    with TestClient(app) as client:
        response = client.post("/api/skills/install", json={"thread_id": "thread-1", "path": "mnt/user-data/outputs/archive-skill.skill"})

    assert response.status_code == 200, response.text
    assert response.json()["skill_name"] == "archive-skill"
    # P1-S3：落点 = 本用户 custom 目录，不再是全局 custom
    assert (user_custom_root / "archive-skill" / "SKILL.md").exists()
    assert not (skills_root / "custom" / "archive-skill").exists()
    assert scan_calls and scan_calls[0]["location"] == "archive-skill/SKILL.md"
    assert refresh_calls == ["refresh"]


def test_install_skill_archive_security_scan_block_returns_400(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    archive = _make_skill_archive(tmp_path, "blocked-skill")
    refresh_calls = []

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="prompt injection")

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr(skills_router, "resolve_thread_virtual_path", lambda thread_id, path: archive)
    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_skills_system_prompt_cache_async", _refresh)

    app = FastAPI()
    app.state.config = config
    app.include_router(skills_router.router)

    with TestClient(app) as client:
        response = client.post("/api/skills/install", json={"thread_id": "thread-1", "path": "mnt/user-data/outputs/blocked-skill.skill"})

    assert response.status_code == 400
    assert not (user_custom_root / "blocked-skill").exists()
    assert not (skills_root / "custom" / "blocked-skill").exists()
    assert refresh_calls == []


def test_custom_skills_router_lifecycle(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    # SEC-M22 复核修复后归属校验真实执行——disk skill 携 autouse 用户为 creator。
    _write_user_skill(user_custom_root, "demo-skill", created_by=_TEST_USER)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        response = client.get("/api/skills/custom")
        assert response.status_code == 200
        assert response.json()["skills"][0]["name"] == "demo-skill"

        get_response = client.get("/api/skills/custom/demo-skill")
        assert get_response.status_code == 200
        assert "# demo-skill" in get_response.json()["content"]

        update_response = client.put(
            "/api/skills/custom/demo-skill",
            json={"content": _skill_content("demo-skill", "Edited skill")},
        )
        assert update_response.status_code == 200
        assert update_response.json()["description"] == "Edited skill"

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["action"] == "human_edit"

        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 200
        assert rollback_response.json()["description"] == "Demo skill"
        assert refresh_calls == ["refresh", "refresh"]


def test_custom_skill_rollback_blocked_by_scanner(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    original_content = _skill_content("demo-skill", created_by=_TEST_USER)
    edited_content = _skill_content("demo-skill", "Edited skill", created_by=_TEST_USER)
    _write_user_skill(user_custom_root, "demo-skill", created_by=_TEST_USER, description="Edited skill")
    from deerflow.skills.storage import get_or_new_user_skill_storage

    storage = get_or_new_user_skill_storage(_TEST_USER, app_config=config)
    storage.get_skill_history_file("demo-skill").parent.mkdir(parents=True, exist_ok=True)
    storage.get_skill_history_file("demo-skill").write_text(
        '{"action":"human_edit","prev_content":' + json.dumps(original_content) + ',"new_content":' + json.dumps(edited_content) + "}\n",
        encoding="utf-8",
    )

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="block", reason="unsafe rollback")

    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", _scan)

    app = _make_test_app(config)

    with TestClient(app) as client:
        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 400
        assert "unsafe rollback" in rollback_response.json()["detail"]

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["scanner"]["decision"] == "block"


def test_custom_skill_delete_preserves_history_and_allows_restore(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    original_content = _skill_content("demo-skill", created_by=_TEST_USER)
    skill_dir = _write_user_skill(user_custom_root, "demo-skill", created_by=_TEST_USER)
    monkeypatch.setattr("app.gateway.routers.skills.scan_skill_content", lambda *args, **kwargs: _async_scan("allow", "ok"))
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")
        assert delete_response.status_code == 200
        assert not (skill_dir / "SKILL.md").exists()

        history_response = client.get("/api/skills/custom/demo-skill/history")
        assert history_response.status_code == 200
        assert history_response.json()["history"][-1]["action"] == "human_delete"

        rollback_response = client.post("/api/skills/custom/demo-skill/rollback", json={"history_index": -1})
        assert rollback_response.status_code == 200
        assert rollback_response.json()["description"] == "Demo skill"
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original_content
        assert refresh_calls == ["refresh", "refresh"]


def test_custom_skill_delete_continues_when_history_write_is_readonly(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    skill_dir = _write_user_skill(user_custom_root, "demo-skill", created_by=_TEST_USER)
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    def _readonly_history(*args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system", "history")

    monkeypatch.setattr("deerflow.skills.storage.user_scoped_skill_storage.UserScopedSkillStorage.append_history", _readonly_history)
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"success": True}
    assert not skill_dir.exists()
    assert refresh_calls == ["refresh"]


def test_custom_skill_delete_fails_when_skill_dir_removal_fails(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    skill_dir = _write_user_skill(user_custom_root, "demo-skill", created_by=_TEST_USER)
    refresh_calls = []

    async def _refresh():
        refresh_calls.append("refresh")

    def _fail_rmtree(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(skill_dir))

    monkeypatch.setattr("deerflow.skills.storage.local_skill_storage.shutil.rmtree", _fail_rmtree)
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)

    with TestClient(app) as client:
        delete_response = client.delete("/api/skills/custom/demo-skill")

    assert delete_response.status_code == 500
    assert "Failed to delete custom skill" in delete_response.json()["detail"]
    assert skill_dir.exists()
    assert refresh_calls == []


def test_update_skill_refreshes_prompt_cache_before_return(monkeypatch, tmp_path):
    # 原 requires_real_config 门（2026-09-23 收尾批）的唯一真实依赖是端点签名
    # 里的 Depends(get_config) → get_app_config() 读 config.yaml（CI fresh
    # checkout 不存在 → 503）。端点其余环节本测试已全部打桩，故把
    # get_app_config 钉到最小内存 AppConfig 后全环境可跑。
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig

    monkeypatch.setattr("deerflow.config.get_app_config", lambda: AppConfig(sandbox=SandboxConfig(use="test")))

    config_path = tmp_path / "extensions_config.json"
    enabled_state = {"value": True}
    refresh_calls = []

    def _load_skills(config):
        skill = _make_skill("demo-skill", enabled=enabled_state["value"])
        skill.created_by = _TEST_USER  # §4.4：启停闸放行自有技能
        return [skill]

    async def _refresh():
        refresh_calls.append("refresh")
        enabled_state["value"] = False

    monkeypatch.setattr("app.gateway.routers.skills._load_skills_sync", _load_skills)
    monkeypatch.setattr("app.gateway.routers.skills.get_extensions_config", lambda: SimpleNamespace(mcp_servers={}, skills={}))
    monkeypatch.setattr("app.gateway.routers.skills.reload_extensions_config", lambda: None)
    monkeypatch.setattr(skills_router.ExtensionsConfig, "resolve_config_path", staticmethod(lambda: config_path))
    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(SimpleNamespace())

    with TestClient(app) as client:
        response = client.put("/api/skills/demo-skill", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert refresh_calls == ["refresh"]
    # No file existed beforehand, so there is nothing to carry over and the
    # write contains only what this endpoint owns. C2 changed the merge source
    # from the in-memory (env-resolved) config to the raw file on disk —
    # rebuilding `mcpServers` from memory is what used to overwrite every
    # `$VAR` placeholder with its resolved value or an empty string. An empty
    # `mcpServers: {}` key is no longer synthesized for a file that has none.
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"skills": {"demo-skill": {"enabled": False}}}


# ── SEC-M22 复核修复回归（2026-09-04）：三处调用点补 await 后，归属校验
# 必须在端点层真实生效——修前 async 化但无 await，协程被丢弃，校验整体
# 失效（任何登录用户可改删他人 custom skill）。这些用例直接打 HTTP 端点。
# P1-S3 后：他人有主 skill = 另一用户的 user-scoped 目录；无主/legacy =
# 旧全局 custom 只读兜底（admin-only）。


def test_endpoint_blocks_update_of_others_custom_skill(monkeypatch, tmp_path):
    """他人有主 skill：登录用户（contextvar=test-user-autouse ≠ creator）PUT → 403。

    SEC-M22 复核修复（2026-09-04）：修前 _check_skill_ownership async 化但调用点
    无 await，协程被丢弃——校验整体失效。本用例端点级钉死修复。
    """
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    other_user_root = tmp_path / "users" / "u-victim" / "skills" / "custom"
    other_user_root.mkdir(parents=True, exist_ok=True)
    (other_user_root / "victim-skill").mkdir()
    (other_user_root / "victim-skill" / "SKILL.md").write_text(_skill_content("victim-skill", created_by="u-victim"), encoding="utf-8")
    app = _make_test_app(config)
    with TestClient(app) as client:
        resp = client.put(
            "/api/skills/custom/victim-skill",
            json={"content": _skill_content("victim-skill", "hijacked")},
        )
    # P1-S3：跨用户访问在 per-user 存储下 = 404（不泄露存在性，B5 验收口径）
    assert resp.status_code == 404, resp.text


def test_endpoint_blocks_delete_of_others_custom_skill(monkeypatch, tmp_path):
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    other_user_root = tmp_path / "users" / "u-victim" / "skills" / "custom"
    (other_user_root / "victim-skill").mkdir(parents=True)
    (other_user_root / "victim-skill" / "SKILL.md").write_text(_skill_content("victim-skill", created_by="u-victim"), encoding="utf-8")
    app = _make_test_app(config)
    with TestClient(app) as client:
        resp = client.delete("/api/skills/custom/victim-skill")
    # P1-S3：跨用户访问 = 404（隔离，不泄露存在性）
    assert resp.status_code == 404, resp.text


def test_endpoint_legacy_skill_requires_admin_for_update(monkeypatch, tmp_path):
    """legacy 无主 skill（旧全局 custom 只读兜底）：PUT → 400 引导自建影子版本。

    P1-S3 后语义（对齐上游）：legacy 经 ensure_custom_skill_is_editable 拒改
    （"create your own version"），列表/读取仍可见（迁移平滑）。
    """
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    legacy_dir = skills_root / "custom" / "legacy-skill"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "SKILL.md").write_text(_skill_content("legacy-skill"), encoding="utf-8")
    app = _make_test_app(config)
    with TestClient(app) as client:
        list_resp = client.get("/api/skills/custom")
        assert list_resp.status_code == 200
        assert any(s["name"] == "legacy-skill" for s in list_resp.json()["skills"])  # 只读兜底可见
        resp = client.put(
            "/api/skills/custom/legacy-skill",
            json={"content": _skill_content("legacy-skill", "edited")},
        )
    assert resp.status_code == 400, resp.text
    # 具体引导文案（"legacy shared skill…create your own version"）按 SEC-M12
    # 纪律收敛在服务端日志，API detail 为通用文案。


# ── §4.4（2026-09-30）：skills PUT 启停鉴权 ─────────────────────────────────


def test_update_skill_toggle_requires_ownership_or_admin(monkeypatch, tmp_path):
    """非 admin 启停无主（public seed / legacy）或他人 skill → 403；自有可启停。"""
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    # 无主 public seed
    pub = skills_root / "public" / "bootstrap-skill"
    pub.mkdir(parents=True)
    (pub / "SKILL.md").write_text(_skill_content("bootstrap-skill"), encoding="utf-8")
    # 自有 custom
    _write_user_skill(user_custom_root, "my-skill", created_by=_TEST_USER)

    monkeypatch.setattr("app.gateway.routers.skills.get_extensions_config", lambda: SimpleNamespace(mcp_servers={}, skills={}))
    monkeypatch.setattr("app.gateway.routers.skills.reload_extensions_config", lambda: None)
    (tmp_path / "extensions_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(skills_router.ExtensionsConfig, "resolve_config_path", staticmethod(lambda config_path=None: tmp_path / "extensions_config.json"))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)
    with TestClient(app) as client:
        resp = client.put("/api/skills/bootstrap-skill", json={"enabled": False})
        assert resp.status_code == 403, resp.text
        assert "admin" in resp.json()["detail"]

        resp = client.put("/api/skills/my-skill", json={"enabled": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is False


# ── 评审修复（2026-09-30）：per-user 启停写入面闭环 ─────────────────────────


def test_custom_skill_toggle_writes_per_user_state_not_global(monkeypatch, tmp_path):
    """P0 修复：CUSTOM 技能启停写本用户 _skill_states.json，不落全局 extensions_config。

    修前：一律写全局 extensions_config.json——用户 A 禁用后所有用户的 AND 视图
    同步变 off（跨用户互相影响）；set_skill_enabled_state 零调用方。
    """
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    _write_user_skill(user_custom_root, "my-skill", created_by=_TEST_USER)
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(skills_router.ExtensionsConfig, "resolve_config_path", staticmethod(lambda config_path=None: extensions_path))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)

    app = _make_test_app(config)
    with TestClient(app) as client:
        resp = client.put("/api/skills/my-skill", json={"enabled": False})
        assert resp.status_code == 200, resp.text
        assert resp.json()["enabled"] is False

    # 全局文件不得出现该键（公共技能才写这里）
    assert "my-skill" not in json.loads(extensions_path.read_text(encoding="utf-8")).get("skills", {})
    # 本用户 per-user 状态文件已落禁用
    from deerflow.config.paths import Paths as _Paths

    states_file = _Paths(base_dir=tmp_path).user_skills_dir(_TEST_USER) / "_skill_states.json"
    assert states_file.exists(), "per-user _skill_states.json must be written"
    assert json.loads(states_file.read_text(encoding="utf-8")) == {"my-skill": {"enabled": False}}


def test_legacy_toggle_by_admin_is_per_user_other_user_unaffected(monkeypatch, tmp_path):
    """LEGACY（旧全局 custom）启停同样 per-user：admin 禁用只影响自己视图。"""
    config, skills_root, user_custom_root = _setup_storage_env(monkeypatch, tmp_path)
    legacy_dir = skills_root / "custom" / "legacy-skill"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "SKILL.md").write_text(_skill_content("legacy-skill"), encoding="utf-8")
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(skills_router.ExtensionsConfig, "resolve_config_path", staticmethod(lambda config_path=None: extensions_path))

    async def _refresh():
        return None

    monkeypatch.setattr("app.gateway.routers.skills.refresh_skills_system_prompt_cache_async", _refresh)
    # _check_skill_ownership 对 legacy 走 admin 门：本测试以 admin 身份操作
    admin_user = SimpleNamespace(system_role="admin", id="admin-user")
    monkeypatch.setattr(skills_router, "get_optional_user_from_request", lambda request: _coro_return(admin_user))

    app = _make_test_app(config)
    from deerflow.skills.storage import get_or_new_user_skill_storage, reset_user_skill_storage

    admin_storage = get_or_new_user_skill_storage(_TEST_USER, app_config=config)
    with TestClient(app) as client:
        resp = client.put("/api/skills/legacy-skill", json={"enabled": False})
        assert resp.status_code == 200, resp.text

    # admin（contextvar 用户）per-user 状态已落；另一用户 bob 视图不受影响
    assert admin_storage.get_skill_enabled_state("legacy-skill") is False
    reset_user_skill_storage()
    bob_storage = get_or_new_user_skill_storage("bob", app_config=config)
    assert bob_storage.get_skill_enabled_state("legacy-skill") is True
    assert "legacy-skill" not in json.loads(extensions_path.read_text(encoding="utf-8")).get("skills", {})


def _coro_return(value):
    async def _inner():
        return value

    return _inner()

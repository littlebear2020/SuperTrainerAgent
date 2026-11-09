import json
from uuid import UUID

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config, get_current_user_from_request, get_optional_user_from_request
from app.gateway.routers import gallery
from deerflow.config.app_config import AppConfig

# Create a test-specific FastAPI app and register the gallery router
app = FastAPI()
app.include_router(gallery.router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    """The module-level app is shared by every test in this file — clear
    dependency_overrides after each test so a per-test override (e.g. the
    401-raising auth stub) can never leak into the next test."""
    yield
    app.dependency_overrides.clear()


class MockUser:
    def __init__(self, user_id="12345678-1234-5678-1234-567812345678", email="creator@example.com"):
        self.id = UUID(user_id) if isinstance(user_id, str) and "-" in user_id else user_id
        self.email = email


@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    """Set up mock file paths and configurations inside pytest's tmp_path."""
    gallery.load_gallery_cases.cache_clear()

    # Paths
    yaml_file = tmp_path / "gallery-cases.yaml"
    ugc_file = tmp_path / "ugc-cases.json"
    likes_file = tmp_path / "likes.json"

    # Mock file locations in gallery router module
    monkeypatch.setattr(gallery, "GALLERY_FILE_PATH", yaml_file)
    monkeypatch.setattr(gallery, "UGC_FILE_PATH", ugc_file)
    monkeypatch.setattr(gallery, "LIKES_FILE_PATH", likes_file)
    monkeypatch.setattr(gallery, "DB_DIR", tmp_path)

    # Standard Mock YAML content
    yaml_content = {
        "cases": [
            {"id": "official-1", "title": "Official Case 1", "skill_name": "skill-1", "category": "category-A", "prompt": "official prompt 1", "featured": True},
            {"id": "official-2", "title": "Official Case 2", "skill_name": "skill-2", "category": "category-B", "prompt": "official prompt 2"},
        ]
    }
    yaml_file.write_text(yaml.dump(yaml_content), encoding="utf-8")

    # Standard Mock UGC JSON content
    ugc_content = [
        {
            "id": "ugc-1",
            "title": "UGC Case 1",
            "skill_name": "skill-1",
            "category": "category-A",
            "prompt": "ugc prompt 1",
            "author_id": "other-user-id",
            "author_name": "other",
            "source_type": "ugc",
            "created_at": "2026-05-26T12:00:00Z",
            "status": "active",
        }
    ]
    ugc_file.write_text(json.dumps(ugc_content), encoding="utf-8")

    # Standard Mock Likes JSON content
    likes_content = {"ugc-1": {"12345678-1234-5678-1234-567812345678": "2026-05-26T12:05:00Z"}}
    likes_file.write_text(json.dumps(likes_content), encoding="utf-8")

    # Mock AppConfig with gallery enabled
    mock_config = AppConfig.model_validate({"log_level": "info", "sandbox": {"use": "test"}, "gallery": {"enabled": True}})

    # FastAPI Dependency Overrides
    app.dependency_overrides[get_config] = lambda: mock_config
    app.dependency_overrides[get_optional_user_from_request] = lambda: MockUser()
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser()

    yield {"yaml_file": yaml_file, "ugc_file": ugc_file, "likes_file": likes_file, "config": mock_config}

    # Cleanup overrides
    app.dependency_overrides.clear()


def test_get_gallery_settings(mock_env):
    """Test retrieving feature flag settings."""
    response = client.get("/api/gallery/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["ugc_enabled"] is True


def test_get_gallery_settings_disabled(mock_env):
    """Test retrieving settings when the gallery is fully disabled in config."""
    disabled_config = AppConfig.model_validate({"log_level": "info", "sandbox": {"use": "test"}, "gallery": {"enabled": False, "ugc_enabled": False}})
    app.dependency_overrides[get_config] = lambda: disabled_config

    response = client.get("/api/gallery/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is False
    assert data["ugc_enabled"] is False


def test_list_gallery_cases_merged_success(mock_env):
    """Test that official and UGC cases are merged and sorted correctly."""
    response = client.get("/api/gallery/cases")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    cases = data["cases"]

    # Sorting should place 'featured' first:
    # 'official-1' is featured, others are not.
    assert cases[0]["id"] == "official-1"
    assert cases[0]["featured"] is True
    assert cases[0]["source_type"] == "official"

    # Next is ugc-1 (has created_at) vs official-2 (no created_at -> blank string)
    assert cases[1]["id"] == "ugc-1"
    assert cases[1]["source_type"] == "ugc"
    assert cases[1]["likes"] == 1
    assert cases[1]["liked_by_me"] is True
    # SEC-M23（批次 2 项 2.11）：公开列表作者名掩码（读侧覆盖存量行）。
    assert cases[1]["author_name"] == "ot***"

    assert cases[2]["id"] == "official-2"
    assert cases[2]["source_type"] == "official"


def test_list_gallery_cases_filter_and_disabled(mock_env):
    """Test filtering by category and checking cases load when gallery is disabled."""
    # Filter by category-A
    response = client.get("/api/gallery/cases?category=category-A")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {c["id"] for c in data["cases"]} == {"official-1", "ugc-1"}

    # Set gallery disabled (should skip loading UGC JSON cases)
    disabled_config = AppConfig.model_validate({"log_level": "info", "sandbox": {"use": "test"}, "gallery": {"enabled": False}})
    app.dependency_overrides[get_config] = lambda: disabled_config

    response = client.get("/api/gallery/cases")
    assert response.status_code == 200
    data = response.json()
    # Should only return official cases
    assert data["total"] == 2
    assert all(c["source_type"] == "official" for c in data["cases"])


def test_create_ugc_case_success(mock_env):
    """Test sharing/creating a community case."""
    post_data = {"title": "My Awesome Prompt", "prompt": "Tell me a story about gravity", "category": "内容创作", "skill_name": "writer"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    data = response.json()

    assert data["id"] is not None
    assert data["title"] == "My Awesome Prompt"
    assert data["prompt"] == "Tell me a story about gravity"
    assert data["category"] == "内容创作"
    assert data["skill_name"] == "writer"
    assert data["source_type"] == "ugc"
    assert data["author_name"] == "cr***"  # SEC-M23: 邮箱前缀掩码（creator@example.com）
    assert data["thumbnail"] is None
    assert data["likes"] == 0
    assert data["liked_by_me"] is False


def test_create_ugc_case_preserves_inactive_cases(mock_env):
    """2026-07-27 评审 C-F2：create 曾用过滤版 load_ugc_cases() 做 read-modify-write，
    与 _load_ugc_cases_raw 的 docstring（"Write paths MUST use this"）自相矛盾——
    任何非 active 案例（软删除/下架/审核）会在下一次创建时被静默抹掉。
    PATCH/DELETE 已用 raw 版，create 漏改。"""
    import json as _json

    ugc_cases = _json.loads(mock_env["ugc_file"].read_text(encoding="utf-8"))
    ugc_cases.append(
        {
            "id": "ugc-archived",
            "title": "已下架案例",
            "category": "category-A",
            "prompt": "archived",
            "author_id": "other-user-id",
            "source_type": "ugc",
            "status": "archived",  # 非 active——过滤版 load 会丢它
        }
    )
    mock_env["ugc_file"].write_text(_json.dumps(ugc_cases), encoding="utf-8")

    resp = client.post("/api/gallery/cases", json={"title": "新案例", "prompt": "p", "category": "办公效率"})
    assert resp.status_code == 200

    on_disk = _json.loads(mock_env["ugc_file"].read_text(encoding="utf-8"))
    ids = [c["id"] for c in on_disk]
    assert "ugc-archived" in ids, "非 active 案例在 create 写回时被静默丢弃"
    assert resp.json()["id"] in ids


def test_create_ugc_case_unauthorized(mock_env):
    """Test sharing fails when the user is not authenticated."""
    from fastapi import HTTPException, Request

    # Overriding get_current_user_from_request to raise a 401 Unauthorized
    async def raise_unauthorized(request: Request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user_from_request] = raise_unauthorized

    post_data = {"title": "My Awesome Prompt", "prompt": "Tell me a story about gravity", "category": "内容创作"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 401


def test_create_ugc_case_gallery_disabled(mock_env):
    """Test sharing fails when the gallery flag is disabled."""
    disabled_config = AppConfig.model_validate({"log_level": "info", "sandbox": {"use": "test"}, "gallery": {"enabled": False}})
    app.dependency_overrides[get_config] = lambda: disabled_config

    post_data = {"title": "My Awesome Prompt", "prompt": "Tell me a story about gravity", "category": "内容创作"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"]


def test_toggle_like_case_success(mock_env):
    """Test liking and unliking toggles correctly with user deduplication."""
    # Currently: ugc-1 is liked by "test-user-id-123" (liked_by_me = True)

    # 1. Unlike ugc-1 (click toggle when already liked)
    response = client.post("/api/gallery/cases/ugc-1/like")
    assert response.status_code == 200
    data = response.json()
    assert data["likes"] == 0
    assert data["liked_by_me"] is False

    # 2. Like ugc-1 again (click toggle when unliked)
    response = client.post("/api/gallery/cases/ugc-1/like")
    assert response.status_code == 200
    data = response.json()
    assert data["likes"] == 1
    assert data["liked_by_me"] is True

    # 3. Like official case "official-1"
    response = client.post("/api/gallery/cases/official-1/like")
    assert response.status_code == 200
    data = response.json()
    assert data["likes"] == 1
    assert data["liked_by_me"] is True


def test_toggle_like_nonexistent_case(mock_env):
    """Test liking a nonexistent case returns 404."""
    response = client.post("/api/gallery/cases/nonexistent-case-id/like")
    assert response.status_code == 404


def test_create_ugc_case_null_email_fallback(mock_env):
    """Test creating a UGC case when user.email is None (defensive check)."""
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser(user_id="12345678-1234-5678-1234-567812345678", email=None)

    post_data = {"title": "Null Email Prompt", "prompt": "Test story", "category": "内容创作"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    data = response.json()
    assert data["author_name"] == "an***"  # SEC-M23: 掩码同样作用于 anonymous 兜底名


def test_toggle_like_multi_user(mock_env):
    """Test that multiple users can like the same case independently."""
    # User A (12345678-1234-5678-1234-567812345678) has liked ugc-1 in mock_env
    # Now user B likes ugc-1
    user_b_id = "87654321-4321-8765-4321-876543210987"
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser(user_id=user_b_id, email="user_b@example.com")

    response = client.post("/api/gallery/cases/ugc-1/like")
    assert response.status_code == 200
    data = response.json()
    assert data["likes"] == 2
    assert data["liked_by_me"] is True

    # User A likes again (which unlikes it)
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser(user_id="12345678-1234-5678-1234-567812345678", email="creator@example.com")
    response = client.post("/api/gallery/cases/ugc-1/like")
    assert response.status_code == 200
    data = response.json()
    assert data["likes"] == 1  # User A unliked, so only User B is left
    assert data["liked_by_me"] is False


@pytest.mark.anyio
async def test_concurrent_create_ugc_cases(mock_env):
    """Test concurrent creations to verify that asyncio lock prevents race conditions."""
    import anyio
    import httpx

    # Httpx AsyncClient allows making true concurrent requests to the FastAPI ASGI app!
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:

        async def post_case(index):
            return await ac.post("/api/gallery/cases", json={"title": f"Concurrent {index}", "prompt": f"Prompt {index}", "category": "内容创作"})

        # Run 5 concurrent posts
        results = []
        async with anyio.create_task_group() as tg:
            for i in range(5):

                async def worker(index=i):
                    res = await post_case(index)
                    results.append(res)

                tg.start_soon(worker)

        # Verify all succeeded
        assert all(res.status_code == 200 for res in results)

        # Verify all 5 cases are present in the JSON file
        with open(mock_env["ugc_file"], encoding="utf-8") as f:
            cases = json.load(f)
        # There was 1 pre-existing ugc case, so total should be 6
        assert len(cases) == 6


def test_list_cases_with_recipe_and_showcase(mock_env):
    """Test that recipe and showcase fields are parsed from YAML."""
    gallery.load_gallery_cases.cache_clear()
    # Clear UGC data so only the official recipe case is returned
    mock_env["ugc_file"].write_text("[]", encoding="utf-8")

    yaml_with_recipe = {
        "cases": [
            {
                "id": "recipe-1",
                "title": "Recipe Case",
                "skill_name": "test-skill",
                "category": "视觉设计",
                "prompt": "fallback prompt",
                "recipe": {
                    "title": "Recipe Case",
                    "goal": "Test goal",
                    "context_blocks": ["You are an expert"],
                    "steps": [
                        {
                            "instruction": "Step 1",
                            "template": "Do {thing}",
                            "tip": "Be specific",
                            "vars": {
                                "thing": {
                                    "label": "What to do?",
                                    "type": "string",
                                    "required": True,
                                }
                            },
                        }
                    ],
                    "pitfalls": ["Don't be vague"],
                },
                "showcase": {
                    "type": "image",
                    "items": [{"url": "/test.png", "caption": "Result"}],
                },
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_with_recipe), encoding="utf-8")

    response = client.get("/api/gallery/cases")
    assert response.status_code == 200
    data = response.json()
    case = data["cases"][0]

    assert case["recipe"] is not None
    assert case["recipe"]["goal"] == "Test goal"
    assert len(case["recipe"]["steps"]) == 1
    assert case["recipe"]["steps"][0]["tip"] == "Be specific"
    assert case["showcase"] is not None
    assert case["showcase"]["type"] == "image"
    assert case["showcase"]["items"][0]["caption"] == "Result"


def test_list_cases_recipe_parse_failure_degrades(mock_env):
    """Test that invalid recipe data degrades to None (no crash)."""
    gallery.load_gallery_cases.cache_clear()
    # Clear UGC data so only the official recipe case is returned
    mock_env["ugc_file"].write_text("[]", encoding="utf-8")

    yaml_bad_recipe = {
        "cases": [
            {
                "id": "bad-recipe-1",
                "title": "Bad Recipe",
                "category": "视觉设计",
                "prompt": "fallback",
                "recipe": {"invalid": "no steps field"},
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_bad_recipe), encoding="utf-8")

    response = client.get("/api/gallery/cases")
    assert response.status_code == 200
    case = response.json()["cases"][0]
    assert case["recipe"] is None
    assert case["prompt"] == "fallback"


def test_list_cases_showcase_parse_failure_degrades(mock_env):
    """Test that invalid showcase data degrades to None (no crash)."""
    gallery.load_gallery_cases.cache_clear()
    mock_env["ugc_file"].write_text("[]", encoding="utf-8")

    yaml_bad_showcase = {
        "cases": [
            {
                "id": "bad-showcase-1",
                "title": "Bad Showcase",
                "category": "视觉设计",
                "prompt": "fallback",
                "showcase": {"type": "invalid_type", "items": []},
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_bad_showcase), encoding="utf-8")

    response = client.get("/api/gallery/cases")
    assert response.status_code == 200
    case = response.json()["cases"][0]
    assert case["showcase"] is None
    assert case["prompt"] == "fallback"


def test_compile_template_function():
    """Test the backend template compiler."""
    from app.gateway.routers.gallery import _compile_template

    result = _compile_template("为《{topic}》生成封面", {"topic": "AI报告"})
    assert result == "为《AI报告》生成封面"

    # Unmatched variable stays as-is
    result = _compile_template("测试{unknown}", {})
    assert result == "测试{unknown}"


def test_instantiate_not_found(mock_env):
    """Test instantiate returns 404 for unknown case."""
    response = client.post("/api/gallery/cases/nonexistent/instantiate", json={"vars": {}})
    assert response.status_code == 404


def test_instantiate_template_mode(mock_env):
    """Test instantiate with template mode case."""
    from unittest.mock import AsyncMock, patch

    gallery.load_gallery_cases.cache_clear()
    yaml_content = {
        "cases": [
            {
                "id": "tmpl-1",
                "title": "Template Case",
                "skill_name": "skill-1",
                "category": "视觉设计",
                "prompt_template": "为《{topic}》生成封面，{style}风格",
                "default_vars": {"topic": "默认", "style": "科技"},
                "vars_config": {
                    "topic": {"label": "标题", "type": "string", "required": True},
                    "style": {"label": "风格", "type": "select", "options": ["科技", "极简"]},
                },
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_content), encoding="utf-8")

    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new_callable=AsyncMock) as mock_sr:
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()

        response = client.post(
            "/api/gallery/cases/tmpl-1/instantiate",
            json={
                "vars": {"topic": "我的文章", "style": "科技"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "thread_id" in data

    # Verify start_run was called with compiled prompt
    mock_sr.assert_called_once()
    call_args = mock_sr.call_args
    run_body = call_args[0][0]  # first positional arg
    messages = run_body.input["messages"]
    user_msg = [m for m in messages if m.get("role") == "user"][0]
    assert "我的文章" in user_msg["content"]
    assert "科技" in user_msg["content"]


def test_instantiate_recipe_thread_has_title_and_guidance(mock_env):
    """instantiate should set the case title as thread metadata and inject a
    guidance system message naming the case + total step count (Task 11)."""
    from unittest.mock import AsyncMock, patch

    gallery.load_gallery_cases.cache_clear()
    yaml_content = {
        "cases": [
            {
                "id": "recipe-1",
                "title": "赛博朋克封面法",
                "skill_name": "skill-1",
                "category": "视觉设计",
                "prompt": "p",
                "recipe": {
                    "title": "赛博朋克封面法",
                    "goal": "生成赛博朋克风封面",
                    "approach": "风格化",
                    "difficulty": "intermediate",
                    "est_steps": 3,
                    "context_blocks": ["你是赛博朋克设计师"],
                    "steps": [
                        {
                            "instruction": "描述主题",
                            "template": "为《{topic}》生成赛博朋克封面",
                            "vars": {"topic": {"label": "主题", "type": "string", "required": True}},
                        },
                        {"instruction": "选风格", "template": "确定风格基调"},
                        {"instruction": "定受众", "template": "明确目标受众"},
                    ],
                    "pitfalls": [],
                },
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_content), encoding="utf-8")

    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new_callable=AsyncMock) as mock_sr:
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()

        response = client.post(
            "/api/gallery/cases/recipe-1/instantiate",
            json={"vars": {"topic": "AI 的未来"}},
        )

    assert response.status_code == 200

    # 1) thread metadata includes the case title (not just gallery_case_id)
    mock_ts.return_value.create.assert_called_once()
    create_kwargs = mock_ts.return_value.create.call_args.kwargs
    assert create_kwargs["metadata"].get("title") == "赛博朋克封面法"

    # 2) system guidance message names the case + total step count
    mock_sr.assert_called_once()
    run_body = mock_sr.call_args[0][0]
    messages = run_body.input["messages"]
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    assert len(sys_msgs) == 1
    assert "赛博朋克封面法" in sys_msgs[0]["content"]
    assert "复刻" in sys_msgs[0]["content"]
    assert "3 步" in sys_msgs[0]["content"]
    # context_blocks still appended after the guidance
    assert "赛博朋克设计师" in sys_msgs[0]["content"]


# ---------------------------------------------------------------------------
# UGC recipe + thumbnail persistence (Problem 4 + Problem 2)
# ---------------------------------------------------------------------------

_VALID_RECIPE = {
    "title": "封面法",
    "goal": "生成封面图",
    "approach": "渐进式细化法",
    "difficulty": "beginner",
    "est_steps": 2,
    "context_blocks": ["你是一位设计师"],
    "steps": [{"instruction": "描述主题", "template": "为《{topic}》生成封面"}],
    "pitfalls": ["不要太抽象"],
}


def test_create_ugc_case_persists_recipe(mock_env):
    """POST with recipe -> persisted to ugc-cases.json and returned."""
    post_data = {"title": "Recipe Case", "prompt": "p", "category": "视觉设计", "recipe": _VALID_RECIPE}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    data = response.json()
    assert data["recipe"] is not None
    assert data["recipe"]["goal"] == "生成封面图"

    saved = json.loads(mock_env["ugc_file"].read_text(encoding="utf-8"))
    ours = [c for c in saved if c["title"] == "Recipe Case"][0]
    assert ours["recipe"]["goal"] == "生成封面图"


def test_create_ugc_case_persists_local_thumbnail(mock_env):
    """POST with a local thumbnail URL -> persisted + returned."""
    post_data = {"title": "Thumb Case", "prompt": "p", "category": "视觉设计", "thumbnail": "/api/gallery/thumbnails/abc.png"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    assert response.json()["thumbnail"] == "/api/gallery/thumbnails/abc.png"


def test_create_ugc_case_rejects_non_local_thumbnail_url(mock_env):
    """Non-local thumbnail (SSRF) -> coerced to None, share still succeeds."""
    post_data = {"title": "SSRF Case", "prompt": "p", "category": "视觉设计", "thumbnail": "https://evil.example/x.png"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    assert response.json()["thumbnail"] is None


def test_create_ugc_case_thumbnail_optional(mock_env):
    """Omitting thumbnail still works (backward compatible)."""
    post_data = {"title": "No Thumb", "prompt": "p", "category": "视觉设计"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    assert response.json()["thumbnail"] is None


# ---------------------------------------------------------------------------
# liked_only filter (Problem 3)
# ---------------------------------------------------------------------------


def test_list_cases_liked_only(mock_env):
    """liked_only=true returns only cases the current user liked."""
    # mock_env seeds ugc-1 as liked by the mock user
    response = client.get("/api/gallery/cases?liked_only=true")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["cases"][0]["id"] == "ugc-1"


def test_list_cases_liked_only_anonymous_empty(mock_env):
    """Anonymous user with liked_only=true -> empty list."""
    app.dependency_overrides[get_optional_user_from_request] = lambda: None
    response = client.get("/api/gallery/cases?liked_only=true")
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_list_cases_liked_only_filters_correctly(mock_env):
    """Like official-1 too; liked_only returns both liked, excludes others."""
    likes = json.loads(mock_env["likes_file"].read_text(encoding="utf-8"))
    uid = "12345678-1234-5678-1234-567812345678"
    likes["official-1"] = {uid: "2026-06-18T00:00:00Z"}
    mock_env["likes_file"].write_text(json.dumps(likes), encoding="utf-8")

    response = client.get("/api/gallery/cases?liked_only=true")
    data = response.json()
    assert data["total"] == 2
    assert {c["id"] for c in data["cases"]} == {"ugc-1", "official-1"}


def test_create_ugc_case_rejects_invalid_recipe(mock_env):
    """Invalid recipe dict -> coerced to None before persisting, share succeeds."""
    bad_recipe = {"invalid": "no steps field"}
    post_data = {"title": "Bad Recipe", "prompt": "p", "category": "视觉设计", "recipe": bad_recipe}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    # Invalid recipe degrades to None in the response.
    assert response.json()["recipe"] is None

    # And is NOT persisted into the JSON store.
    saved = json.loads(mock_env["ugc_file"].read_text(encoding="utf-8"))
    ours = [c for c in saved if c["title"] == "Bad Recipe"][0]
    assert ours.get("recipe") is None


def test_create_ugc_case_validates_and_persists_recipe(mock_env):
    """Valid recipe dict -> validated then persisted as a clean dict."""
    valid_recipe = {
        "title": "封面法",
        "goal": "生成封面图",
        "steps": [{"instruction": "描述主题", "template": "为《{topic}》生成封面"}],
    }
    post_data = {"title": "Good Recipe", "prompt": "p", "category": "视觉设计", "recipe": valid_recipe}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 200
    assert response.json()["recipe"] is not None
    assert response.json()["recipe"]["goal"] == "生成封面图"


def test_get_gallery_settings_independent_ugc_flag(mock_env):
    """gallery.enabled=true but ugc_enabled=false must be reflected independently."""
    ugc_off_config = AppConfig.model_validate(
        {
            "log_level": "info",
            "sandbox": {"use": "test"},
            "gallery": {"enabled": True, "ugc_enabled": False},
        }
    )
    app.dependency_overrides[get_config] = lambda: ugc_off_config

    response = client.get("/api/gallery/settings")
    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["ugc_enabled"] is False


def test_create_ugc_case_rejects_unknown_category(mock_env):
    """category not in the whitelist -> 422, not persisted (Task 13)."""
    post_data = {"title": "Bad Cat", "prompt": "p", "category": "不存在的分类"}
    response = client.post("/api/gallery/cases", json=post_data)
    assert response.status_code == 422


def test_get_gallery_categories_returns_whitelist(mock_env):
    """GET /categories returns the single-source whitelist (Task 13)."""
    response = client.get("/api/gallery/categories")
    assert response.status_code == 200
    cats = response.json()["categories"]
    assert isinstance(cats, list)
    assert "视觉设计" in cats
    assert set(cats) == {"办公效率", "内容创作", "视觉设计", "数据分析", "视频创作"}


# ---------------------------------------------------------------------------
# 2026-07-27 T8：UGC 案例作者删改（PATCH/DELETE + author_id/editable_by_me）
# ---------------------------------------------------------------------------


def _create_case_as_current_user(client) -> str:
    """POST 建一条 UGC（author = 当前 MockUser），返回 case id。"""
    resp = client.post(
        "/api/gallery/cases",
        json={"title": "我的案例", "prompt": "我的 prompt", "category": "办公效率"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_owner_can_patch_own_case(mock_env):
    case_id = _create_case_as_current_user(client)
    resp = client.patch(
        f"/api/gallery/cases/{case_id}",
        json={"title": "改后的标题", "prompt": "改后的 prompt"},
    )
    assert resp.status_code == 200
    assert resp.json()["title"] == "改后的标题"
    assert resp.json()["prompt"] == "改后的 prompt"


def test_non_owner_patch_forbidden(mock_env):
    # ugc-1 的 author_id="other-user-id" ≠ 默认 MockUser id → 越权
    resp = client.patch("/api/gallery/cases/ugc-1", json={"title": "越权"})
    assert resp.status_code == 403


def test_official_case_patch_forbidden(mock_env):
    resp = client.patch("/api/gallery/cases/official-1", json={"title": "官方不可改"})
    assert resp.status_code in (403, 404)


def test_owner_delete_removes_case_and_likes(mock_env):
    case_id = _create_case_as_current_user(client)
    client.post(f"/api/gallery/cases/{case_id}/like")
    resp = client.delete(f"/api/gallery/cases/{case_id}")
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    ids = [c["id"] for c in client.get("/api/gallery/cases").json()["cases"]]
    assert case_id not in ids
    # likes 同步清理
    assert case_id not in json.loads(mock_env["likes_file"].read_text(encoding="utf-8"))


def test_non_owner_delete_forbidden(mock_env):
    assert client.delete("/api/gallery/cases/ugc-1").status_code == 403


def test_delete_holds_likes_lock_during_cleanup(mock_env, monkeypatch):
    """2026-07-27 评审 C-F3：DELETE 清理 likes.json 必须持 likes_write_lock——
    toggle_like 用同一把锁做 read-modify-write，不持锁会交错产生 lost-update。"""
    case_id = _create_case_as_current_user(client)
    client.post(f"/api/gallery/cases/{case_id}/like")

    held: dict[str, bool] = {}
    real_load = gallery.load_likes

    def spy_load():
        held["locked"] = gallery.likes_write_lock.locked()
        return real_load()

    monkeypatch.setattr(gallery, "load_likes", spy_load)
    resp = client.delete(f"/api/gallery/cases/{case_id}")
    assert resp.status_code == 200
    assert held.get("locked") is True, "DELETE 清理 likes 时未持 likes_write_lock"


def test_response_carries_author_id_and_editable_flag(mock_env):
    case_id = _create_case_as_current_user(client)
    cases = {c["id"]: c for c in client.get("/api/gallery/cases").json()["cases"]}
    assert cases[case_id]["author_id"] is not None
    assert cases[case_id]["editable_by_me"] is True
    assert cases["ugc-1"]["editable_by_me"] is False  # 他人案例


def test_patch_invalid_category_422(mock_env):
    case_id = _create_case_as_current_user(client)
    resp = client.patch(f"/api/gallery/cases/{case_id}", json={"category": "不存在的分类"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2026-07-27 T12：defer instantiate（文件类灵感两步流）
# ---------------------------------------------------------------------------


def _write_recipe_case_yaml(mock_env):
    """recipe-1 案例 fixture（与 test_instantiate_recipe_thread_has_title_and_guidance 同构）。"""
    gallery.load_gallery_cases.cache_clear()
    yaml_content = {
        "cases": [
            {
                "id": "recipe-1",
                "title": "赛博朋克封面法",
                "skill_name": "skill-1",
                "category": "视觉设计",
                "prompt": "p",
                "recipe": {
                    "title": "赛博朋克封面法",
                    "goal": "生成赛博朋克风封面",
                    "approach": "风格化",
                    "difficulty": "intermediate",
                    "est_steps": 3,
                    "context_blocks": ["你是赛博朋克设计师"],
                    "steps": [
                        {
                            "instruction": "描述主题",
                            "template": "为《{topic}》生成赛博朋克封面",
                            "vars": {"topic": {"label": "主题", "type": "string", "required": True}},
                        },
                        {"instruction": "选风格", "template": "确定风格基调"},
                        {"instruction": "定受众", "template": "明确目标受众"},
                    ],
                    "pitfalls": [],
                },
            }
        ]
    }
    mock_env["yaml_file"].write_text(yaml.dump(yaml_content), encoding="utf-8")


def test_instantiate_defer_creates_thread_without_starting_run(mock_env):
    """defer=true：建线程 + 回传 compiled_prompt，但不得 start_run（文件类灵感两步流）。"""
    from unittest.mock import AsyncMock, patch

    _write_recipe_case_yaml(mock_env)
    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new_callable=AsyncMock) as mock_sr:
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()
        resp = client.post(
            "/api/gallery/cases/recipe-1/instantiate",
            json={"vars": {"topic": "任意值"}, "defer": True},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["thread_id"]
    assert body["compiled_prompt"], "defer 必须回传编译后的首条用户消息"
    assert "【灵感库案例《" in body["compiled_prompt"], "defer prompt 须带案例前缀（system guidance 的替代）"
    assert "任意值" in body["compiled_prompt"]
    mock_sr.assert_not_called()


def test_instantiate_default_still_starts_run(mock_env):
    """defer 缺省 false：现行为不回归（start_run 照常调用）。"""
    from unittest.mock import AsyncMock, patch

    _write_recipe_case_yaml(mock_env)
    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new_callable=AsyncMock) as mock_sr:
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()
        resp = client.post("/api/gallery/cases/recipe-1/instantiate", json={"vars": {}})
    assert resp.status_code == 200
    mock_sr.assert_called_once()


# ---------------------------------------------------------------------------
# 2026-07-31 A1：instantiate 返回 run_id（灵感库「假死」修复）
# ---------------------------------------------------------------------------


def test_instantiate_returns_run_id(mock_env):
    """非 defer 分支必须回传 run_id——前端据此写 useStream 重连键
    （lg:stream:{thread_id}）接续这个服务端创建的 run，否则页面静止（假死）。"""
    from unittest.mock import AsyncMock, patch

    _write_recipe_case_yaml(mock_env)
    captured: dict = {}

    class _FakeRecord:
        run_id = "run-test-123"

    async def fake_start_run(body, thread_id, request):
        captured["thread_id"] = thread_id
        return _FakeRecord()

    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new=fake_start_run):
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()
        resp = client.post("/api/gallery/cases/recipe-1/instantiate", json={"vars": {}})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == "run-test-123"
    assert body["thread_id"] == captured["thread_id"], "run 必须启动在返回的那个 thread 上"


def test_instantiate_defer_has_no_run_id(mock_env):
    """defer 分支不启动 run，因此不得回传 run_id（前端不应写重连键）。"""
    from unittest.mock import AsyncMock, patch

    _write_recipe_case_yaml(mock_env)
    with patch("app.gateway.deps.get_thread_store") as mock_ts, patch("app.gateway.deps.get_checkpointer") as mock_ck, patch("app.gateway.services.start_run", new_callable=AsyncMock) as mock_sr:
        mock_ts.return_value.create = AsyncMock()
        mock_ck.return_value.aput = AsyncMock()
        resp = client.post(
            "/api/gallery/cases/recipe-1/instantiate",
            json={"vars": {"topic": "任意值"}, "defer": True},
        )
    assert resp.status_code == 200
    assert "run_id" not in resp.json()
    mock_sr.assert_not_called()

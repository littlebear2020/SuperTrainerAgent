"""Tests for Recipe Card schema validation and POST /api/gallery/extract-recipe endpoint."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.store.memory import InMemoryStore
from pydantic import ValidationError

from app.gateway.deps import get_config, get_current_user_from_request
from app.gateway.gallery.prompts import format_messages_for_extraction
from app.gateway.gallery.schemas import (
    Recipe,
    RecipeStep,
    RecipeStepVar,
    ShowcaseConfig,
    ShowcaseItem,
)
from app.gateway.routers import gallery
from deerflow.config.app_config import AppConfig
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore

# Shared FastAPI app + test client for extract-recipe endpoint tests
app = FastAPI()
app.include_router(gallery.router)
client = TestClient(app)


# ---------------------------------------------------------------------------
# Schema validation tests (Task 1)
# ---------------------------------------------------------------------------


class TestRecipeStepVar:
    def test_string_type_defaults(self):
        var = RecipeStepVar(label="主题")
        assert var.type == "string"
        assert var.required is False
        assert var.options is None

    def test_select_type_with_options(self):
        var = RecipeStepVar(
            label="颜色",
            type="select",
            options=["蓝色", "红色"],
        )
        assert var.type == "select"
        assert var.options == ["蓝色", "红色"]

    def test_file_type_with_accept(self):
        """2026-07-27 评审 C-F1：提取 prompt 规则 7 指令 LLM 产出 type='file' 变量，
        但 RecipeStepVar.type 曾是 Literal['string','select'] → extract-recipe 校验
        422，文件类灵感主路径断裂。schema 必须与顶层 VarConfig（routers/gallery.py）
        对齐接受 file + accept/multiple。"""
        var = RecipeStepVar(label="源文档", type="file", required=True, accept=".pdf,.docx")
        assert var.type == "file"
        assert var.accept == ".pdf,.docx"
        assert var.multiple is False

    def test_recipe_with_file_var_step_validates(self):
        """端到端：LLM 按规则 7 产出的含 file 变量的 recipe 必须过 Recipe 校验
        （修前 ValidationError → extract-recipe 端点 422）。"""
        recipe = Recipe(
            title="文档转PPT",
            goal="上传文档生成 PPT",
            steps=[
                {
                    "instruction": "上传源文档",
                    "template": "解析 {doc} 并生成大纲",
                    "vars": {"doc": {"label": "源文档（PDF）", "type": "file", "required": True, "accept": ".pdf,.docx"}},
                }
            ],
        )
        dumped = recipe.model_dump()
        assert dumped["steps"][0]["vars"]["doc"]["type"] == "file"


class TestRecipeStep:
    def test_minimal_step(self):
        step = RecipeStep(instruction="描述主题", template="为《{topic}》生成封面")
        assert step.instruction == "描述主题"
        assert step.tip is None
        assert step.vars is None

    def test_full_step(self):
        step = RecipeStep(
            instruction="描述主题",
            template="为《{topic}》生成封面",
            example="为《AI发展趋势》生成封面",
            tip="先给方向",
            vars={
                "topic": RecipeStepVar(label="标题", required=True),
            },
        )
        assert step.vars["topic"].required is True


class TestRecipe:
    def test_valid_recipe(self):
        recipe = Recipe(
            title="赛博朋克封面",
            goal="生成科技风封面图",
            context_blocks=["你是数字艺术家"],
            steps=[
                RecipeStep(instruction="描述", template="为《{topic}》生成"),
            ],
            pitfalls=["不要太抽象"],
        )
        assert recipe.difficulty == "intermediate"
        assert recipe.est_steps == 3
        assert len(recipe.steps) == 1

    def test_recipe_requires_steps(self):
        with pytest.raises(ValidationError):
            Recipe(title="test", goal="test", steps=[])

    def test_showcase_optional(self):
        recipe = Recipe(
            title="test",
            goal="test",
            steps=[RecipeStep(instruction="x", template="x")],
        )
        assert recipe.showcase is None


class TestShowcaseConfig:
    def test_image_showcase(self):
        sc = ShowcaseConfig(
            type="image",
            items=[ShowcaseItem(url="/img.png", caption="结果")],
        )
        assert sc.type == "image"
        assert sc.items[0].caption == "结果"

    def test_gallery_showcase(self):
        sc = ShowcaseConfig(
            type="gallery",
            items=[
                ShowcaseItem(url="/1.png"),
                ShowcaseItem(url="/2.png"),
            ],
        )
        assert len(sc.items) == 2


# ---------------------------------------------------------------------------
# format_messages_for_extraction unit tests (Shape B: checkpoint model_dump dicts)
# ---------------------------------------------------------------------------


class TestFormatMessagesForExtraction:
    def test_human_ai_transcript(self):
        msgs = [
            {"type": "human", "content": "帮我生成封面"},
            {"type": "ai", "content": "好的，我来生成"},
        ]
        out = format_messages_for_extraction(msgs)
        assert "👤 用户: 帮我生成封面" in out
        assert "🤖 AI: 好的，我来生成" in out

    def test_skips_tool_and_system(self):
        msgs = [
            {"type": "human", "content": "hi"},
            {"type": "system", "content": "sysprompt"},
            {"type": "tool", "content": "tool result"},
            {"type": "ai", "content": "answer"},
        ]
        out = format_messages_for_extraction(msgs)
        assert "sysprompt" not in out
        assert "tool result" not in out
        assert "answer" in out

    def test_skips_ai_with_only_tool_calls(self):
        msgs = [
            {"type": "human", "content": "do x"},
            {"type": "ai", "content": "", "tool_calls": [{"name": "x"}]},
            {"type": "ai", "content": "done"},
        ]
        out = format_messages_for_extraction(msgs)
        assert out.count("🤖 AI:") == 1
        assert "done" in out

    def test_multimodal_content_list(self):
        msgs = [
            {"type": "human", "content": [{"type": "text", "text": "描述这张图"}, {"type": "image_url", "image_url": "data:..."}]},
        ]
        out = format_messages_for_extraction(msgs)
        assert "描述这张图" in out

    def test_empty_conversation(self):
        assert format_messages_for_extraction([]) == "(空对话)"
        assert format_messages_for_extraction([{"type": "tool", "content": "x"}]) == "(空对话)"

    def test_truncation(self):
        long_human = "a" * 2000
        out = format_messages_for_extraction([{"type": "human", "content": long_human}])
        assert "...[已截断]" not in out  # human cap is 2000

        long_ai = "b" * 1500
        out2 = format_messages_for_extraction([{"type": "ai", "content": long_ai}])
        assert "...[已截断]" in out2


# ---------------------------------------------------------------------------
# extract-recipe endpoint tests (Task 4)
# ---------------------------------------------------------------------------


class MockUser:
    def __init__(self, user_id="12345678-1234-5678-1234-567812345678", email="user@example.com"):
        self.id = UUID(user_id)
        self.email = email


class MockCheckpointTuple:
    """Mimics a checkpointer.aget_tuple() result with channel_values["messages"]."""

    def __init__(self, messages):
        self.checkpoint = {"channel_values": {"messages": messages}}


def _mock_checkpointer(messages):
    """Return a mock checkpointer whose aget_tuple yields the given messages.

    ``messages`` are real LangChain objects (as the checkpointer stores them);
    the real serialize_channel_values path converts them to model_dump dicts.
    """
    store = AsyncMock()
    store.aget_tuple = AsyncMock(return_value=MockCheckpointTuple(messages))
    return store


@pytest.fixture
def extract_env(monkeypatch):
    """Set up environment for extract-recipe tests."""
    mock_config = AppConfig.model_validate(
        {
            "log_level": "info",
            "sandbox": {"use": "test"},
            "gallery": {"enabled": True},
        }
    )

    app.dependency_overrides[get_config] = lambda: mock_config
    app.dependency_overrides[get_current_user_from_request] = lambda: MockUser()
    # SEC-C1 ownership gate (gallery.py:859-864) reads request.app.state.thread_store
    # via deps.get_thread_store() and 503s without it — the shared test app here
    # predates the gate and never set it, which is why the whole endpoint block
    # went red (503) after 536b2f392. Attach the real memory-backed store so the
    # gate's actual semantics (not a mock's) run in these tests (R1′, 2026-09-09).
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())

    yield

    app.dependency_overrides.clear()
    del app.state.thread_store


def _recipe_json():
    return json.dumps(
        {
            "title": "科技风封面生成",
            "goal": "生成科技风文章封面",
            "approach": "渐进式细化法",
            "difficulty": "beginner",
            "est_steps": 2,
            "context_blocks": ["你是一位专业设计师"],
            "steps": [{"instruction": "描述主题", "template": "为《{topic}》生成封面", "vars": {"topic": {"label": "标题", "type": "string", "required": True}}}],
            "pitfalls": ["不要太抽象"],
        }
    )


def test_extract_recipe_success(extract_env):
    """Test successful recipe extraction from a checkpoint."""
    messages = [
        HumanMessage(content="帮我生成一个科技风封面"),
        AIMessage(content="好的，我来为你生成..."),
        HumanMessage(content="颜色再蓝一点"),
        AIMessage(content="已调整颜色..."),
    ]
    mock_ckpt = _mock_checkpointer(messages)

    mock_response = MagicMock()
    mock_response.content = _recipe_json()

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={
                "thread_id": "test-thread",
                "title": "科技封面",
                "category": "视觉设计",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["recipe"]["goal"] == "生成科技风文章封面"
    assert len(data["recipe"]["steps"]) == 1


def test_extract_recipe_backfills_title_when_llm_omits_it(extract_env):
    """Regression (A1 bug): the extraction prompt's JSON template does NOT ask
    the LLM for a ``title`` field, but ``Recipe`` schema requires it. Real LLM
    runs therefore return a recipe dict without ``title`` → 422 in production.

    Fix: ``extract_recipe`` must backfill ``req.title`` (the title the user
    typed in the share dialog) when the LLM omits ``title``, instead of
    failing validation.
    """
    messages = [
        HumanMessage(content="帮我生成一个科技风封面"),
        AIMessage(content="好的，我来为你生成..."),
    ]
    mock_ckpt = _mock_checkpointer(messages)

    # LLM returns a recipe WITHOUT `title` — mirroring real-world output.
    recipe_without_title = json.dumps(
        {
            "goal": "生成科技风文章封面",
            "approach": "渐进式细化法",
            "difficulty": "beginner",
            "est_steps": 1,
            "context_blocks": ["你是一位专业设计师"],
            "steps": [{"instruction": "描述主题", "template": "为《{topic}》生成封面"}],
            "pitfalls": ["不要太抽象"],
        }
    )
    mock_response = MagicMock()
    mock_response.content = recipe_without_title

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={
                "thread_id": "test-thread",
                "title": "用户填写的案例标题",
                "category": "视觉设计",
            },
        )

    assert response.status_code == 200, response.text
    data = response.json()
    # The user-provided title must be backfilled into the recipe.
    assert data["recipe"]["title"] == "用户填写的案例标题"
    # Other LLM-provided fields are preserved.
    assert data["recipe"]["goal"] == "生成科技风文章封面"


def test_extract_recipe_reads_from_checkpointer_after_restart(extract_env):
    """Regression (Bug 1B): after a gateway restart the in-memory event store is
    empty, but extraction still works because it reads the persistent checkpointer."""
    messages = [
        HumanMessage(content="帮我生成一个科技风封面"),
        AIMessage(content="好的，我来为你生成..."),
    ]
    mock_ckpt = _mock_checkpointer(messages)

    mock_response = MagicMock()
    mock_response.content = _recipe_json()

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "test-thread", "title": "科技封面", "category": "视觉设计"},
        )

    assert response.status_code == 200


def test_extract_recipe_no_messages_404(extract_env):
    """No checkpoint for the thread -> 404."""
    store = AsyncMock()
    store.aget_tuple = AsyncMock(return_value=None)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=store):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "missing-thread", "title": "x", "category": "x"},
        )

    assert response.status_code == 404


def test_extract_recipe_llm_failure_returns_422(extract_env):
    """Invalid LLM output returns 422."""
    messages = [HumanMessage(content="test")]
    mock_ckpt = _mock_checkpointer(messages)

    mock_response = MagicMock()
    mock_response.content = "this is not json at all"

    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "test-thread", "title": "test", "category": "视觉设计"},
        )

    assert response.status_code == 422


def test_extract_recipe_requires_auth():
    """Test that extract-recipe requires authentication."""

    async def raise_unauthorized(request: Request):
        raise HTTPException(status_code=401, detail="Not authenticated")

    # The module-level app is shared by every test here — the 401 override
    # must not leak into later tests even if the assert fails.
    app.dependency_overrides[get_current_user_from_request] = raise_unauthorized
    try:
        response = client.post(
            "/api/gallery/extract-recipe",
            json={
                "thread_id": "x",
                "title": "x",
                "category": "x",
            },
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_current_user_from_request, None)


def test_extract_recipe_500_does_not_leak_internal_detail(extract_env):
    """Task 3: a 500 from extract-recipe must return a generic message,
    not the raw exception text (which may contain internal URLs / keys)."""
    # Provide a valid checkpoint so we get past the 404 guard, then force
    # create_chat_model to raise to exercise the 500 path.
    messages = [HumanMessage(content="hi")]
    mock_ckpt = _mock_checkpointer(messages)

    sentinel = "SECRET-INTERNAL-KEY-12345 oss://bucket/secret"

    def _boom(*args, **kwargs):
        raise RuntimeError(sentinel)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", side_effect=_boom):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "t1", "title": "x", "category": "c"},
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert sentinel not in detail
    assert "Failed to extract recipe" in detail


# ---------------------------------------------------------------------------
# R1′ (2026-09-09 requirements-v2 review plan): extract-recipe ownership gate
# ---------------------------------------------------------------------------

_OTHER_USER_ID = "99999999-9999-4999-8999-999999999999"


def _seeded_thread_store(rows: list[tuple[str, str | None]]) -> MemoryThreadMetaStore:
    """Memory store pre-seeded with (thread_id, owner_user_id) thread_meta rows.

    Uses the real MemoryThreadMetaStore so check_access's actual semantics
    (missing row / NULL owner / other owner) are what gets pinned, not a mock's.
    """
    store = MemoryThreadMetaStore(InMemoryStore())
    for thread_id, owner in rows:
        asyncio.run(store.create(thread_id, user_id=owner))
    return store


def test_extract_recipe_cross_user_thread_404(extract_env):
    """R1′: 用户 B 携用户 A 的 thread_id 调 extract-recipe → 归属 404，
    且不得触达 checkpointer（内容零泄漏，存在性不可区分）。"""
    app.state.thread_store = _seeded_thread_store([("thread-of-a", _OTHER_USER_ID)])
    probe = MagicMock()
    probe.aget_tuple = AsyncMock()

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=probe):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "thread-of-a", "title": "x", "category": "视觉设计"},
        )

    assert response.status_code == 404
    # Ownership 404 (not the "No messages" 404) and no checkpoint read at all.
    assert response.json()["detail"] == "Thread thread-of-a not found"
    probe.aget_tuple.assert_not_awaited()


def test_extract_recipe_own_thread_200(extract_env):
    """R1′: 本人线程 → 门放行，提取正常 200（对照跨用户 404）。"""
    app.state.thread_store = _seeded_thread_store([("thread-own", str(MockUser().id))])
    mock_ckpt = _mock_checkpointer([HumanMessage(content="帮我生成封面"), AIMessage(content="好的")])
    mock_response = MagicMock()
    mock_response.content = _recipe_json()
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "thread-own", "title": "科技封面", "category": "视觉设计"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["recipe"]["goal"] == "生成科技风文章封面"


def test_extract_recipe_missing_thread_404_after_gate(extract_env):
    """R1′: 不存在的 thread_id → 门放行（require_existing=False，行缺失=允许），
    404 来自后续 checkpoint 读取（"No messages"），与归属 404 同码不可区分。"""
    assert app.state.thread_store is not None
    empty_probe = AsyncMock()
    empty_probe.aget_tuple = AsyncMock(return_value=None)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=empty_probe):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "never-existed", "title": "x", "category": "x"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "No messages found in thread"
    empty_probe.aget_tuple.assert_awaited_once()  # gate passed; reached the read


def test_extract_recipe_null_owner_thread_currently_allowed(extract_env):
    """R1′ 钉住现状：NULL-owner（legacy/共享，thread_meta 行 user_id=None）线程当前**放行**
    ——与 runs 端点同口径（check_access require_existing=False，memory.py:114-121）。
    当前口径：NULL-owner 放行；将来 M1 收紧时此用例应失败并强迫显式更新口径。"""
    app.state.thread_store = _seeded_thread_store([("thread-legacy-shared", None)])
    mock_ckpt = _mock_checkpointer([HumanMessage(content="帮我生成封面"), AIMessage(content="好的")])
    mock_response = MagicMock()
    mock_response.content = _recipe_json()
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)

    with patch("app.gateway.routers.gallery.get_checkpointer", return_value=mock_ckpt), patch("app.gateway.routers.gallery.create_chat_model", return_value=mock_model):
        response = client.post(
            "/api/gallery/extract-recipe",
            json={"thread_id": "thread-legacy-shared", "title": "x", "category": "视觉设计"},
        )

    assert response.status_code == 200, response.text


def test_recipe_retry_suffix_uses_single_braces():
    """RECIPE_RETRY_SUFFIX is concatenated verbatim (no .format()), so it must
    contain single { / }, not the {{ / }} that RECIPE_EXTRACTION_*_PROMPT needs."""
    from app.gateway.gallery.prompts import RECIPE_RETRY_SUFFIX

    # The instructions about JSON boundaries must reference single braces,
    # because this suffix is never run through str.format().
    assert "第一个字符是 {" in RECIPE_RETRY_SUFFIX
    assert "最后一个字符是 }" in RECIPE_RETRY_SUFFIX
    # And must NOT contain the escaped double-brace form.
    assert "第一个字符是 {{" not in RECIPE_RETRY_SUFFIX
    assert "最后一个字符是 }}" not in RECIPE_RETRY_SUFFIX


# ---------------------------------------------------------------------------
# 2026-07-27 T11：文件类模板变量 + 附件感知提取
# ---------------------------------------------------------------------------


def test_var_config_accepts_file_type():
    from app.gateway.routers.gallery import VarConfig

    cfg = VarConfig(label="源文档", type="file", required=True, accept=".pdf,.docx")
    assert cfg.type == "file" and cfg.accept == ".pdf,.docx" and cfg.multiple is False


def test_format_messages_marks_attachments_without_filenames():
    """人消息里的文件 block 必须留下类型标记（提取才能学会"此处需上传"），
    但不得泄漏文件名/路径（隐私指令保留）。
    注：checkpoint model_dump 消息的 role 键是 ``type``（见函数 docstring）。"""
    messages = [
        {
            "type": "human",
            "content": [
                {"type": "text", "text": "帮我把这份文档做成PPT"},
                {"type": "file", "mime": "application/pdf", "filename": "年度总结-内部机密.pdf"},
            ],
        },
        {"type": "ai", "content": "好的，我先解析文档……"},
    ]
    text = format_messages_for_extraction(messages)
    assert "PDF" in text or "pdf" in text, "文件类型标记缺失"
    assert "年度总结" not in text and "内部机密" not in text, "文件名泄漏"


def test_format_messages_marks_file_only_message():
    """纯文件（无文本）的人消息也不得整条消失——标记行仍须出现。"""
    messages = [
        {
            "type": "human",
            "content": [{"type": "image", "mime": "image/png", "url": "https://x/y.png"}],
        },
    ]
    text = format_messages_for_extraction(messages)
    assert "图片" in text


# ---------------------------------------------------------------------------
# D-3 #7: hidden/reminder messages never reach the extraction LLM
# ---------------------------------------------------------------------------


def test_hidden_dynamic_context_messages_are_skipped():
    """Injected memory (full user memory text, ≤2000 chars) must not be
    formatted as “👤 用户:” input into the recipe-extraction LLM."""
    msgs = [
        {
            "type": "human",
            "content": "<memory>\n用户偏好：喜欢简洁回答\n</memory>",
            "additional_kwargs": {"hide_from_ui": True, "dynamic_context_reminder": True},
        },
        {"type": "human", "content": "帮我生成封面"},
        {"type": "ai", "content": "好的，我来生成"},
    ]
    out = format_messages_for_extraction(msgs)
    assert "👤 用户: 帮我生成封面" in out
    assert "用户偏好" not in out
    assert "<memory>" not in out


def test_hide_from_ui_only_messages_are_skipped():
    """Union-filter half: view-image contexts (hide_from_ui, no reminder
    key) and todo reminders (marker added D-3 #10) are excluded too."""
    msgs = [
        {"type": "human", "content": "继续任务"},
        {
            "type": "human",
            "content": "Your todo list from earlier is no longer visible...",
            "name": "todo_reminder",
            "additional_kwargs": {"hide_from_ui": True},
        },
        {
            "type": "human",
            "content": "[image context data]",
            "additional_kwargs": {"hide_from_ui": True},
        },
        {"type": "ai", "content": "完成"},
    ]
    out = format_messages_for_extraction(msgs)
    assert "todo list" not in out.lower()
    assert "image context" not in out
    assert "👤 用户: 继续任务" in out


def test_visible_human_without_kwargs_unchanged():
    msgs = [{"type": "human", "content": "普通问题", "additional_kwargs": {}}]
    out = format_messages_for_extraction(msgs)
    assert "👤 用户: 普通问题" in out

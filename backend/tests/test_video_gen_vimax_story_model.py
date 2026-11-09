"""Tests for ViMax story model resolution (vimax_llm_model dual-use fix).

``vimax_llm_model`` is a bare API model id (e.g. ``qwen/qwen3.8-max``——2026-10-12
起生产值，经反查命中注册名 ``qwen3.8-max``)
that ViMax's ``build_vimax_config`` passes directly to the Ark API.  But
``create_chat_model`` expects a registered ``models[]`` name.  The
``_resolve_vimax_story_model`` helper tries the value as-is first; on
ValueError it reverse-looks-up the registered name by matching model id +
base_url, avoiding a spurious warning + silent fallback.

复审修订（2026-09-13）：``model_roster_miss`` 降级留痕只发生在真实回退点
（注册名试失败 + 反查无果）——反查命中零留痕；建单侧预检已不对 vimax 留痕
（双用途字段系统性误报，见 test_script_model.py）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.gateway.video_gen.service import _resolve_vimax_story_model

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_model(name: str, model: str, *, api_base: str | None = None, base_url: str | None = None):
    """Build a lightweight stand-in for ModelConfig (extra='allow')."""
    kwargs: dict = {"name": name, "model": model}
    if api_base:
        kwargs["api_base"] = api_base
    if base_url:
        kwargs["base_url"] = base_url
    return SimpleNamespace(**kwargs)


ARK_URL = "https://ark.cn-beijing.volces.com/api/v3"
QIHU_URL = "https://api.360.cn/v1"


# ---------------------------------------------------------------------------
# Case 1: vimax_llm_model is already a registered name → direct success
# ---------------------------------------------------------------------------


def test_registered_name_used_directly():
    """When vimax_llm_model matches a registered name, create_chat_model
    is called once with that name — no ValueError, no warning."""
    fake_model = MagicMock(name="chat_model_instance")

    with patch("deerflow.models.create_chat_model", return_value=fake_model) as mock_ccm:
        result = _resolve_vimax_story_model(
            vimax_llm_model="qwen3.8-max",
            script_model=None,
            vimax_llm_base_url=QIHU_URL,
        )

    assert result is fake_model
    mock_ccm.assert_called_once_with("qwen3.8-max", thinking_enabled=False)


# ---------------------------------------------------------------------------
# Case 2: bare API id that matches a models[] entry → reverse-lookup, no warning
# ---------------------------------------------------------------------------


def test_bare_id_reverse_lookup_no_warning(caplog, tmp_path):
    """vimax_llm_model = 'qwen/qwen3.8-max' is NOT a registered
    name (ValueError), but models[] has an entry with model='qwen/qwen3.8-max'
    and base_url matching vimax_llm_base_url → reverse-lookup succeeds silently."""
    fake_model = MagicMock(name="chat_model_instance")
    models = [
        _make_model("glm-5.3-flash", "z-ai/glm-5.3-flash", base_url=QIHU_URL),
        _make_model("qwen3.8-max", "qwen/qwen3.8-max", base_url=QIHU_URL),
    ]
    fake_app_config = SimpleNamespace(models=models)

    call_log: list[str] = []

    def fake_create_chat_model(name, thinking_enabled=False):
        call_log.append(name)
        if name == "qwen/qwen3.8-max":
            raise ValueError(f"Model '{name}' not found")
        return fake_model

    with (
        patch("deerflow.models.create_chat_model", side_effect=fake_create_chat_model),
        patch("deerflow.config.get_app_config", return_value=fake_app_config),
    ):
        result = _resolve_vimax_story_model(
            vimax_llm_model="qwen/qwen3.8-max",
            script_model=None,
            vimax_llm_base_url=QIHU_URL,
            task_dir=tmp_path,
        )

    assert result is fake_model
    # First call tried the bare id, second call used the resolved name
    assert call_log == ["qwen/qwen3.8-max", "qwen3.8-max"]
    # No warning should be logged
    assert not any("回退默认模型" in r.message for r in caplog.records)
    # 复审修订 2026-09-13：反查命中 = 正常工作路径，零降级留痕
    assert not (tmp_path / "degradations.json").exists()


# ---------------------------------------------------------------------------
# Case 3: bare API id with no match → fallback to default + warning
# ---------------------------------------------------------------------------


def test_bare_id_no_match_fallback_with_warning(caplog, tmp_path):
    """vimax_llm_model doesn't match any models[] entry → warning + fallback
    to create_chat_model(None)."""
    fake_default = MagicMock(name="default_model")
    models = [
        _make_model("glm-5.3-flash", "z-ai/glm-5.3-flash", base_url=QIHU_URL),
    ]
    fake_app_config = SimpleNamespace(models=models)

    call_log: list[str] = []

    def fake_create_chat_model(name, thinking_enabled=False):
        call_log.append(name)
        if name and name not in ("glm-5.3-flash",):
            raise ValueError(f"Model '{name}' not found")
        return fake_default

    with (
        patch("deerflow.models.create_chat_model", side_effect=fake_create_chat_model),
        patch("deerflow.config.get_app_config", return_value=fake_app_config),
    ):
        import logging

        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.service"):
            result = _resolve_vimax_story_model(
                vimax_llm_model="nonexistent-model-12345",
                script_model=None,
                vimax_llm_base_url=ARK_URL,
                task_dir=tmp_path,
            )

    assert result is fake_default
    # Third call is the fallback with None
    assert call_log == ["nonexistent-model-12345", None]
    assert any("回退默认模型" in r.message for r in caplog.records)
    # 复审修订 2026-09-13：真实回退点补 KIND_MODEL_ROSTER_MISS 留痕（建单侧
    # 预检移除后 vimax 撤册可见性的唯一出口）
    import json

    items = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))["items"]
    miss = [i for i in items if i["kind"] == "model_roster_miss"]
    assert len(miss) == 1
    assert "nonexistent-model-12345" in miss[0]["detail"]
    assert "已回退默认模型" in miss[0]["detail"]

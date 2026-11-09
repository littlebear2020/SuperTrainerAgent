"""Tests for automatic thread title generation (TitleConfig + TitleMiddleware behavior)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.title_middleware import TitleMiddleware
from deerflow.config.title_config import TitleConfig, get_title_config, set_title_config


class TestTitleConfig:
    """Tests for TitleConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = TitleConfig()
        assert config.enabled is True
        assert config.max_words == 6
        assert config.max_chars == 60
        assert config.model_name is None

    def test_custom_config(self):
        """Test custom configuration."""
        config = TitleConfig(
            enabled=False,
            max_words=10,
            max_chars=100,
            model_name="gpt-4",
        )
        assert config.enabled is False
        assert config.max_words == 10
        assert config.max_chars == 100
        assert config.model_name == "gpt-4"

    def test_config_validation(self):
        """Test configuration validation."""
        # max_words should be between 1 and 20
        with pytest.raises(ValueError):
            TitleConfig(max_words=0)
        with pytest.raises(ValueError):
            TitleConfig(max_words=21)

        # max_chars should be between 10 and 200
        with pytest.raises(ValueError):
            TitleConfig(max_chars=5)
        with pytest.raises(ValueError):
            TitleConfig(max_chars=201)

    def test_get_set_config(self):
        """Test global config getter and setter."""
        original_config = get_title_config()
        try:
            # Set new config
            new_config = TitleConfig(enabled=False, max_words=10)
            set_title_config(new_config)

            # Verify it was set
            assert get_title_config().enabled is False
            assert get_title_config().max_words == 10
        finally:
            # Restore original config even when an assertion above fails
            set_title_config(original_config)


def _first_exchange_state(**overrides):
    """State with exactly one real user message and one assistant reply."""
    state = {
        "messages": [
            HumanMessage(content="帮我写一份新员工培训计划"),
            AIMessage(content="好的，以下是一份新员工培训计划草案……"),
        ],
    }
    state.update(overrides)
    return state


class TestTitleMiddleware:
    """Behavior tests for TitleMiddleware (sync path never calls an LLM)."""

    def test_middleware_initialization(self):
        """Test middleware can be initialized."""
        middleware = TitleMiddleware()
        assert middleware is not None
        assert middleware.state_schema is not None

    def test_after_model_generates_fallback_title_on_first_exchange(self):
        """First complete exchange → after_model returns a title derived from the user message.

        The sync hook never blocks on an LLM: it returns the local fallback title
        (user message, possibly truncated). The async/LLM path is aafter_model.
        """
        middleware = TitleMiddleware()
        result = middleware.after_model(_first_exchange_state(), runtime=None)
        assert result == {"title": "帮我写一份新员工培训计划"}

    def test_after_model_skips_when_title_already_set(self):
        """A thread that already has a title must not be re-titled."""
        middleware = TitleMiddleware()
        result = middleware.after_model(_first_exchange_state(title="已有标题"), runtime=None)
        assert result is None

    def test_after_model_skips_when_disabled(self):
        """enabled=False → no title generation."""
        original_config = get_title_config()
        try:
            set_title_config(TitleConfig(enabled=False))
            middleware = TitleMiddleware()
            assert middleware.after_model(_first_exchange_state(), runtime=None) is None
        finally:
            set_title_config(original_config)

    def test_after_model_skips_without_assistant_reply(self):
        """No assistant message yet → not a complete exchange → no title."""
        middleware = TitleMiddleware()
        state = {"messages": [HumanMessage(content="你好")]}
        assert middleware.after_model(state, runtime=None) is None

    def test_dynamic_context_reminders_do_not_suppress_title(self):
        """D-3: hidden reminder HumanMessages must not count as user turns —
        otherwise injected threads would permanently suppress title generation."""
        middleware = TitleMiddleware()
        state = {
            "messages": [
                HumanMessage(content="<memory>injected</memory>", additional_kwargs={"dynamic_context_reminder": True, "hide_from_ui": True}),
                HumanMessage(content="帮我写一份新员工培训计划"),
                AIMessage(content="好的……"),
            ],
        }
        result = middleware.after_model(state, runtime=None)
        assert result == {"title": "帮我写一份新员工培训计划"}

    def test_fallback_title_truncates_long_user_message(self):
        """Fallback caps at min(max_chars, 50) chars and appends an ellipsis."""
        middleware = TitleMiddleware()
        long_msg = "很" * 100
        result = middleware.after_model(_first_exchange_state(messages=[HumanMessage(content=long_msg), AIMessage(content="好")]), runtime=None)
        assert result is not None
        assert result["title"] == "很" * 50 + "..."

    def test_parse_title_strips_quotes_and_think_tags(self):
        """Model output normalization: strip think blocks, surrounding quotes, cap length."""
        middleware = TitleMiddleware()
        assert middleware._parse_title('<think>reasoning</think>"我的标题"') == "我的标题"
        assert middleware._parse_title("'另一个标题'") == "另一个标题"

    def test_parse_title_caps_at_max_chars(self):
        middleware = TitleMiddleware()
        parsed = middleware._parse_title("标" * 100)
        assert len(parsed) == get_title_config().max_chars

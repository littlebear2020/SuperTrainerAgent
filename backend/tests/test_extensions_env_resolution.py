"""Tests for environment variable resolution in ExtensionsConfig."""

from deerflow.config.extensions_config import ExtensionsConfig


class TestResolveEnvVariables:
    """Tests for ExtensionsConfig.resolve_env_variables."""

    def test_full_value_replacement(self, monkeypatch):
        """$VAR at start of string replaces the entire value."""
        monkeypatch.setenv("TEST_API_KEY", "secret-123")

        raw = {"key": "$TEST_API_KEY"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["key"] == "secret-123"

    def test_full_value_unresolved_becomes_empty(self, monkeypatch):
        """Unresolved $VAR becomes empty string."""
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)

        raw = {"key": "$NONEXISTENT_VAR"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["key"] == ""

    def test_inline_replacement_bearer_prefix(self, monkeypatch):
        """'Bearer $VAR' is resolved inline."""
        monkeypatch.setenv("TEST_TOKEN", "tok-abc")

        raw = {"Authorization": "Bearer $TEST_TOKEN"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["Authorization"] == "Bearer tok-abc"

    def test_inline_replacement_multiple_vars(self, monkeypatch):
        """Multiple inline $VAR patterns are all resolved."""
        monkeypatch.setenv("VAR_A", "a")
        monkeypatch.setenv("VAR_B", "b")

        raw = {"key": "$VAR_A and $VAR_B"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["key"] == "a and b"

    def test_inline_unresolved_var_becomes_empty(self, monkeypatch):
        """Unresolved inline $VAR is replaced with empty string."""
        monkeypatch.setenv("KNOWN_VAR", "yes")
        monkeypatch.delenv("UNKNOWN_VAR", raising=False)

        raw = {"key": "prefix-$KNOWN_VAR-$UNKNOWN_VAR-suffix"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["key"] == "prefix-yes--suffix"

    def test_no_env_vars_unchanged(self):
        """Strings without $ are passed through unchanged."""
        raw = {"key": "plain value"}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["key"] == "plain value"

    def test_nested_dict_resolution(self, monkeypatch):
        """Env vars in nested dicts are resolved."""
        monkeypatch.setenv("NESTED_KEY", "resolved")

        raw = {
            "headers": {
                "Authorization": "Bearer $NESTED_KEY",
                "X-Custom": "plain",
            },
            "url": "https://example.com",
        }
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["headers"]["Authorization"] == "Bearer resolved"
        assert result["headers"]["X-Custom"] == "plain"
        assert result["url"] == "https://example.com"

    def test_list_with_dicts_resolution(self, monkeypatch):
        """Env vars in list items (dicts) are resolved."""
        monkeypatch.setenv("LIST_VAR", "val")

        raw = {"items": [{"key": "$LIST_VAR"}, {"key": "static"}]}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["items"][0]["key"] == "val"
        assert result["items"][1]["key"] == "static"

    def test_list_with_non_dict_items_preserved(self):
        """Non-dict list items are preserved as-is."""
        raw = {"items": ["string", 42, True]}
        result = ExtensionsConfig.resolve_env_variables(raw)

        assert result["items"] == ["string", 42, True]


class TestExtensionsConfigFromFileEnvResolution:
    """Integration tests: from_file() resolves inline env vars in headers."""

    def test_metaso_header_resolved(self, tmp_path, monkeypatch):
        """Simulates the real metaso config: Bearer $METASO_API_KEY."""
        monkeypatch.setenv("METASO_API_KEY", "mk-test-key-12345")

        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(
            """{
                "mcpServers": {
                    "metaso": {
                        "enabled": true,
                        "type": "http",
                        "url": "https://metaso.cn/api/mcp",
                        "headers": {
                            "Authorization": "Bearer $METASO_API_KEY"
                        }
                    }
                },
                "skills": {}
            }"""
        )

        config = ExtensionsConfig.from_file(str(config_file))
        server = config.mcp_servers["metaso"]

        assert server.headers["Authorization"] == "Bearer mk-test-key-12345"

    def test_env_field_full_value_resolved(self, tmp_path, monkeypatch):
        """$VAR in env values (full-value) is still resolved correctly."""
        monkeypatch.setenv("MY_SECRET", "secret-value")

        config_file = tmp_path / "extensions_config.json"
        config_file.write_text(
            """{
                "mcpServers": {
                    "test": {
                        "enabled": true,
                        "type": "stdio",
                        "command": "npx",
                        "env": {"API_KEY": "$MY_SECRET"}
                    }
                },
                "skills": {}
            }"""
        )

        config = ExtensionsConfig.from_file(str(config_file))
        server = config.mcp_servers["test"]

        assert server.env["API_KEY"] == "secret-value"

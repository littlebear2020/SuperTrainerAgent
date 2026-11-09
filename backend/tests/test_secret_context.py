"""Request-scoped secret carrier 纯函数面（P1-S2，2026-09-30 上游移植）。

移植自上游 tests/test_run_metadata_secret_safety.py 的模块级用例；网关
response 模型接线用例留待 required-secrets 全链路落地（P2/P3）时随链移植。
"""

import pytest

from deerflow.runtime.secret_context import (
    ACTIVE_SECRETS_CONTEXT_KEY,
    LegacyRunMetadataSecretError,
    extract_request_secrets,
    read_active_secrets,
    redact_config_secrets,
    redact_metadata_secrets,
    redact_secret_context_keys,
    validate_run_metadata_secrets,
)
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY


@pytest.mark.parametrize("value", ["secret", "", None, {"nested": True}])
def test_validate_run_metadata_rejects_auth_token_key_by_presence(value):
    with pytest.raises(LegacyRunMetadataSecretError, match=r"config\.context\.secrets"):
        validate_run_metadata_secrets({"auth_token": value, "token_usage": 7})


@pytest.mark.parametrize(
    "metadata",
    [None, "not-a-mapping", {"token": "keep", "nested": {"auth_token": "keep"}}],
)
def test_validate_run_metadata_accepts_non_legacy_shapes(metadata):
    validate_run_metadata_secrets(metadata)


def test_redact_metadata_secrets_removes_exact_key_without_mutating_source():
    source = {
        "auth_token": "legacy-secret",
        "token_usage": 7,
        "nested": {"auth_token": "ordinary-nested-metadata"},
    }

    redacted = redact_metadata_secrets(source)

    assert redacted == {
        "token_usage": 7,
        "nested": {"auth_token": "ordinary-nested-metadata"},
    }
    assert source["auth_token"] == "legacy-secret"
    assert redacted is not source


def test_redact_config_secrets_hides_legacy_config_metadata_without_mutating_source():
    source = {
        "metadata": {
            "auth_token": "legacy-secret",
            "token_usage": 7,
            "nested": {"auth_token": "ordinary-nested-metadata"},
        },
        "context": {
            "secrets": {"MCP_AUTH_TOKEN": "request-secret"},
            "model_name": "default",
        },
    }

    redacted = redact_config_secrets(source)

    assert redacted == {
        "metadata": {
            "token_usage": 7,
            "nested": {"auth_token": "ordinary-nested-metadata"},
        },
        "context": {"model_name": "default"},
    }
    assert source["metadata"]["auth_token"] == "legacy-secret"
    assert source["context"]["secrets"] == {"MCP_AUTH_TOKEN": "request-secret"}
    assert redacted is not source
    assert redacted["metadata"] is not source["metadata"]
    assert redacted["context"] is not source["context"]


def test_redact_config_secrets_drops_trace_id_from_metadata_and_context():
    """``deerflow_trace_id`` is server-issued and ignored as an input; echoing a
    caller-supplied one back would manufacture disagreement."""
    source = {
        "metadata": {DEERFLOW_TRACE_METADATA_KEY: "forged-meta", "token_usage": 7},
        "context": {DEERFLOW_TRACE_METADATA_KEY: "forged-ctx", "model_name": "default"},
    }

    redacted = redact_config_secrets(source)

    assert redacted == {
        "metadata": {"token_usage": 7},
        "context": {"model_name": "default"},
    }
    assert source["metadata"][DEERFLOW_TRACE_METADATA_KEY] == "forged-meta"
    assert source["context"][DEERFLOW_TRACE_METADATA_KEY] == "forged-ctx"


def test_extract_request_secrets_keeps_string_pairs_only():
    context = {
        "secrets": {
            "MCP_AUTH_TOKEN": "v",
            42: "dropped",
            "NON_STR": {"nested": "dropped"},
            "OK2": "v2",
        },
        "other": 1,
    }

    extracted = extract_request_secrets(context)

    assert extracted == {"MCP_AUTH_TOKEN": "v", "OK2": "v2"}
    assert extract_request_secrets(None) == {}
    assert extract_request_secrets("junk") == {}


def test_read_active_secrets_ignores_malformed_carrier():
    assert read_active_secrets({ACTIVE_SECRETS_CONTEXT_KEY: "not-a-dict"}) == {}
    assert read_active_secrets(None) == {}
    assert read_active_secrets({ACTIVE_SECRETS_CONTEXT_KEY: {"A": "b"}}) == {"A": "b"}


def test_redact_secret_context_keys_strips_all_reserved_keys():
    context = {
        "secrets": {"K": "v"},
        "__active_skill_secrets": {"K": "v"},
        "__slash_skill_secret_source": {"path": "p", "owner_token": "t"},
        "__skill_secrets_binding_audit": {"names": ["K"]},
        "__slash_skill_activation_run": "mid-1",
        "__skill_tool_policy_decision": {"allow": True},
        "model_name": "default",
    }

    redacted = redact_secret_context_keys(context)

    assert redacted == {"model_name": "default"}
    assert context["secrets"] == {"K": "v"}  # source not mutated

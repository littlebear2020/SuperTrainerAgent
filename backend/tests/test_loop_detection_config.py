"""Tests for LoopDetectionConfig (Pydantic model + module singleton).

Mirrors the summarization_config.py pattern: a Pydantic BaseModel with a
module-level singleton and a load_X_config_from_dict() loader. NOT a
dataclass with from_dict — AppConfig is a Pydantic BaseModel and the whole
config layer follows this convention.
"""

import pytest

from deerflow.config.loop_detection_config import (
    LoopDetectionConfig,
    get_loop_detection_config,
    load_loop_detection_config_from_dict,
)


@pytest.fixture(autouse=True)
def _reset_loop_config():
    """Reset the module singleton around each test so they don't leak."""
    load_loop_detection_config_from_dict({})
    yield
    load_loop_detection_config_from_dict({})


def test_defaults_match_hardcoded_constants():
    cfg = LoopDetectionConfig()
    assert cfg.enabled is True
    assert cfg.hash_warn_threshold == 3
    assert cfg.hash_hard_limit == 5
    assert cfg.window_size == 20
    assert cfg.tool_freq_warn == 60
    assert cfg.tool_freq_hard_limit == 100
    assert "check_video_status" in cfg.polling_exempt


def test_load_from_dict_applies_overrides_to_singleton():
    load_loop_detection_config_from_dict(
        {
            "enabled": False,
            "hash_hard_limit": 9,
            "polling_exempt": ["my_status_tool"],
        }
    )
    cfg = get_loop_detection_config()
    assert cfg.enabled is False
    assert cfg.hash_hard_limit == 9
    assert cfg.polling_exempt == ["my_status_tool"]


def test_load_from_dict_rejects_invalid_types():
    # Pydantic validates: a non-int threshold must raise (catches typos in config.yaml).
    with pytest.raises(Exception):
        load_loop_detection_config_from_dict({"hash_hard_limit": "not-an-int"})

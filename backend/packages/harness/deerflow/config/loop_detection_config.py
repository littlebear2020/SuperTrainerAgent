"""Configuration for LoopDetectionMiddleware."""

from __future__ import annotations

from pydantic import BaseModel, Field

# Tools whose job is to poll a status with identical arguments (e.g. a video
# render task id). Hash-based loop detection must NOT fire on these — calling
# check_video_status(task_id="vg_xxx") 30 times is correct behaviour, not a loop.
_DEFAULT_POLLING_EXEMPT: list[str] = [
    "check_video_status",
    "check_task_status",
    "check_status",
    "get_video_status",
    "get_task_status",
]


class LoopDetectionConfig(BaseModel):
    """Thresholds for repetitive-tool-call detection.

    Overridable from ``config.yaml`` under ``loop_detection:``. Defaults match
    the previously-hardcoded constants so existing behaviour is unchanged unless
    an operator opts in.
    """

    enabled: bool = Field(default=True, description="Master switch for loop detection.")
    # Layer 1: identical tool-call-set (by hash) within the sliding window.
    hash_warn_threshold: int = Field(default=3, ge=1, description="Inject a warning after N identical tool-call sets.")
    hash_hard_limit: int = Field(default=5, ge=1, description="Force-stop after N identical tool-call sets.")
    window_size: int = Field(default=20, ge=1, description="Sliding window of recent tool calls.")
    # Layer 2: same tool *type* called many times regardless of args.
    tool_freq_warn: int = Field(default=60, ge=1, description="Warn after N calls to the same tool type.")
    tool_freq_hard_limit: int = Field(default=100, ge=1, description="Force-stop after N calls to the same tool type.")
    # Tools exempt from Layer 1 hash detection (status pollers).
    polling_exempt: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_POLLING_EXEMPT),
        description="Tools exempt from hash detection (status pollers).",
    )


# Global configuration instance (mirrors summarization_config.py)
_loop_detection_config: LoopDetectionConfig = LoopDetectionConfig()


def get_loop_detection_config() -> LoopDetectionConfig:
    """Get the current loop-detection configuration."""
    return _loop_detection_config


def set_loop_detection_config(config: LoopDetectionConfig) -> None:
    """Set the loop-detection configuration."""
    global _loop_detection_config
    _loop_detection_config = config


def load_loop_detection_config_from_dict(config_dict: dict) -> None:
    """Load loop-detection configuration from a dictionary (Pydantic-validated)."""
    global _loop_detection_config
    _loop_detection_config = LoopDetectionConfig(**(config_dict or {}))

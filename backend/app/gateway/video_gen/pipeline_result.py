"""Structured pipeline result types for _run_pipeline return values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PipelineCompleted:
    """Pipeline finished — Gateway-complete or Worker handoff payload."""

    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineDraft:
    """Pipeline generated a draft storyboard (phase 1 only)."""

    storyboard: dict[str, Any] = field(default_factory=dict)
    # idea2video (M3.1): idea expanded into a full story; task pauses at STORY_READY
    # for user confirmation. When set, storyboard is empty (no fake narration blueprint).
    story: str | None = None


@dataclass
class PipelineGatewayDone:
    """Pipeline fully completed in Gateway (e.g. remotion render). No Worker handoff."""

    pass


# Union type for _run_pipeline return
PipelineResult = PipelineCompleted | PipelineDraft | PipelineGatewayDone

"""Engine registry (阶段 1.2) — routes video_type → engine instance.

Dependency-injected: vimax needs a config_factory (TrainerAI config → ViMax
config). Callers pass factories so the registry stays free of app.state
coupling and is unit-testable.

The remotion_runner_factory ctor param is retained (2026-07-15 split: remotion
now renders via chat direct-write — course-video agent + auto-video-flow — not
the backend engine layer) so vimax wiring + the vimax registry tests keep their
ctor signature intact. The remotion engine case is gone; get_engine("remotion")
raises UnknownVideoTypeError.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from .base import EngineContext, VideoEngine


class _AgentRunnerFactory(Protocol):
    def __call__(self) -> object: ...  # retained ctor param (remotion path removed)


class _VimaxConfigFactory(Protocol):
    def __call__(self, working_dir: Path, request: dict) -> dict: ...  # → ViMax config dict


class UnknownVideoTypeError(ValueError):
    """Raised when get_engine receives a video_type with no registered engine."""


class EngineRegistry:
    """Routes video_type → engine. Constructed once with runner/vimax factories."""

    def __init__(
        self,
        *,
        remotion_runner_factory: _AgentRunnerFactory,
        vimax_config_factory: _VimaxConfigFactory | None = None,
        vimax_burn_subtitles: bool = True,
    ) -> None:
        self._remotion_runner_factory = remotion_runner_factory
        self._vimax_config_factory = vimax_config_factory
        self._vimax_burn_subtitles = vimax_burn_subtitles

    def get_engine(self, video_type: str, ctx: EngineContext | None = None) -> VideoEngine:
        """Return the engine for video_type.

        ctx is accepted for API compatibility but unused — the registered engine
        (vimax) resolves its dependencies via the factory captured at construction
        time, not per-task. remotion is no longer an engine (renders via chat).
        """
        match video_type:
            case "vimax":
                if self._vimax_config_factory is None:
                    raise ValueError("vimax engine requires a vimax_config_factory")
                from .vimax.audio_overlay import overlay_audio_track
                from .vimax.engine import VimaxEngine

                # Phase 3: inject narration_llm_factory for per-shot narration TTS.
                def _narration_llm_factory() -> Any:
                    from deerflow.models import create_chat_model

                    return create_chat_model(None, thinking_enabled=False)

                # HR-T3: 包装 overlay_fn 注入 burn_subtitles（config 灰度开关）。
                # engine 调 overlay_fn(video, task_dir, audio_override=...)，wrapper 补 burn。
                # 2026-07-29: burn 不再只看全局开关——per-task subtitle_style="none"
                # 必须能否决它，否则「选了无字幕」照样烧默认样式字幕。
                _burn = self._vimax_burn_subtitles

                def _overlay_fn(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
                    from ..subtitle_filter import should_burn_subtitles

                    return overlay_audio_track(
                        video_path,
                        task_dir,
                        audio_override=audio_override,
                        burn_subtitles=should_burn_subtitles(global_burn=_burn, subtitle_style=subtitle_style),
                        subtitle_style=subtitle_style,
                    )

                return VimaxEngine(
                    config_factory=self._vimax_config_factory,
                    overlay_fn=_overlay_fn,
                    narration_llm_factory=_narration_llm_factory,
                )
            case _:
                # remotion renders via chat direct-write (2026-07-15 split); legacy
                # names (cinematic/ai_image/ai_video) map to vimax at composition.
                raise UnknownVideoTypeError(f"unknown video_type: {video_type!r} (registered: vimax)")

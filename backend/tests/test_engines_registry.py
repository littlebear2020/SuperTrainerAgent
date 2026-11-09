"""Tests for engine registry (阶段 1.2) — routes video_type → engine.

Registry is dependency-injected: remotion needs a RemotionAgentRunner
(app.state singletons), vimax needs a config_factory (TrainerAI → ViMax
config). get_engine(video_type, ctx) — ctx is accepted for API
compatibility but unused by the registered engines.
"""

from pathlib import Path

import pytest

from app.gateway.video_gen.engines.registry import EngineRegistry, UnknownVideoTypeError


class _FakeRunner:
    def __init__(self, out: Path) -> None:
        self.out = out

    async def render_via_agent(self, **kw) -> Path:
        return self.out


def test_registry_rejects_remotion(tmp_path):
    """Remotion nested-agent engine removed (2026-07-15 split): get_engine("remotion")
    must raise UnknownVideoTypeError — remotion now renders via chat direct-write
    (course-video agent + auto-video-flow), not the backend engine layer. The
    remotion_runner_factory ctor param is retained (B2b) so vimax wiring/tests stay
    intact, but the remotion case branch is gone."""
    reg = EngineRegistry(remotion_runner_factory=lambda: _FakeRunner(tmp_path / "x"))
    with pytest.raises(UnknownVideoTypeError):
        reg.get_engine("remotion")


def test_registry_unknown_type_raises(tmp_path):
    reg = EngineRegistry(remotion_runner_factory=lambda: _FakeRunner(tmp_path / "x"))
    with pytest.raises(UnknownVideoTypeError):
        reg.get_engine("cinematic")  # v3 renamed cinematic→vimax; legacy name must error


def test_registry_routes_vimax(tmp_path):
    """阶段 2.6: vimax is registered, routing to VimaxEngine with the host config_factory."""
    reg = EngineRegistry(
        remotion_runner_factory=lambda: _FakeRunner(tmp_path / "x"),
        vimax_config_factory=lambda wd, req: {"working_dir": str(wd)},
    )
    engine = reg.get_engine("vimax")
    assert engine.name == "vimax"


def test_registry_vimax_requires_config_factory(tmp_path):
    """vimax without a config_factory is a wiring error, not a silent default."""
    reg = EngineRegistry(remotion_runner_factory=lambda: _FakeRunner(tmp_path / "x"))
    with pytest.raises((ValueError, UnknownVideoTypeError)):
        reg.get_engine("vimax")
